from dataclasses import replace
from types import SimpleNamespace
import pytest
import sys
from pathlib import Path
NATIVE = Path(__file__).resolve().parents[1] / 'third_party/dream-rsi/my_rsi'
sys.path.insert(0, str(NATIVE / 'src'))
sys.path.insert(0, str(NATIVE / 'tests'))
from conftest import make_tree, store
from dream_rsi.errors import PolicyTimeout, PolicyError, InfrastructureError
from dream_rsi.replay import HistoryPool, PoolEvaluator, ReplayRunner, ReplayWorld, TreeObjective
from dream_rsi.policy import initial_policy_source
from dream_rsi.config import ResourceLimits
from dream_rsi.campaign import Campaign


def setup(store):
    pool = HistoryPool.freeze([ReplayWorld.from_tree(make_tree())], 'protocol')
    evaluator = PoolEvaluator(TreeObjective(.01,.005), W=1, store=store,
        policy_limits=ResourceLimits(wall_time_s=5), persistent_policy_workers=True)
    return evaluator, pool, initial_policy_source()


@pytest.mark.asyncio
async def test_timeout_retry_and_cached_success(store, monkeypatch):
    evaluator, pool, source = setup(store)
    run = ReplayRunner.run
    calls = []
    async def flaky(self, *args, **kw):
        calls.append(kw['worker'])
        if len(calls) == 1: raise PolicyTimeout('injected')
        return await run(self,*args,**kw)
    monkeypatch.setattr(ReplayRunner,'run',flaky)
    result = await evaluator.evaluate(source,pool,5)
    assert result.status == 'valid' and len(calls) == 2
    assert calls[0] is not calls[1]
    assert await evaluator.evaluate(source,pool,5) == result
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_timeout_exhaustion_is_durable_not_invalid(store, monkeypatch):
    evaluator,pool,source = setup(store)
    calls = []
    async def timeout(*a,**kw):
        calls.append(1)
        raise PolicyTimeout('injected')
    monkeypatch.setattr(ReplayRunner,'run',timeout)
    for _ in range(2):
        with pytest.raises(PolicyTimeout,match='exhausted 3'):
            await evaluator.evaluate(source,pool,5)
    assert len(calls) == 3
    assert not store.list('pool_evaluation') and not store.list('replay_episode')


@pytest.mark.asyncio
async def test_legacy_timeout_recovered_and_audited(store, monkeypatch):
    evaluator,pool,source = setup(store)
    good = await evaluator.evaluate(source,pool,5)
    for key,row in store.list('replay_episode'):
        store.put('replay_episode',key,replace(row,status='invalid',V=None,error='Policy timeout or malformed output: '))
    bad = replace(good,status='invalid',mean_V=None,worlds=tuple(v for k,v in store.list('replay_episode')))
    store.put('pool_evaluation',good.evaluation_hash,bad)
    recovered=await evaluator.evaluate(source,pool,5)
    assert recovered.status == 'valid' and recovered.mean_V == good.mean_V
    assert len(store.list('replay_timeout_legacy_pool')) == 1
    assert len(store.list('replay_timeout_legacy_episode')) == 1


@pytest.mark.asyncio
async def test_real_policy_error_not_retried(store,monkeypatch):
    evaluator,pool,source=setup(store);calls=[]
    async def invalid(*a,**kw):
        calls.append(1);raise PolicyError('malformed decision')
    monkeypatch.setattr(ReplayRunner,'run',invalid)
    result=await evaluator.evaluate(source,pool,5)
    assert result.status=='invalid' and len(calls)==1
    assert not store.list('replay_timeout_attempts')


def test_fallback_preserves_old_policy_without_new_score(store):
    from dream_rsi.registry import PolicyRegistry
    from dream_rsi.models import EvaluationBundle
    registry=PolicyRegistry(store)
    v=registry.register(initial_policy_source(),None,'initial','old')
    registry.attach_evaluation(v.version_id,EvaluationBundle('old-pool',(),.5,'valid','eval'))
    engine=SimpleNamespace(store=store,registry=registry)
    saved=[]
    cp={'incumbent_id':'old','pool_hash':'new-pool'}
    result=Campaign._retain_policy_after_timeout(engine,cp,17,'',saved.append,PolicyTimeout('exhausted'))
    assert result['selected_id']=='old' and result['selected_mean_V'] is None
    assert saved == [result] and store.get('activation','18')['version_id']=='old'
    assert store.get('offline_fallback','17')['status']=='skipped_timeout'
    assert store.get('policy','old').evaluation.pool_hash=='old-pool'
    with pytest.raises(InfrastructureError):
        Campaign._retain_policy_after_timeout(engine,{**cp,'incumbent_id':'missing'},18,'',saved.append,PolicyTimeout())


@pytest.mark.asyncio
async def test_campaign_timeout_completes_selection_without_revision(store, monkeypatch):
    from dream_rsi.campaign import Campaign
    from dream_rsi.config import CampaignConfig
    from dream_rsi.registry import PolicyRegistry
    from dream_rsi.models import EvaluationBundle
    evaluator,pool,source=setup(store)
    registry=PolicyRegistry(store)
    old=registry.register(source,None,'initial','old')
    registry.attach_evaluation('old',EvaluationBundle('old-pool',(),.5,'valid','eval'))
    engine=object.__new__(Campaign)
    engine.store=store;engine.registry=registry
    engine.config=replace(CampaignConfig(),fixed_exploration=True)
    engine.policy_limits=ResourceLimits(wall_time_s=5)
    engine._pool=lambda cp:pool
    saved=[]
    cp={'incumbent_id':'old','pool_hash':pool.pool_hash,'beta1':.01,'beta2':.005}
    async def timeout(*args,**kw):raise PolicyTimeout('exhausted')
    monkeypatch.setattr(PoolEvaluator,'evaluate',timeout)
    out=await engine._dream(cp,17,save=saved.append)
    assert out['phase']=='SELECTED' and out['selected_id']=='old'
    assert out['selected_mean_V'] is None
    assert store.get('policy','r0017-v0000').evaluation is None
    assert store.get('offline_fallback','17')


@pytest.mark.asyncio
async def test_campaign_rechecks_legacy_invalid_incumbent(store):
    from dream_rsi.config import CampaignConfig
    from dream_rsi.registry import PolicyRegistry
    evaluator,pool,source=setup(store)
    good=await evaluator.evaluate(source,pool,5)
    registry=PolicyRegistry(store)
    registry.register(source,None,'initial','old')
    registry.attach_evaluation('old',good)
    registry.register(source,'old','reevaluation','r0017-v0000')
    bad=replace(good,status='invalid',mean_V=None,
        worlds=tuple(replace(row,status='invalid',V=None,error='Policy timeout or malformed output: ') for row in good.worlds))
    registry.attach_evaluation('r0017-v0000',bad)
    engine=object.__new__(Campaign)
    engine.store=store;engine.registry=registry
    engine.config=replace(CampaignConfig(),fixed_exploration=True)
    engine.policy_limits=ResourceLimits(wall_time_s=5)
    engine._pool=lambda cp:pool
    cp={'incumbent_id':'old','pool_hash':pool.pool_hash,'beta1':.01,'beta2':.005}
    result=await engine._dream(cp,17,save=lambda cp:None)
    assert result['phase']=='SELECTED' and result['offline_status']=='completed'
    assert store.get('policy','r0017-v0000').status=='valid'
    assert store.get('replay_timeout_legacy_policy','r0017-v0000').status=='invalid'
