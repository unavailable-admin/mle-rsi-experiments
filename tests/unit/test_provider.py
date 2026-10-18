import asyncio
import httpx
import pytest
from mle_rsi.provider import ModelService
from mle_rsi.ledger import Ledger
from mle_rsi.io import write
from dream_rsi.config import ModelConfig


@pytest.mark.asyncio
async def test_billing_recovery_uses_existing_response(tmp_path,monkeypatch):
    monkeypatch.setenv('TEST_MODEL_KEY','fixture')
    requests=[]
    def handler(request):
        requests.append(request.method)
        return httpx.Response(200,json={'amount':{'currency':'USD','value':.02}})
    transport=httpx.MockTransport(handler)
    ledger=Ledger(tmp_path,{})
    settings={'model':{'base_url':'https://fixture.invalid','model':'fixture','api_key_env':'TEST_MODEL_KEY'},
              'reservation_rates':{'input_usd_per_million':1,'output_usd_per_million':1},'billing_delays':[0]}
    service=ModelService(tmp_path,'task',ledger,settings,asyncio.Semaphore(1),
        client_factory=lambda **kw:httpx.AsyncClient(transport=transport,**kw))
    ledger.reserve('id','task','hash',{'api_usd':1});ledger.record('id','running')
    directory=service.directory('id');write(directory/'sending.json',{})
    write(directory/'response.json',{'status':200,'provider_id':'p','body':{'model':'fixture',
          'choices':[{'finish_reason':'stop','message':{'content':'source'}}],'usage':{'total_tokens':4}}})
    try:
        result=await service.lookup('id','hash')
        assert result['status']=='completed' and result['text']=='source'
        assert requests==['GET']
        assert ledger.summary()['totals']['api_usd']==.02
        assert await service.lookup('id','hash')==result
        assert requests==['GET']
    finally:ledger.close()


@pytest.mark.asyncio
async def test_504_skip_preserves_unknown_fee(tmp_path,monkeypatch):
    monkeypatch.setenv('TEST_MODEL_KEY','fixture')
    transport=httpx.MockTransport(lambda r:httpx.Response(504,text='gateway'))
    ledger=Ledger(tmp_path,{})
    settings={'model':{'base_url':'https://fixture.invalid','model':'fixture','api_key_env':'TEST_MODEL_KEY'},
        'continue_on_504':True,'reservation_rates':{'input_usd_per_million':1,'output_usd_per_million':1}}
    service=ModelService(tmp_path,'task',ledger,settings,asyncio.Semaphore(1),
        client_factory=lambda **kw:httpx.AsyncClient(transport=transport,**kw))
    try:
        result=await service.generate(ModelConfig(**settings['model']),[{'role':'user','content':'x'}],'id','discovery','hash')
        assert result['status']=='skipped'
        assert ledger.get('id')['amounts']['api_usd']>0
        assert await service.lookup('id','hash')==result
    finally:ledger.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('error_type,expected_posts', [(httpx.ConnectTimeout, 2), (httpx.ReadTimeout, 1)])
async def test_connection_retry_and_unknown_no_resend(tmp_path, monkeypatch, error_type, expected_posts):
    from dream_rsi.llm import LLMClient
    from dream_rsi.storage import CampaignStore, UsageLedger
    from dream_rsi.errors import ExternalUnknown
    monkeypatch.setenv('TEST_MODEL_KEY', 'fixture')
    posts = []
    def handler(request):
        if request.method == 'GET':
            return httpx.Response(200, json={'amount': {'currency': 'USD', 'value': .02}})
        posts.append(request)
        if len(posts) == 1:
            raise error_type('') from OSError('fixture connection failure')
        return httpx.Response(200, headers={'x-request-id': 'p'}, json={
            'model': 'fixture', 'choices': [{'finish_reason': 'stop', 'message': {'content': 'source'}}],
            'usage': {'total_tokens': 4}})
    settings = {'model': {'base_url': 'https://fixture.invalid', 'model': 'fixture',
                         'api_key_env': 'TEST_MODEL_KEY', 'provider': 'openai-compatible',
                         'timeout_attempts': 3, 'timeout_retry_delay_s': 0},
                'reservation_rates': {'input_usd_per_million': 1, 'output_usd_per_million': 1},
                'billing_delays': [0]}
    ledger = Ledger(tmp_path, {})
    store = CampaignStore(tmp_path/'store')
    service = ModelService(tmp_path, 'task', ledger, settings, asyncio.Semaphore(1),
        client_factory=lambda **kw: httpx.AsyncClient(transport=httpx.MockTransport(handler), **kw))
    client = LLMClient(ModelConfig(**settings['model']), UsageLedger(store), transport=service)
    try:
        if error_type is httpx.ConnectTimeout:
            result, _ = await client.generate([{'role': 'user', 'content': 'x'}], 'id')
            assert result == 'source'
            assert ledger.summary()['totals']['api_usd'] == .02
        else:
            for _ in range(2):
                with pytest.raises(ExternalUnknown):
                    await client.generate([{'role': 'user', 'content': 'x'}], 'id')
        assert len(posts) == expected_posts
        failed = next(row for row in ledger.rows() if row['state'] in ('not_sent', 'unknown'))
        assert failed['evidence']['error_type'] == error_type.__name__
        assert failed['evidence']['cause_type'] == 'OSError'
        assert failed['evidence']['stage'] == 'request'
        assert error_type.__name__ in failed['evidence']['error']
        assert failed['amounts']['api_usd'] == 0 if error_type is httpx.ConnectTimeout else failed['amounts']['api_usd'] > 0
    finally:
        store.close()
        ledger.close()
