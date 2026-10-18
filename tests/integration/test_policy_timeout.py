"""Concurrent host work must not make a millisecond policy hit a one-second limit."""
import asyncio
import time

import pytest
from dream_rsi.config import ResourceLimits
from dream_rsi.errors import PolicyTimeout
from dream_rsi.models import EvaluationReport, FailClass
from dream_rsi.policy import workspace_policy_source
from dream_rsi.policy_runtime import PolicyWorker
from dream_rsi.decision_rules import transform
from dream_rsi.seed import make_artifact
from dream_rsi.tree import DiscoveryTree, build_view
from mle_rsi.backend import assemble
from mle_rsi.config import validate
from mle_rsi.ledger import Ledger
from mle_rsi.resources import ResourcePool


def initial_view():
    tree = DiscoveryTree.create('snapshot', EvaluationReport(False, True, 0., FailClass.OK, 'protocol'),
                                (), make_artifact('Unevaluated workspace', '# implement task'))
    return build_view(tree.nodes, (), 0, 2, 2, action_protocol='branching-workspaces-v3',
                      scheduling_beta=.6, remaining_nodes=4)[0]


@pytest.mark.asyncio
async def test_configured_campaign_survives_host_loop_stall(tmp_path, spec, monkeypatch):
    spec['search'].pop('policy_decision_timeout_s', None)
    spec = validate(spec)
    ledger = Ledger(tmp_path, spec['budget'])
    campaign = assemble(tmp_path, spec['tasks'][0], spec, ledger,
                        ResourcePool(spec['host'], mock=True))
    try:
        assert campaign.policy_limits.wall_time_s == 8
        assert campaign.scheduler.policy_limits.wall_time_s == 8
        source = transform(workspace_policy_source(), {'best_first': True, 'fallback': True,
            'prefer_repair': True, 'repair_limit': 2, 'stagnation_draft': 3})
        original = PolicyWorker._read
        async def with_stall(worker):
            if getattr(worker, 'read_phase', 'startup') == 'decision':
                asyncio.get_running_loop().call_soon(time.sleep, 1.3)
            return await original(worker)
        monkeypatch.setattr(PolicyWorker, '_read', with_stall)
        async with PolicyWorker(source, ResourceLimits(wall_time_s=1)) as worker:
            with pytest.raises(PolicyTimeout, match='phase=decision'):
                await worker.decide(initial_view())
        async with PolicyWorker(source, campaign.policy_limits) as worker:
            assert (await worker.decide(initial_view())).parent_ids == ('r', 'r')
    finally:
        campaign.close()
        ledger.close()


@pytest.mark.asyncio
async def test_nonterminating_policy_still_times_out():
    source = 'class Policy:\n def initial_state(self): return {}\n def decide(self,view,state):\n  while True: pass\n'
    async with PolicyWorker(source, ResourceLimits(wall_time_s=1)) as worker:
        with pytest.raises(PolicyTimeout, match='phase=decision'):
            await worker.decide(initial_view())
