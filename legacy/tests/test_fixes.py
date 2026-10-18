import asyncio
from dataclasses import replace
import itertools
import mle_rsi
from mle_rsi.repair_policy import repair_best_first_source
from dream_rsi.config import PolicyLimits
from dream_rsi.models import BatchDecision
from dream_rsi.policy import best_first_policy_source
from dream_rsi.policy_runtime import PolicyWorker
from dream_rsi.repair_guard import bounded_repair_decision, repair_parent_ids
from scripts.audit_firstbatch_policy import view


def node(i, score=0., fail='ok', parent='r', stage='draft'):
    return dict(node_id=i,parent_id=parent,score=score,valid=fail=='ok',fail_class=fail,stage=stage)


def test_rank_real_score_ties_zero_and_permutations():
    ns={};exec(best_first_policy_source(width=5),ns);policy=ns['Policy']()
    for scores in [(1.,.999),(1e-15,0.),(-.1,-.11),(1.,1.)]:
        for order in itertools.permutations([node('a',scores[0]),node('b',scores[1])]):
            v=dict(revealed_nodes=order,eligible_parent_ids=['b','a'],remaining_nodes=5,
                   remaining_rounds=5,max_parallelism=5,priority_jitter={'a':-.05,'b':.05})
            assert policy.decide(v,{})['parent_ids']==(['b']*5 if scores[0]==scores[1] else ['a']*5)


def test_explicit_exploration_does_not_replace_best_first_slots():
    ns={};exec(repair_best_first_source(5),ns);p=ns['Policy']()
    v=dict(revealed_nodes=[node('r',1e-12,parent=None),node('a',.9),node('b',.8)],
           eligible_parent_ids=['r','a','b'],remaining_nodes=5,remaining_rounds=5,max_parallelism=5)
    assert p.decide(v,{})['parent_ids']==['a','a','a','a','b']


def test_all_failed_repair_budget_categories_and_no_chains():
    nodes=[node('r',1e-12,parent=None)]+[node('n'+str(i),fail=f) for i,f in enumerate(['runtime_error','timeout','source_error','runtime_error'])]
    observation=replace(view(nodes,5),max_parallelism=5)
    decision=bounded_repair_decision(BatchDecision(('r',)*5),observation)
    assert len(decision.parent_ids)==5 and len(set(decision.parent_ids[:3]))==3
    assert decision.parent_ids[-2:]==('r','r')
    assert len({next(n['fail_class'] for n in nodes if n['node_id']==p) for p in decision.parent_ids[:3]})==3
    for budget in range(6):
        v=replace(observation,remaining_nodes=budget)
        d=bounded_repair_decision(BatchDecision(('r',)*5),v)
        assert len(d.parent_ids)<=budget
    used=decision.parent_ids[0]
    nodes.append(node('debug-failed',fail='runtime_error',parent=used,stage='debug'))
    assert used not in repair_parent_ids(nodes,[n['node_id'] for n in nodes],5)
    assert 'debug-failed' not in repair_parent_ids(nodes,[n['node_id'] for n in nodes],5)
    nodes.append(node('success',.8))
    assert len(repair_parent_ids(nodes,[n['node_id'] for n in nodes],5))==1


def test_policy_worker_reset_and_host_share_repair_semantics():
    nodes=[node('r',1e-12,parent=None)]+[node('n'+str(i),fail=f) for i,f in enumerate(['runtime_error','timeout','source_error','runtime_error'])]
    observation=replace(view(nodes,5),max_parallelism=5)
    async def check():
        async with PolicyWorker(repair_best_first_source(5),PolicyLimits(wall_time_s=5,bounded_repair=True)) as w:
            first=await w.decide(observation)
            await w.reset()
            second=await w.decide(observation)
            assert first.parent_ids==second.parent_ids
            assert list(first.parent_ids[:3])==repair_parent_ids(nodes,observation.eligible_parent_ids,5)
    asyncio.run(check())


def test_prediction_claim_survives_store_reopen(tmp_path):
    from dream_rsi.storage import CampaignStore
    from mle_rsi.diversity import record_prediction_fingerprints
    s=CampaignStore(tmp_path)
    assert record_prediction_fingerprints(s,'a',{'validation':'same','submission':'one'})=={}
    s.close();s=CampaignStore(tmp_path)
    assert record_prediction_fingerprints(s,'a',{'validation':'same'})=={}
    assert record_prediction_fingerprints(s,'b',{'validation':'same','submission':'two'})=={'validation':'a'}
    s.close()


def test_duplicate_observation_reaches_model_evidence():
    from dream_rsi.evidence import evaluation_summary
    report={'valid':True,'metrics':{'public_accuracy':.9},'diagnostics':{
        'prediction_duplicate_of':{'validation':'tree-0001:r0:a3'}}}
    summary=evaluation_summary(report)
    assert summary['prediction_duplicate_of']=={'validation':'tree-0001:r0:a3'}
    assert summary['valid']
