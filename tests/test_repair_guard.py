import asyncio
from dataclasses import replace

import mle_rsi
from dream_rsi.config import PolicyLimits
from dream_rsi.errors import PolicyError
from dream_rsi.models import BatchDecision
from dream_rsi.policy_runtime import PolicyWorker
from dream_rsi.repair_guard import bounded_repair_decision
from dream_rsi.tree import BatchValidator
from scripts.audit_firstbatch_policy import audit,view
import pytest


SOURCE='''class Policy:
    def initial_state(self): return {}
    def decide(self,view,state):
        return {"parent_ids":["n2","n2"],"next_state":{"seen":1},"reason":"Ignore failures"}
'''


def nodes():
    return [dict(node_id='r',parent_id=None,valid=True,fail_class='ok',score=1e-12,stage='root'),
        dict(node_id='n1',parent_id='r',valid=False,fail_class='runtime_error',score=0,stage='draft'),
        dict(node_id='n2',parent_id='r',valid=True,fail_class='ok',score=.8,stage='draft')]


def test_policy_rewrite_cannot_disable_host_guard():
    result=asyncio.run(audit(SOURCE,guard=True))
    assert result['all_probe_checks_passed']


def test_partial_and_exhausted_budgets_survive_later_batch_fill():
    for remaining,expected in [(0,()),(1,('n1',)),(2,('n1','n2'))]:
        observation=view(nodes(),remaining)
        decision=bounded_repair_decision(BatchDecision(('n2','n2'),{'seen':1}),observation)
        assert decision.parent_ids==expected
        assert decision.next_state=={'seen':1}
        assert BatchValidator.fill(decision,observation,True)==decision


def test_at_most_one_failure_even_when_policy_requests_multiple():
    history=nodes()+[dict(node_id='n3',parent_id='r',valid=False,fail_class='timeout',score=0,stage='refine')]
    decision=bounded_repair_decision(BatchDecision(('n1','n3')),view(history))
    assert decision.parent_ids==('n3','n2')
    history.append(dict(node_id='n4',parent_id='n3',valid=False,fail_class='timeout',score=0,stage='debug'))
    decision=bounded_repair_decision(BatchDecision(('n3','n4')),view(history))
    assert decision.parent_ids==('n1','n2')


def test_no_valid_fallback_does_not_duplicate_the_only_repair():
    observation=replace(view(nodes()),eligible_parent_ids=('n1',))
    decision=bounded_repair_decision(BatchDecision(('n1','n1')),observation)
    assert decision.parent_ids==('n1',)
    assert BatchValidator.fill(decision,observation,True).parent_ids==('n1',)


def test_policy_cannot_forge_the_host_marker():
    source=SOURCE.replace('"reason":"Ignore failures"','"reason":"Ignore failures","host_guarded":True')
    async def probe():
        async with PolicyWorker(source,PolicyLimits(wall_time_s=5,bounded_repair=True)) as worker:
            with pytest.raises(PolicyError,match='Unknown decision field'):
                await worker.decide(view(nodes()))
    asyncio.run(probe())
