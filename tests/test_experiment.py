import asyncio
import json
from pathlib import Path

import pytest

from mle_rsi import REPO
from mle_rsi.backend import quality, metric_spec
from mle_rsi.common import Budget, LimitReached, read
from mle_rsi.experiment import initialize, develop, freeze_policy, final_evaluate, campaign, frozen
from dream_rsi.web.data import RunReader


def spec():
    s=read(REPO/'configs/gemini-dev4-lite22.json')
    s['development_tasks']=['leaf-classification','spooky-author-identification']
    s['search'].update(development_rounds=2,batches_per_round=2,workers=2,
                       policy_versions_per_round=2,replay_steps=8,replay_scenarios=1,final_batches=2)
    return s


def test_budget_shares_phases_and_keeps_unknown_reservations(tmp_path):
    b=Budget(tmp_path,spec()['budget'])
    b.reserve_api('d','leaf','development',1.,'h')
    b.update_api('d',state='uncertain')
    b.reserve_api('other','leaf','final',1.,'h')
    b.update_api('other',state='completed',charged_usd=0)
    b.update_api('d',state='completed',charged_usd=2.4)
    with pytest.raises(LimitReached):b.reserve_api('dev2','leaf','development',2.7,'h')
    b.reserve_api('f','leaf','final',2.,'h')
    b.update_api('f',state='completed',charged_usd=2.)
    with pytest.raises(LimitReached):b.reserve_api('too-much','leaf','final',.7,'h')
    b.start_execution('e','leaf','development',2000,True)
    with pytest.raises(LimitReached):b.execution_allowance('leaf','development',True)
    b.finish_execution('e',1900,1900)
    assert b.execution_allowance('leaf','development',True)==500
    assert b.execution_allowance('leaf','final',True)==5300


def test_quality_directions_and_invalid_scores():
    loss=metric_spec('leaf-classification');auc=metric_spec('aerial-cactus-identification')
    assert quality(.1,loss)>quality(.5,loss)
    assert quality(.9,auc)>quality(.5,auc)
    for v in [float('nan'),float('inf'),-1]:
        with pytest.raises(ValueError):quality(v,loss)


@pytest.mark.asyncio
async def test_native_online_offline_freeze_final_and_viewer(tmp_path):
    root=initialize(tmp_path/'run',spec(),mode='mock')
    results=await develop(root)
    assert all(r['completed_rounds']==2 for r in results)
    for task in spec()['development_tasks']:
        c=campaign(root,task,'development')
        assert c.ledger.summary()['developer']['calls']==2
        assert c.store.get('activation','2') is not None
        c.store.close()
    choice=await freeze_policy(root)
    assert not choice['private_scores_used']
    assert len(choice['chosen']['scores'])==2
    await final_evaluate(root,['leaf-classification'])
    c=campaign(root,'leaf-classification','final',initial_source=(root/'frozen-policy.py').read_text())
    assert c.ledger.summary()['developer']['calls']==0
    c.store.close()
    summary=read(root/'summary.json')
    assert summary['mode']=='mock' and summary['groups']['all']['tasks']==22
    assert summary['groups']['all']['medals']==0
    viewer=RunReader(root/'campaigns')
    page=viewer.campaign('dev-leaf-classification')
    assert page['task_backend']['benchmark']=='mle'
    assert len(viewer.all_rounds('dev-leaf-classification')['nodes'])>=5
    assert (await freeze_policy(root))['policy_sha256']==choice['policy_sha256']


def test_frozen_config_rejects_source_drift(tmp_path,monkeypatch):
    root=initialize(tmp_path/'run',spec(),mode='mock')
    monkeypatch.setattr('mle_rsi.experiment.source_manifest',lambda:{'changed':True})
    with pytest.raises(RuntimeError):frozen(root)

@pytest.mark.asyncio
async def test_parallel_lanes_never_overlap_cpu_groups():
    from mle_rsi.experiment import task_lanes,task_cpu_ids
    from mle_rsi.backend import task_ids
    s=spec();tasks=task_ids()[:8];active=set();peak=0
    async def run(task):
        nonlocal peak
        cpus=set(task_cpu_ids(s,task,'final'))
        assert not active & cpus
        active.update(cpus);peak=max(peak,len(active))
        await asyncio.sleep(.01)
        active.difference_update(cpus)
        return {'task':task}
    rows=await task_lanes(s,tasks,'final',run)
    assert [r['task'] for r in rows]==tasks
    assert peak==8 and not active

@pytest.mark.asyncio
async def test_mle_viewer_hides_bootstrap_score(tmp_path):
    root=initialize(tmp_path/'root',spec(),mode='mock')
    c=campaign(root,'leaf-classification','development')
    await c.bootstrap()
    c.store.close()
    row=RunReader(root/'campaigns').campaign('dev-leaf-classification')
    assert row['best_score'] is None


def test_uncertain_two_calls_leave_exact_remaining_budget(tmp_path):
    b=Budget(tmp_path,spec()['budget'])
    for key in ['a','b']:
        b.reserve_api(key,'taxi','development',.863984,'hash')
        b.update_api(key,state='uncertain')
    b.reserve_api('new','taxi','development',3.272032,'newhash')
    with pytest.raises(LimitReached):b.reserve_api('over','taxi','final',.00001,'h')
    rows=read(tmp_path/'budget.json')['api']
    assert rows['a']['state']=='uncertain' and 'charged_usd' not in rows['a']
