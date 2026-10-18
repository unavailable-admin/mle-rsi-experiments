from mle_rsi.config import OFFLINE


def test_max_retains_explicit_effort_and_baseline_stays_default():
    assert OFFLINE['max']['M']==5
    assert OFFLINE['max']['policy_tuning_trials']==48
    assert OFFLINE['max']['counterfactual_episodes']==4
    assert OFFLINE['max']['replay_node_budget_growth']==.7
    assert OFFLINE['baseline']['counterfactual_episodes']==0
