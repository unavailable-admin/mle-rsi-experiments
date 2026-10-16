import asyncio
from dataclasses import replace
from pathlib import Path
import pytest
from mle_rsi import REPO
from mle_rsi.common import read, write, sha, lock
from mle_rsi.experiment import initialize, campaign
from mle_rsi.rounds import run, control
from mle_rsi.offline_max import export, prepare, opened, SEARCH, target_spec


def config():
    s=read(REPO/'configs/lite22-rounds-half.json')
    s['development_tasks']=s['experiment_tasks']=['leaf-classification']
    return s


def test_max_settings_and_half_machine():
    s=target_spec(config(),read(REPO/'configs/offline-max-4090-half.json'))
    assert s['search']['policy_tuning_trials']==48
    assert s['search']['policy_versions_per_round']==5
    assert s['search']['counterfactual_episodes']==4
    assert s['search']['replay_node_budget_growth']==.7
    assert s['execution']['cpu_ids']==list(range(32))
    assert s['execution']['gpu_pool']==[0,1,2,3]
    assert len(s['execution']['cpu_groups'])==8
    assert s['budget']==config()['budget']


@pytest.mark.asyncio
async def test_export_refresh_then_online_resume_without_rewriting_parent(tmp_path,monkeypatch):
    # Exercise the complete algorithm at small tuning cost. Production defaults
    # are asserted separately; counterfactual still runs for all four revisions.
    monkeypatch.setitem(SEARCH,'policy_tuning_trials',3)
    monkeypatch.setitem(SEARCH,'policy_tuning_scenarios',1)
    monkeypatch.setitem(SEARCH,'replay_scenarios',1)
    monkeypatch.setitem(SEARCH,'counterfactual_steps',2)
    parent=tmp_path/'parent';initialize(parent,config(),'mock')
    await run(parent,through_round=1)
    with opened(parent/'campaigns/dev-leaf-classification') as old:
        original_summary=old.get('round_summary','1')
        original_policy=old.get('checkpoint','active')['incumbent_id']
    old_frozen=sha(parent/'frozen-config.json')
    snap=export(parent,tmp_path/'export',1)
    root=prepare(snap,tmp_path/'next',read(REPO/'configs/offline-max-4090-half.json'),1)
    with opened(root/'campaigns/dev-leaf-classification') as store:
        assert store.get('round_summary','1')==original_summary
        assert len(store.list('node'))==25
    control(root,'resume')
    await run(root,through_round=2)
    state=read(root/'round-status.json')
    assert state['state']=='target_reached', state
    marker=read(root/'offline-max/leaf-classification.json')
    assert marker['candidate_nodes']==25
    assert marker['evaluated_versions']==5
    assert marker['counterfactual_reports']==4
    assert len(marker['actual_trials'])==4 and max(marker['actual_trials'])<=3
    with opened(root/'campaigns/dev-leaf-classification') as store:
        assert len(store.list('node'))==50
        assert store.get('round_summary','1')==original_summary
        refresh=store.get('offline_refresh','1')
        assert refresh['selected_id'].startswith('offline-max-')
        assert len(store.list('offline_refresh'))==1
    await run(root,through_round=2)
    with opened(root/'campaigns/dev-leaf-classification') as store:
        assert len(store.list('node'))==50
        assert len(store.list('offline_refresh'))==1
    assert sha(parent/'frozen-config.json')==old_frozen
    with opened(parent/'campaigns/dev-leaf-classification') as store:
        assert store.get('checkpoint','active')['incumbent_id']==original_policy
        assert len(store.list('node'))==25
    # Same baseline config cannot silently adopt mutated runtime search parameters.
    with pytest.raises(ValueError,match='already exists'):export(parent,snap,1)
    with lock(parent/'controller',blocking=False):
        with pytest.raises(BlockingIOError):export(parent,tmp_path/'locked',1)
    write(parent/'budget.json',{'api':{'x':{'state':'uncertain'}},'execution':{}})
    with pytest.raises(ValueError,match='unsettled'):export(parent,tmp_path/'uncertain',1)
    (snap/'rounds/0001/leaf-classification/selected.json').write_text('tampered')
    with pytest.raises(ValueError,match='Export changed'):
        prepare(snap,tmp_path/'tampered',read(REPO/'configs/offline-max-4090-half.json'),1)


@pytest.mark.asyncio
async def test_growth_overrides_legacy_step_limit_and_changes_normalization(tmp_path):
    from dream_rsi.replay import replay_node_cap, objective_from, history_pool_from_store, PoolEvaluator, replay_settings
    s=config();root=tmp_path/'p';initialize(root,s,'mock');await run(root,through_round=1)
    c=campaign(root,'leaf-classification','development',resource_pool=object())
    try:
        cp=c.checkpoints.load(c.config.campaign_id)
        cfg=replace(c.config,replay_node_budget=5,replay_node_budget_growth=.7,K2=1)
        pool=history_pool_from_store(c.store,cfg,cp['history_ids'],c.protocol_hash)
        assert replay_node_cap(pool.worlds[0],None,5,.7)==18
        evaluator=PoolEvaluator(objective_from(cfg,cp['beta1'],cp['beta2']),cfg.W,c.store,c.policy_limits,
            fixed_batch_size=True,persistent_policy_workers=True,**replay_settings(cfg))
        policy=c.store.get('policy',cp['incumbent_id'])
        result=await evaluator.evaluate(policy,pool,1)
        assert result.status=='valid'
        assert evaluator.objective.work=='per_slot'
        # A K2 of one does not truncate this node-budget-controlled episode.
        assert any(len(row.trajectory.steps)>1 for row in result.worlds)
    finally:c.store.close()


@pytest.mark.asyncio
async def test_refresh_crash_resumes_cached_versions_and_preserves_round_summary(tmp_path):
    s=config();s['search'].update(policy_tuning_trials=2,policy_tuning_scenarios=1,
        policy_versions_per_round=5,replay_scenarios=1,counterfactual_episodes=1,counterfactual_steps=1)
    root=tmp_path/'run';initialize(root,s,'mock');await run(root,through_round=1)
    c=campaign(root,'leaf-classification','development',resource_pool=object())
    original=c.store.get('round_summary','1')
    old=c.checkpoints.load(c.config.campaign_id)
    revise=c.developer.revise
    calls=[]
    async def interrupted(base,feedback,request):
        calls.append(request)
        if len(calls)==2:raise asyncio.CancelledError()
        return await revise(base,feedback,request)
    c.developer.revise=interrupted
    try:
        with pytest.raises(asyncio.CancelledError):await c.refresh_offline(1)
        assert c.checkpoints.load(c.config.campaign_id)==old
        assert c.store.get('policy','offline-max-r0001-v0001') is not None
        cached=c.store.get('policy','offline-max-r0001-v0001')
        c.developer.revise=revise
        result=await c.refresh_offline(1)
        assert result['evaluated_versions']==5 and result['candidate_nodes']==25
        assert c.store.get('policy','offline-max-r0001-v0001')==cached
        assert c.store.get('round_summary','1')==original
        assert await c.refresh_offline(1)==result
    finally:c.store.close()


@pytest.mark.asyncio
async def test_inherited_winning_submission_uses_sealed_copy_on_new_host(tmp_path):
    from dataclasses import replace
    from mle_rsi.rounds import export_round
    s=config();root=tmp_path/'run';initialize(root,s,'mock');await run(root,through_round=1)
    submission=root/'rounds/0001/leaf-classification/submission.csv'
    submission.write_text('id,prediction\n1,0.5\n')
    with opened(root/'campaigns/dev-leaf-classification',False) as store:
        best=store.get('round_best','1')
        node=best['best_node']
        node=replace(node,evaluation=replace(node.evaluation,diagnostics={
            'output_dir':'/nonexistent-5090/path/output','submission_sha256':sha(submission)}))
        store.put('round_best','2',{**best,'best_node':node})
        write(root/'offline-max-lineage.json',{})
        out=export_round(root,'leaf-classification',store,2)
        assert sha(out/'submission.csv')==sha(submission)
