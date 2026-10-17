import asyncio
from pathlib import Path
import pytest
from mle_rsi import REPO
from mle_rsi.common import read, write
from mle_rsi.round_resources import ResourcePool
from mle_rsi.rounds import validate, control, run, verify_seal
from mle_rsi.round_dashboard import report, PAGE
from mle_rsi.experiment import initialize


def config():
    return read(REPO/'configs/lite22-rounds-half.json')


def test_durable_dream_skip_allows_resume_without_releasing_reservation(tmp_path):
    from dream_rsi.storage import CampaignStore
    from mle_rsi.rounds import unresolved
    task = 'leaf-classification'
    key = f'dev-{task}:dream:r0005-v0001:response:0'
    row = dict(task=task, state='uncertain', reserved_usd=.45)
    budget = {'api': {key: row}, 'execution': {}}
    write(tmp_path/'budget.json', budget)
    assert unresolved(tmp_path, task)
    store = CampaignStore(tmp_path/'campaigns'/f'dev-{task}')
    store.put('model_request_skip', 'dream:r0005-v0002', 'different revision')
    assert unresolved(tmp_path, task)
    store.put('model_request_skip', 'dream:r0005-v0001', 'Explicitly skipped after timeout')
    assert not unresolved(tmp_path, task)
    assert read(tmp_path/'budget.json') == budget
    row['state'] = 'pending'
    write(tmp_path/'budget.json', budget)
    assert unresolved(tmp_path, task)
    row['state'] = 'uncertain'
    budget['execution']['job'] = dict(task=task, state='running')
    write(tmp_path/'budget.json', budget)
    assert unresolved(tmp_path, task)
    store.close()


def test_half_machine_config_and_no_fixed_search_target():
    s = config()
    assert len(validate(s)) == 22
    assert s['execution']['cpu_ids'] == list(range(32))
    assert s['execution']['gpu_pool'] == [0,1,2,3]
    assert s['budget']['api_usd_total'] == 150
    s['execution']['cpu_groups'][1][0] = 0
    with pytest.raises(ValueError, match='disjoint'):
        validate(s)


@pytest.mark.asyncio
async def test_shared_slots_across_tasks_and_gpu_exclusion(tmp_path):
    pool = ResourcePool(tmp_path,[list(range(i,i+8)) for i in range(0,32,8)],[0,1],mock=True)
    active = set(); gpu_active = set(); peak = 0
    async def work(i):
        nonlocal peak
        async with pool.slot('task-'+str(i%5), i%2==0) as slot:
            assert not active.intersection(slot['cpus'])
            active.update(slot['cpus'])
            peak = max(peak,len(active)//8)
            if slot['gpu'] is not None:
                assert slot['gpu'] not in gpu_active
                gpu_active.add(slot['gpu'])
            await asyncio.sleep(.03)
            active.difference_update(slot['cpus'])
            if slot['gpu'] is not None: gpu_active.remove(slot['gpu'])
    await asyncio.wait_for(asyncio.gather(*(work(i) for i in range(20))),10)
    assert peak == 4 and len(pool.free)==4 and not pool.waiters and not pool.active


@pytest.mark.asyncio
async def test_gpu_waiter_does_not_hold_cpu_and_cancel_cleans_queue(tmp_path):
    pool=ResourcePool(tmp_path,[[0,1]],[],mock=True)
    async def gpu():
        async with pool.slot('gpu',True):
            pytest.fail('No GPU exists')
    task=asyncio.create_task(gpu())
    await asyncio.sleep(.02)
    async with pool.slot('cpu',False) as slot:
        assert slot['cpus']==(0,1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError): await task
    assert not pool.waiters and len(pool.free)==1


@pytest.mark.asyncio
async def test_two_task_rounds_resume_and_private_reporting_isolation(tmp_path):
    root=tmp_path/'run'
    s=config(); s['development_tasks']=s['experiment_tasks']=['leaf-classification','spooky-author-identification']
    initialize(root,s,'mock')
    await run(root,through_round=1)
    first=report(root)
    assert [r['candidates'] for r in first['rows']]==[25,25]
    assert all(r['completed_rounds']==1 for r in first['rows'])
    assert first['rounds'][0]['verification_only']
    assert first['rounds'][0]['evaluated']==0
    # Idempotent same-target resume; no extra candidates or new API calls.
    await run(root,through_round=1)
    assert [r['candidates'] for r in report(root)['rows']]==[25,25]
    # Existing private report artifacts must not prevent public-history continuation.
    await run(root,through_round=2)
    second=report(root)
    assert [r['candidates'] for r in second['rows']]==[50,50]
    assert len(second['rounds'])==2
    for task in s['experiment_tasks']:
        out=root/'rounds/0001'/task
        verify_seal(out)
        assert read(out/'seal.json')['private_used_for_selection'] is False
    out=root/'rounds/0001'/s['experiment_tasks'][0]
    (out/'selected.json').write_text('{}')
    with pytest.raises(RuntimeError,match='changed'): verify_seal(out)


def test_control_and_dashboard_fixed_denominator(tmp_path):
    s=config(); root=tmp_path/'run'; initialize(root,s,'mock')
    assert control(root,'pause')['action']=='pause'
    assert report(root)['control']['action']=='pause'
    assert len(report(root)['rows'])==22
    with pytest.raises(ValueError):control(root,'delete')
    assert 'X-Control-Token' in PAGE and '分母始终' in PAGE


@pytest.mark.asyncio
async def test_pause_drains_batch_and_resume_keeps_exact_candidate_count(tmp_path,monkeypatch):
    from mle_rsi.backend import MLEBackend
    root=tmp_path/'run'; s=config()
    s['development_tasks']=s['experiment_tasks']=['leaf-classification']
    s['round_robin']['active_batches']=1
    initialize(root,s,'mock')
    original=MLEBackend._evaluate
    paused=False
    async def evaluate(self,artifact,batch):
        nonlocal paused
        result=await original(self,artifact,batch)
        if not paused:
            paused=True
            control(root,'pause')
        return result
    monkeypatch.setattr(MLEBackend,'_evaluate',evaluate)
    runner=asyncio.create_task(run(root,through_round=1))
    try:
        for _ in range(200):
            await asyncio.sleep(.1)
            p=root/'round-status.json'
            if p.exists() and read(p)['state']=='paused':break
        else:pytest.fail('Pause did not reach a durable boundary')
        data=report(root)
        assert data['rows'][0]['candidates']==5
        assert data['rows'][0]['completed_rounds']==0
        assert not data['scheduler']['active']
        control(root,'resume')
        await asyncio.wait_for(runner,30)
        assert report(root)['rows'][0]['candidates']==25
        assert report(root)['rows'][0]['state']=='round_complete'
    finally:
        if not runner.done():
            runner.cancel()
            await asyncio.gather(runner,return_exceptions=True)


def test_uncertain_api_blocks_retry(tmp_path):
    from mle_rsi.rounds import unresolved
    write(tmp_path/'budget.json',{'api':{'x':{'task':'leaf','state':'uncertain'}},'execution':{}})
    assert unresolved(tmp_path,'leaf')


@pytest.mark.asyncio
async def test_cleanup_grace_is_scoped_to_round_controller(tmp_path,monkeypatch):
    from dream_rsi import sandbox
    from mle_rsi.backend import MLEBackend
    before=sandbox.PROCESS_CLEANUP_TIMEOUT_S
    root=tmp_path/'run';s=config();s['development_tasks']=s['experiment_tasks']=['leaf-classification']
    initialize(root,s,'mock')
    original=MLEBackend._evaluate
    async def evaluate(self,artifact,batch):
        assert sandbox.PROCESS_CLEANUP_TIMEOUT_S==30
        return await original(self,artifact,batch)
    monkeypatch.setattr(MLEBackend,'_evaluate',evaluate)
    await run(root,through_round=1)
    assert sandbox.PROCESS_CLEANUP_TIMEOUT_S==before


def test_dashboard_reuses_readonly_stores_without_stale_control(tmp_path):
    root=tmp_path/'run';initialize(root,config(),'mock')
    stores={}
    control(root,'pause')
    assert report(root,stores)['control']['action']=='pause'
    control(root,'resume')
    assert report(root,stores)['control']['action']=='resume'


@pytest.mark.asyncio
async def test_independent_rounds_unequal_starts_slow_task_and_failure(tmp_path, monkeypatch):
    """R7 must advance twice while R11 is still running; R15 failure is isolated."""
    from types import SimpleNamespace
    from mle_rsi import rounds
    tasks = ['dog-breed-identification', 'aerial-cactus-identification', 'new-york-city-taxi-fare-prediction']
    root = tmp_path/'run'; s = config()
    s['development_tasks'] = s['experiment_tasks'] = tasks
    initialize(root, s, 'mock')
    starts = dict(zip(tasks, [7, 11, 15]))
    calls = []; active = set(); fast_advanced = asyncio.Event()
    class FakeCampaign:
        def __init__(self, task):
            self.task = task; self.completed = starts[task]
            self.config = SimpleNamespace(campaign_id=task)
            self.checkpoints = SimpleNamespace(load=lambda _: {'completed_rounds': self.completed})
            self.scheduler = SimpleNamespace()
            self.store = SimpleNamespace(close=lambda: None)
        async def bootstrap(self): pass
        async def run_round(self, number):
            assert self.task not in active
            active.add(self.task); calls.append((self.task, number))
            try:
                if self.task == tasks[1]:
                    await asyncio.wait_for(fast_advanced.wait(), 5)
                if self.task == tasks[2]:
                    raise RuntimeError('isolated failure')
                if self.task == tasks[0] and number == 9:
                    fast_advanced.set()
                self.completed = number
                if self.task == tasks[0] and number == 10:
                    control(root, 'stop')
            finally:
                active.remove(self.task)
    monkeypatch.setattr(rounds, 'campaign', lambda root, task, *a, **kw: FakeCampaign(task))
    monkeypatch.setattr(rounds, 'round_slot_evidence', lambda *a: {})
    def exported(root, task, store, number):
        out = root/'rounds'/f'{number:04d}'/task
        write(out/'official-result.json', {'state': 'mock'})
        return out
    monkeypatch.setattr(rounds, 'export_round', exported)
    monkeypatch.setattr('mle_rsi.round_dashboard.report', lambda *a: {})
    await asyncio.wait_for(run(root), 10)
    assert (tasks[0], 8) in calls and (tasks[0], 9) in calls
    assert (tasks[1], 12) in calls and (tasks[2], 16) in calls
    assert calls.count((tasks[2], 16)) == 1
    assert read(root/'task-status'/f'{tasks[2]}.json')['state'] == 'blocked'
    assert read(root/'round-status.json')['state'] == 'stopped'


def test_native_viewer_distinguishes_queued_retired_and_dead_controller(tmp_path):
    from mle_rsi.common import lock
    from mle_rsi.round_viewer import task_view_state
    write(tmp_path/'round-status.json', {'state': 'running'})
    write(tmp_path/'task-status/leaf.json', {'state': 'queued'})
    assert task_view_state(tmp_path, 'leaf')['state'] == 'stopped'
    with lock(tmp_path/'controller'):
        assert task_view_state(tmp_path, 'leaf') == {'state': 'running', 'phase': '等待调度'}
        write(tmp_path/'round-status.json', {'state': 'paused'})
        assert task_view_state(tmp_path, 'leaf')['phase'] == '已暂停'
    write(tmp_path/'task-status/leaf.json', {'state': 'retired_medal'})
    assert task_view_state(tmp_path, 'leaf') == {'state': 'completed', 'phase': '已获牌封存'}


def test_dashboard_node_cache_tracks_updates_and_deletions(tmp_path, monkeypatch):
    from dream_rsi.storage import CampaignStore
    from mle_rsi.round_dashboard import dashboard_nodes
    store = CampaignStore(tmp_path/'cache')
    store.put('node','one',{'value':1})
    calls=[]; original=store.codec.decode_many
    def decode(values):
        calls.append(len(values))
        return original(values)
    monkeypatch.setattr(store.codec,'decode_many',decode)
    assert dashboard_nodes(store)==[{'value':1}]
    assert dashboard_nodes(store)==[{'value':1}]
    assert calls==[1,0]
    store.put('node','one',{'value':2});store.put('node','two',{'value':3})
    assert dashboard_nodes(store)==[{'value':2},{'value':3}]
    with store.connection:store.connection.execute("DELETE FROM records WHERE key='one'")
    assert dashboard_nodes(store)==[{'value':3}]
    store.close()


@pytest.mark.asyncio
async def test_unsent_connection_failure_auto_resumes_same_round(tmp_path,monkeypatch):
    from dream_rsi.campaign import Campaign
    from mle_rsi.gpt_provider import ConnectionNotSent
    import mle_rsi.rounds as rounds
    s=config();s['development_tasks']=s['experiment_tasks']=['leaf-classification']
    root=tmp_path/'run';initialize(root,s,'mock')
    original=Campaign.run_round;seen=[]
    async def flaky(self,number):
        seen.append(number)
        if len(seen)==1:raise ConnectionNotSent('TLS setup failed')
        return await original(self,number)
    monkeypatch.setattr(Campaign,'run_round',flaky)
    monkeypatch.setattr(rounds,'connection_retry_delay',lambda _:0)
    await run(root,through_round=1)
    assert seen==[1,1]
    row=report(root)['rows'][0]
    assert row['completed_rounds']==1 and row['candidates']==25
    assert read(root/'auto-recovery/leaf-classification.json')['failures']==1


@pytest.mark.asyncio
async def test_connection_recovery_has_finite_circuit_breaker(tmp_path,monkeypatch):
    from dream_rsi.campaign import Campaign
    from mle_rsi.gpt_provider import ConnectionNotSent
    import mle_rsi.rounds as rounds
    s=config();s['development_tasks']=s['experiment_tasks']=['leaf-classification']
    root=tmp_path/'run';initialize(root,s,'mock');calls=[]
    async def fail(self,number):
        calls.append(number)
        raise ConnectionNotSent('persistent connect failure')
    monkeypatch.setattr(Campaign,'run_round',fail)
    monkeypatch.setattr(rounds,'connection_retry_delay',lambda _:0)
    await run(root,through_round=1)
    assert len(calls)==6
    assert read(root/'round-status.json')['state']=='blocked'
