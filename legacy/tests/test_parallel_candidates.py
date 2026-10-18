import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
import httpx
import pytest
from mle_rsi import REPO
from mle_rsi.common import Budget,LimitReached,read,write
from mle_rsi.backend import MLEBackend
from mle_rsi.gpt_provider import GPTTransport
from mle_rsi.single_task import search,validate


def spec():return read(REPO/'configs/spooky-solver-500.json')


def test_config_requires_five_disjoint_cpu_lanes():
    s=spec();assert validate(s)==500
    assert s['execution']['candidate_cpu_groups']==[list(range(n,n+8)) for n in range(0,40,8)]
    s['execution']['candidate_cpu_groups'][1][0]=0
    with pytest.raises(ValueError,match='disjoint'):validate(s)


def test_execution_reservation_is_atomic_and_counts_inflight(tmp_path):
    s=spec()['budget'];s.update(execution_seconds_per_task=350,development_execution_seconds_per_task=350)
    budget=Budget(tmp_path,s)
    def reserve(i):
        try:return budget.reserve_execution(str(i),'task','development',100,False)
        except LimitReached:return None
    with ThreadPoolExecutor(max_workers=5) as pool:results=list(pool.map(reserve,range(5)))
    ledger=read(tmp_path/'budget.json')['execution']
    assert sum(r['wall_seconds'] for r in ledger.values())==350
    assert results.count(None)==1
    first=next(iter(ledger));budget.finish_execution(first,10,0)
    assert budget.reserve_execution('new','task','development',100,False)>0
    with pytest.raises(LimitReached,match='already used'):budget.reserve_execution(first,'task','development',100,False)


@pytest.mark.asyncio
async def test_backend_really_runs_five_and_recycles_lane_after_failure(tmp_path,monkeypatch):
    s=spec();backend=MLEBackend('spooky-author-identification',tmp_path,s,'development',mock=True)
    active=set();peak=0;seen=[]
    async def evaluate(worker,artifact,batch):
        nonlocal peak
        lane=tuple(worker.spec['execution']['cpu_ids'])
        assert not active.intersection(lane)
        active.update(lane);seen.append(lane);peak=max(peak,len(active)//8)
        try:
            await asyncio.sleep(.03)
            if artifact==2:raise ValueError('candidate failure')
            return artifact
        finally:active.difference_update(lane)
    monkeypatch.setattr(MLEBackend,'_evaluate',evaluate)
    rows=await asyncio.gather(*(backend.evaluate(i,{}) for i in range(10)),return_exceptions=True)
    assert peak==5 and len(set(seen))==5 and backend.cpu_slots.qsize()==5
    assert len(s['execution']['cpu_ids'])==40
    assert isinstance(rows[2],ValueError) and rows[-1]==9


@pytest.mark.asyncio
async def test_api_five_inflight_and_shared_budget(tmp_path,monkeypatch):
    s=spec();monkeypatch.setenv(s['model']['api_key_env'],'test-only')
    active=0;peak=0;posts=0
    async def handler(request):
        nonlocal active,peak,posts
        if request.method=='GET':return httpx.Response(200,json={'amount':{'currency':'USD','value':'.01'}})
        posts+=1;ident=str(posts);active+=1;peak=max(peak,active)
        try:await asyncio.sleep(.03)
        finally:active-=1
        return httpx.Response(200,headers={'x-request-id':ident},json={'model':s['model']['name'],
            'choices':[{'finish_reason':'stop','message':{'content':'done'}}],'usage':{'total_tokens':10}})
    original=httpx.AsyncClient
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kw:original(transport=httpx.MockTransport(handler),**kw))
    transport=GPTTransport(tmp_path,s,'spooky','development','c')
    cfg=SimpleNamespace(model=s['model']['name'],api_key_env=s['model']['api_key_env'],reasoning_effort='medium',max_output_tokens=16384)
    await asyncio.gather(*(transport(cfg,[{'role':'user','content':'test'}],str(i),'discovery') for i in range(10)))
    assert peak==5 and posts==10
    ledger=read(tmp_path/'budget.json')['api'];assert len(ledger)==10
    assert all(r['state']=='completed' for r in ledger.values())
    assert sum(r['charged_usd'] for r in ledger.values())==pytest.approx(.1)


@pytest.mark.asyncio
async def test_restart_with_uncertain_execution_stops_before_campaign(tmp_path,monkeypatch):
    import mle_rsi.single_task as entry
    monkeypatch.setattr(entry,'frozen',lambda root:{'spec':spec(),'mode':'mock'})
    monkeypatch.setattr(entry,'campaign',lambda *a,**k:pytest.fail('Must not start campaign'))
    write(tmp_path/'budget.json',{'execution':{'old':{'state':'running'}},'api':{}})
    with pytest.raises(RuntimeError,match='reconcile'):await search(tmp_path)
