"""Explicit offline effort presets. Baseline remains the new-stage default."""
PRESETS={
 'baseline':dict(policy_versions_per_round=2,policy_tuning_trials=0,policy_tuning_scenarios=4,
                 replay_node_budget_growth=None,counterfactual_episodes=0,counterfactual_steps=8,
                 counterfactual_alternatives=3,replay_scenarios=4),
 'light':dict(policy_versions_per_round=3,policy_tuning_trials=12,policy_tuning_scenarios=2,
              replay_node_budget_growth=.2,counterfactual_episodes=1,counterfactual_steps=4,
              counterfactual_alternatives=2,replay_scenarios=2),
 'max':dict(policy_versions_per_round=5,policy_tuning_trials=48,policy_tuning_scenarios=4,
            replay_node_budget_growth=.7,counterfactual_episodes=4,counterfactual_steps=8,
            counterfactual_alternatives=3,replay_scenarios=4)}


def apply_tier(spec,tier='baseline'):
    if tier not in PRESETS:raise ValueError('Unknown offline effort tier')
    spec['search'].update(PRESETS[tier])
    return spec


def receipt(store,number):
    prefix=f'r{number:04d}-'
    tuning=[v for k,v in store.list('policy_tuning') if k.startswith(prefix)]
    return {'round':number,'actual_tuning_trials':[len(v.get('trials',[])) for v in tuning],
            'policy_versions':sum(k.startswith(prefix) for k,v in store.list('policy')),
            'counterfactual_reports':sum(k.startswith('dream:'+prefix) for k,v in store.list('counterfactual_feedback'))}
