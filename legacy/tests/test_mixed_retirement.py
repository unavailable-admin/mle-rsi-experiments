import asyncio
import pytest
from mle_rsi.adaptive_resources import MixedResourcePool
from mle_rsi.common import write,read,sha
from mle_rsi.medal_retirement import retire_winners


def settings():
    return dict(cpu_groups=[list(range(i,i+4)) for i in range(0,64,4)],gpu_pool=list(range(8)),
        mixed_resources={'max_slots':16},gpu_cpu_affinity={str(i):list(range(0,32) if i<4 else range(32,64)) for i in range(8)})


@pytest.mark.asyncio
async def test_mixed_twelve_tasks_fill_all_cores_with_eight_gpu(tmp_path):
    pool=MixedResourcePool(tmp_path,settings(),True)
    used=set(); devices=set(); ready=asyncio.Event(); counts=[]
    async def worker(i):
        async with pool.slot(str(i),i<8) as slot:
            assert not used.intersection(slot['cpus'])
            used.update(slot['cpus']); counts.append(len(slot['cpus']))
            if i<8:
                assert slot['gpu'] not in devices
                devices.add(slot['gpu'])
                assert len(slot['cpus'])==4
            else: assert len(slot['cpus'])==8
            if len(used)==64: ready.set()
            await asyncio.wait_for(ready.wait(),2)
            await asyncio.sleep(.03)
            used.difference_update(slot['cpus'])
    await asyncio.gather(*(worker(i) for i in range(12)))
    assert sorted(counts)==[4]*8+[8]*4
    assert len(pool.free)==16 and not pool.active and not pool.waiters


@pytest.mark.asyncio
async def test_cancellation_returns_gpu_and_cpu(tmp_path):
    pool=MixedResourcePool(tmp_path,settings(),True)
    entered=asyncio.Event()
    async def worker():
        async with pool.slot('a',True):
            entered.set(); await asyncio.sleep(20)
    task=asyncio.create_task(worker()); await entered.wait();task.cancel()
    with pytest.raises(asyncio.CancelledError):await task
    assert len(pool.free)==16 and not pool.mock_used and not pool.active


def checkpoint(root,task,number,medal=True,state='graded'):
    folder=root/'rounds'/f'{number:04d}'/task
    for name in ('selected.json','solver.py','submission.csv'):write(folder/name,{})
    write(folder/'seal.json',{'artifacts':{n:sha(folder/n) for n in ('selected.json','solver.py','submission.csv')}})
    write(folder/'official-result.json',dict(state=state,any_medal=medal,valid_submission=True))
    return folder


def test_retire_real_medals_only_and_preserve_evidence(tmp_path):
    spec={'experiment_tasks':['a','b','c'],'round_robin':{'retire_on_medal':{'from_round':4}}}
    checkpoint(tmp_path,'a',3)
    checkpoint(tmp_path,'b',4,False)
    checkpoint(tmp_path,'c',4,True,'verification_only')
    assert retire_winners(tmp_path,spec,'real')=={}
    folder=checkpoint(tmp_path,'a',4)
    assert retire_winners(tmp_path,spec,'mock')=={}
    saved=retire_winners(tmp_path,spec,'real'); assert list(saved)==['a']
    assert retire_winners(tmp_path,spec,'real')==saved
    checkpoint(tmp_path,'b',5)
    assert set(retire_winners(tmp_path,spec,'real'))=={'a','b'}
    (folder/'submission.csv').write_text('changed')
    with pytest.raises(RuntimeError,match='changed'):retire_winners(tmp_path,spec,'real')

@pytest.mark.asyncio
async def test_migration_continues_nodes_without_reset(tmp_path):
    from mle_rsi import REPO
    from mle_rsi.experiment import initialize
    from mle_rsi.rounds import run
    from mle_rsi.round_dashboard import report
    from mle_rsi.migrate_rounds import migrate
    s=read(REPO/'configs/lite22-rounds-half.json')
    s['development_tasks']=s['experiment_tasks']=['leaf-classification']
    source=tmp_path/'parent';initialize(source,s,'mock')
    await run(source,through_round=1)
    destination=migrate(source,tmp_path/'next',1)
    assert report(destination)['rows'][0]['candidates']==25
    write(destination/'control.json',{'action':'resume'})
    await run(destination,through_round=2)
    assert report(destination)['rows'][0]['candidates']==50
    assert report(source)['rows'][0]['candidates']==25
    await run(destination,through_round=2)
    assert report(destination)['rows'][0]['candidates']==50

@pytest.mark.asyncio
async def test_round_winners_exit_next_wave_and_all_medals_stop(tmp_path,monkeypatch):
    from mle_rsi import REPO
    from mle_rsi.experiment import initialize
    from mle_rsi.rounds import run
    from mle_rsi.round_dashboard import report
    import mle_rsi.rounds as rounds
    import mle_rsi.medal_retirement as retirement
    original=retirement.retire_winners
    monkeypatch.setattr(retirement,'retire_winners',lambda root,spec,mode:original(root,spec,'real'))
    def grade(root,task,result,output_dir=None,cpu_ids=None):
        number=int(output_dir.parent.name)
        medal=task=='leaf-classification' or number==2
        # Mock has no submission file; provide an explicit test-only sealed fixture.
        checkpoint(root,task,number,medal)
        return read(output_dir/'official-result.json')
    monkeypatch.setattr(rounds,'grade_selected',grade)
    s=read(REPO/'configs/lite22-rounds-half.json')
    s['development_tasks']=s['experiment_tasks']=['leaf-classification','spooky-author-identification']
    s['round_robin']['retire_on_medal']={'from_round':1}
    root=tmp_path/'run';initialize(root,s,'mock')
    await run(root,through_round=3)
    d=report(root)
    assert d['state']['state']=='all_medaled'
    assert [r['candidates'] for r in d['rows']]==[25,50]
    assert d['retired_count']==2
    assert d['rounds'][-1]['complete'] and d['rounds'][-1]['cumulative_medals']==2
    await run(root,through_round=3)
    assert [r['candidates'] for r in report(root)['rows']]==[25,50]


def test_prior_medal_survives_later_regression_without_double_counting(tmp_path):
    spec={'experiment_tasks':['a'],'round_robin':{'retire_on_medal':{'from_round':1,'activation_round':4}}}
    checkpoint(tmp_path,'a',1,True)
    checkpoint(tmp_path,'a',4,False)
    saved=retire_winners(tmp_path,spec,'real')
    assert saved['a']['round']==1
    assert saved['a']['activation_round']==4
    assert not (saved['a']['activation_round']<4)
    assert saved['a']['activation_round']<5


@pytest.mark.asyncio
async def test_aged_eight_core_request_accumulates_blocks(tmp_path):
    import time
    config=settings();config['cpu_groups']=[[0,1,2,3],[4,5,6,7]]
    config['mixed_resources']['task_cpu_cores']={'holder':4}
    pool=MixedResourcePool(tmp_path,config,True)
    release=asyncio.Event();held=asyncio.Event();order=[]
    async def holder():
        async with pool.slot('holder',False):
            held.set();await release.wait()
    async def work(task,gpu):
        async with pool.slot(task,gpu):
            order.append(task);await asyncio.sleep(.02)
    a=asyncio.create_task(holder());await held.wait()
    b=asyncio.create_task(work('aged',False));await asyncio.sleep(.02)
    pool.waiters[0]['queued_at']=time.time()-601
    c=asyncio.create_task(work('new_gpu',True));await asyncio.sleep(.03)
    assert order==[]
    release.set();await asyncio.wait_for(asyncio.gather(a,b,c),2)
    assert order==['aged','new_gpu']
