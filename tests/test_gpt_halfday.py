from types import SimpleNamespace
import json
import time

import httpx
import pytest

from mle_rsi import REPO
from mle_rsi.common import Budget, LimitReached, read, write
from mle_rsi.experiment import campaign, initialize, run_task
from mle_rsi.gpt_provider import GPTTransport
from mle_rsi.supervisor import phase_deadline


def spec():
    return read(REPO/'configs/gpt-halfday-dev4-lite22.json')


def config(s, model='gpt-6-sol'):
    return SimpleNamespace(api_key_env=s['model']['api_key_env'], model=model,
                           reasoning_effort='medium', max_output_tokens=16384)


@pytest.mark.asyncio
async def test_gpt_wire_billing_cache_and_input_drift(tmp_path, monkeypatch):
    s=spec();cfg=config(s);monkeypatch.setenv(cfg.api_key_env,'test-key')
    calls=[]
    def handler(request):
        calls.append(request.method)
        if request.method=='GET':return httpx.Response(200,json={'amount':{'currency':'USD','value':'.01'}})
        payload=json.loads(request.content)
        assert payload['reasoning_effort']=='medium' and payload['max_completion_tokens']==16384
        assert 'temperature' not in payload and not payload['stream']
        return httpx.Response(200,headers={'x-request-id':'gpt-test'},json={'model':cfg.model,
            'choices':[{'finish_reason':'stop','message':{'content':'complete'}}],'usage':{'total_tokens':500}})
    original=httpx.AsyncClient
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kw:original(transport=httpx.MockTransport(handler),**kw))
    t=GPTTransport(tmp_path,s,'leaf-classification','development','dev-leaf')
    args=(cfg,[{'role':'user','content':'test'}],'one','discovery')
    assert (await t(*args))['usage']['cost_usd']==.01
    assert (await t(*args))['choices'][0]['message']['content']=='complete'
    assert calls==['POST','GET']
    with pytest.raises(RuntimeError,match='different input'):
        await t(cfg,[{'role':'user','content':'changed'}],'one','discovery')


@pytest.mark.asyncio
@pytest.mark.parametrize('failure',['timeout','length','wrong_model'])
async def test_gpt_failures_never_resend_and_rejected_outputs_are_billed(tmp_path,monkeypatch,failure):
    s=spec();cfg=config(s);monkeypatch.setenv(cfg.api_key_env,'test-key');calls=[]
    def handler(request):
        calls.append(request.method)
        if request.method=='GET':return httpx.Response(200,json={'amount':{'currency':'USD','value':'.02'}})
        if failure=='timeout':raise httpx.ReadTimeout('lost response',request=request)
        return httpx.Response(200,headers={'x-request-id':'gpt-test'},json={
            'model':'different' if failure=='wrong_model' else cfg.model,
            'choices':[{'finish_reason':'length' if failure=='length' else 'stop','message':{'content':'partial'}}]})
    original=httpx.AsyncClient
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kw:original(transport=httpx.MockTransport(handler),**kw))
    t=GPTTransport(tmp_path,s,'leaf-classification','development','dev-leaf')
    args=(cfg,[{'role':'user','content':'test'}],'one','discovery')
    with pytest.raises((httpx.ReadTimeout,RuntimeError)):await t(*args)
    with pytest.raises(LimitReached):await t(*args)
    assert calls.count('POST')==1
    row=read(tmp_path/'budget.json')['api']['dev-leaf:one']
    if failure=='timeout':assert row['state']=='uncertain' and 'charged_usd' not in row
    else:assert row['state']=='rejected' and row['charged_usd']==.02


def test_global_budget_counts_other_tasks_and_uncertain_reservations(tmp_path):
    s=spec()['budget'];s.update(api_usd_total=1,api_usd_per_task=5)
    b=Budget(tmp_path,s);b.reserve_api('a','leaf','development',.7,'h');b.update_api('a',state='uncertain')
    with pytest.raises(LimitReached,match='Experiment API ceiling'):
        b.reserve_api('b','taxi','final',.31,'h')
    b.reserve_api('b','taxi','final',.3,'h')
    b.update_api('b',state='completed',charged_usd=.1)
    b.reserve_api('c','spooky','development',.1,'h')


def test_role_models_and_32_node_budget(tmp_path):
    root=initialize(tmp_path/'run',spec(),mode='mock');c=campaign(root,'leaf-classification','development')
    try:
        assert c.config.outer_rounds==4 and c.config.round_discovery_calls==8
        assert c.config.developer_model.reasoning_effort=='high'
        assert c.config.discovery_model.reasoning_effort=='medium'
        assert c.config.budget.max_discovery_calls>=32
    finally:c.store.close()


@pytest.mark.asyncio
async def test_expired_phase_stops_without_candidates_and_deadline_does_not_reset(tmp_path):
    root=initialize(tmp_path/'run',spec(),mode='mock')
    expired=time.time()-1
    write(root/'phase-timing.json',{'development':{'started_at':expired-10,'deadline':expired}})
    assert phase_deadline(root,'development',100,time.time()+100)==expired
    result=await run_task(root,'leaf-classification','development')
    assert result['state']=='stopped' and result['completed_rounds']==0
    assert result['candidate_nodes']==0 and result['selected'] is None
