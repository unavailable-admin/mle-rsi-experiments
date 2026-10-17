import asyncio
from pathlib import Path
from types import SimpleNamespace
import pytest
from mle_rsi.common import Budget, BudgetExhausted, read, write
from mle_rsi.candidate_contract import resource_request, validate_training_contract
from mle_rsi.stage2 import budget_for_stage


def test_increment_preserves_pending_and_limits_each_new_stage(tmp_path):
    old={'api':{'old':dict(task='a',phase='development',state='uncertain',reserved_usd=3)},
         'execution':{'old':dict(task='a',phase='development',state='running',wall_seconds=905,gpu_seconds=905)}}
    write(tmp_path/'budget.json',old)
    caps=budget_for_stage(old,{},['a'])
    b=Budget(tmp_path,caps)
    b.reserve_api('new','a','development',15,'request')
    with pytest.raises(BudgetExhausted):b.reserve_api('extra','a','development',.01,'request2')
    assert read(tmp_path/'budget.json')['api']['old']==old['api']['old']
    assert b.reserve_execution('newjob','a','development',180000,True)==179995
    with pytest.raises(BudgetExhausted):b.reserve_execution('extra','a','development',10,True)


def test_candidate_routing_and_training_contract():
    spec={'stage2':{'candidate_resources':True},'execution':{'gpu_tasks':[]}}
    assert resource_request('MLE_RESOURCE="gpu"','jigsaw',spec)
    assert not resource_request('MLE_RESOURCE="cpu"','image',spec)
    with pytest.raises(ValueError):resource_request('MLE_RESOURCE=detect_device()','x',spec)
    with pytest.raises(ValueError):validate_training_contract({'members':[{'name':'cb','validation':{'iterations':900},'final':{'iterations':350}}]})
    validate_training_contract({'members':[{'name':'cb','validation':{'iterations':350},'final':{'iterations':350}}]})


def test_kill_race_does_not_mask_live_failure():
    from bench_container import kill_if_running
    import docker
    class Container:
        status='exited'
        def kill(self):raise docker.errors.APIError('409 already exited')
        def reload(self):pass
    kill_if_running(Container())
    c=Container();c.status='running'
    with pytest.raises(docker.errors.APIError):kill_if_running(c)


@pytest.mark.asyncio
async def test_policy_fallback_is_durable_and_replayable(tmp_path):
    from dream_rsi.storage import CampaignStore
    from dream_rsi.models import PolicyView
    from mle_rsi.policy_recovery import RecoveringPolicyWorker
    store=CampaignStore(tmp_path)
    root={'node_id':'r','score':0,'child_ids':[]}
    view=PolicyView(root,(root,),('r',),(),(),0,5,5,parent_repeats_allowed=True)
    worker=RecoveringPolicyWorker('',None,store=store,tree_id='tree-0001')
    worker.failure='broken pipe input_bytes=123 returncode=1'
    first=await worker.decide(view)
    assert first.parent_ids==('r',)*5
    again=RecoveringPolicyWorker('',None,store=store,tree_id='tree-0001')
    assert await again.decide(view)==first
    assert 'input_bytes=123' in store.get('policy_fallback','tree-0001:0')['error']
    store.close()


def test_atomic_bundle_and_cache_identity(tmp_path, monkeypatch):
    from mle_rsi import candidate_runtime as runtime
    monkeypatch.setattr(runtime,'task_config',lambda:{'output_dir':str(tmp_path)})
    for name in ('submission.csv','validation_predictions.csv','candidate-result.json','validation-split.json'):
        (tmp_path/name).write_text('complete')
    with pytest.raises(ValueError):runtime.commit_outputs()
    runtime.record_training_contract([dict(name='model',validation={'n':1},final={'n':1})])
    bundle=runtime.commit_outputs()
    (tmp_path/'submission.csv').write_text('partial overwrite')
    assert (bundle/'submission.csv').read_text()=='complete'
    assert read(tmp_path/'complete-bundle.json')['directory']==bundle.name
    identity={key:'a'*64 for key in ('data_sha256','split_sha256','weights_sha256','preprocessing_sha256','extractor_sha256')}
    identity.update(deterministic=True,frozen=True)
    first=runtime._feature_key(identity)
    identity['split_sha256']='b'*64
    assert runtime._feature_key(identity)!=first
    identity['deterministic']=False
    with pytest.raises(ValueError):runtime._feature_key(identity)


@pytest.mark.asyncio
async def test_interrupted_slot_finishes_without_fabricating_node_or_retry(tmp_path,monkeypatch):
    from mle_rsi import REPO
    from mle_rsi.backend import MLEBackend
    from mle_rsi.experiment import initialize
    from mle_rsi.rounds import run,round_slot_evidence
    from dream_rsi.storage import CampaignStore
    spec=read(REPO/'configs/lite22-rounds-half.json')
    spec['development_tasks']=spec['experiment_tasks']=['leaf-classification']
    initialize(tmp_path/'run',spec,'mock')
    calls=[];original=MLEBackend._evaluate
    async def fail_one(self,artifact,batch):
        value=batch() if callable(batch) else batch
        calls.append(value['attempt_id'])
        if value['attempt_id']=='tree-0001:r0:a0':raise RuntimeError('injected interrupted candidate')
        return await original(self,artifact,batch)
    monkeypatch.setattr(MLEBackend,'_evaluate',fail_one)
    await run(tmp_path/'run',through_round=1)
    await run(tmp_path/'run',through_round=1)
    store=CampaignStore(tmp_path/'run/campaigns/dev-leaf-classification',read_only=True)
    result=round_slot_evidence(store,1)
    assert result['materialized_candidates']==24 and len(result['interrupted_slots'])==1
    assert calls.count("tree-0001:r0:a0")==1
    assert len(store.list("attempt"))==25
    assert len(store.list('node'))==24
    store.close()


@pytest.mark.asyncio
async def test_boundary_migration_excludes_later_nodes_but_preserves_spend(tmp_path):
    from mle_rsi import REPO
    from mle_rsi.experiment import initialize
    from mle_rsi.rounds import run
    from mle_rsi.stage2 import prepare
    from dream_rsi.storage import CampaignStore
    spec=read(REPO/'configs/lite22-rounds-half.json')
    spec['development_tasks']=spec['experiment_tasks']=['leaf-classification']
    old=tmp_path/'old';new=tmp_path/'new'
    initialize(old,spec,'mock')
    await run(old,through_round=1)
    # A future, unsealed round must not leak into the imported search state.
    store=CampaignStore(old/'campaigns/dev-leaf-classification')
    store.put('policy','r0002-v0000',store.get('policy','initial'))
    store.close()
    ledger={'api':{'old-unknown':dict(task='leaf-classification',phase='development',state='uncertain',reserved_usd=.7)},'execution':{}}
    write(old/'budget.json',ledger)
    prepare(old,new)
    assert read(new/'budget.json')==ledger
    store=CampaignStore(new/'campaigns/dev-leaf-classification',read_only=True)
    assert store.get('policy','r0002-v0000') is None
    assert store.get('checkpoint','active')['completed_rounds']==1
    store.close()
    from mle_rsi.rounds import control,unresolved
    assert not unresolved(new,'leaf-classification')
    control(new,'resume')
    await run(new,through_round=2)
    assert read(new/'task-status/leaf-classification.json')['state']=='round_complete'
    assert read(new/'budget.json')==ledger


def test_context_selection_keeps_parent_and_discloses_omissions():
    import json
    from dream_rsi.prompting import _evidence_messages
    from dream_rsi.config import ModelConfig,HistoryInputConfig
    model=ModelConfig(provider='mock',model='mock',context_tokens=100000,max_output_tokens=1000,tokenizer='conservative-utf8')
    user={'parent':{'node_id':'n1','diagnosis':{'error':'important'}},
          'files':{'/solver.py':'exact parent source'},
          'reference_sources':[{'node_id':'n2','source':'x'*50000}]}
    messages=_evidence_messages('system',user,model,'discovery',HistoryInputConfig(mode='evidence',discovery_tokens=40000))
    chosen=json.loads(messages[1]['content'])
    assert chosen['files']==user['files'] and chosen['parent']==user['parent']
    assert not chosen['reference_sources']
    assert chosen['context_selection']['omitted_references'][0]['node_id']=='n2'


def test_stagnation_uses_public_scores_and_preserves_width():
    from mle_rsi.policy_recovery import stagnation_draft
    from dream_rsi.models import BatchDecision
    nodes=[{'node_id':'r','valid':False,'score':0}]+[{'node_id':str(i),'valid':True,'score':1} for i in range(60)]
    view=SimpleNamespace(revealed_nodes=nodes,eligible_parent_ids=('r','1'))
    original=BatchDecision(('1',)*5,{},'measured')
    result=stagnation_draft(original,view)
    assert result.parent_ids==('1','1','1','1','r')
    nodes[-1]['score']=1.1
    assert stagnation_draft(original,view)==original


@pytest.mark.asyncio
async def test_cumulative_online_policy_context_matches_replay(tmp_path,monkeypatch):
    from mle_rsi import REPO
    from mle_rsi.experiment import initialize
    from mle_rsi.rounds import run
    from dream_rsi.policy_runtime import PolicyWorker
    seen=[];original=PolicyWorker.decide
    async def observe(self,view,state=None):
        seen.append((len(view.revealed_nodes),len(view.base_context)))
        return await original(self,view,state)
    monkeypatch.setattr(PolicyWorker,'decide',observe)
    spec=read(REPO/'configs/lite22-rounds-half.json')
    spec['development_tasks']=spec['experiment_tasks']=['leaf-classification']
    initialize(tmp_path/'run',spec,'mock')
    await run(tmp_path/'run',through_round=2)
    assert any(count>25 for count,_ in seen)
    assert all(context==0 for _,context in seen)
