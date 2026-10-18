import pytest
from mle_rsi.controller import initialize,run
from mle_rsi.reporting import status


@pytest.mark.asyncio
async def test_offline_tuning_counterfactual_actual_counts(tmp_path,spec):
    # Small numerical fixture verifies the path, not the production max workload.
    spec['offline_tier']='max'
    spec['search'].update(M=2,policy_tuning_trials=2,policy_tuning_scenarios=1,
                         counterfactual_episodes=1,counterfactual_steps=1,counterfactual_alternatives=1)
    root=initialize(tmp_path/'run',spec)
    await run(root)
    offline=status(root)['tasks']['fixture']['summary']['offline']
    assert offline['counterfactual_reports']==1
    assert offline['tuning_trials'] and max(offline['tuning_trials'])<=2
