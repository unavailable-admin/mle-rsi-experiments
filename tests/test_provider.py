from types import SimpleNamespace
import httpx
import pytest
from mle_rsi.common import read, LimitReached
from mle_rsi.provider import GeminiTransport
from test_experiment import spec

@pytest.mark.asyncio
async def test_provider_billing_cache_and_rejected_model(tmp_path, monkeypatch):
    s=spec();monkeypatch.setenv(s['model']['api_key_env'],'test-key')
    config=SimpleNamespace(api_key_env=s['model']['api_key_env'],model=s['model']['name'],max_output_tokens=128)
    calls=[]
    def handler(request):
        calls.append(request.method)
        if request.method=='GET':return httpx.Response(200,json={'amount':{'currency':'USD','value':'.001'}})
        return httpx.Response(200,headers={'x-request-id':'test-request'},json={
            'modelVersion':config.model,'candidates':[{'finishReason':'STOP','content':{'parts':[{'text':'ok'}]}}],
            'usageMetadata':{'totalTokenCount':10}})
    original=httpx.AsyncClient
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kw:original(transport=httpx.MockTransport(handler),**kw))
    t=GeminiTransport(tmp_path,s,'leaf-classification','development','dev-leaf')
    args=(config,[{'role':'user','content':'test'}],'one','discovery')
    assert (await t(*args))['usage']['cost_usd']==.001
    assert (await t(*args))['choices'][0]['message']['content']=='ok'
    assert calls==['POST','GET']
    with pytest.raises(RuntimeError):await t(config,[{'role':'user','content':'changed'}],'one','discovery')

@pytest.mark.asyncio
async def test_provider_uncertain_request_never_retries(tmp_path,monkeypatch):
    s=spec();monkeypatch.setenv(s['model']['api_key_env'],'test-key')
    config=SimpleNamespace(api_key_env=s['model']['api_key_env'],model=s['model']['name'],max_output_tokens=128)
    calls=[]
    def handler(request):
        calls.append(request.method)
        raise httpx.ReadTimeout('test',request=request)
    original=httpx.AsyncClient
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kw:original(transport=httpx.MockTransport(handler),**kw))
    t=GeminiTransport(tmp_path,s,'leaf-classification','development','dev-leaf')
    args=(config,[{'role':'user','content':'test'}],'one','discovery')
    with pytest.raises(httpx.ReadTimeout):await t(*args)
    with pytest.raises(LimitReached):await t(*args)
    assert calls==['POST']
    assert read(tmp_path/'budget.json')['api']['dev-leaf:one']['state']=='uncertain'
