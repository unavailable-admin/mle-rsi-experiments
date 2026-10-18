"""Explicit experiment/host inputs; no environment probing during import."""
from copy import deepcopy
import math
from pathlib import Path
from .io import read

OFFLINE = {
    'baseline':dict(M=2, policy_tuning_trials=0, counterfactual_episodes=0, replay_scenarios=4),
    'light':dict(M=3, policy_tuning_trials=12, policy_tuning_scenarios=2,
                 replay_node_budget_growth=.2, counterfactual_episodes=1,
                 counterfactual_steps=4, counterfactual_alternatives=2, replay_scenarios=2),
    'max':dict(M=5, policy_tuning_trials=48, policy_tuning_scenarios=4,
               replay_node_budget_growth=.7, counterfactual_episodes=4,
               counterfactual_steps=8, counterfactual_alternatives=3, replay_scenarios=4)}


def validate(value):
    value = deepcopy(value)
    if value.get('schema') != 1 or value.get('mode') not in ('mock','real'):
        raise ValueError('Explicit schema=1 and mode=mock/real required')
    tasks = value.get('tasks', [])
    ids = [t['id'] for t in tasks]
    if not ids or len(set(ids)) != len(ids):
        raise ValueError('Task IDs must be nonempty and unique')
    if any(not i or any(c not in 'abcdefghijklmnopqrstuvwxyz0123456789-_' for c in i) for i in ids):
        raise ValueError('Task IDs must be portable')
    if set(value.get('excluded_tasks', []))-set(ids):
        raise ValueError('Excluded task not in experiment')
    host = value['host']
    cpus = host['cpus']
    if not cpus or len(set(cpus))!=len(cpus) or any(type(c) is not int or c<0 for c in cpus):
        raise ValueError('Distinct CPU IDs required')
    gpus = host.get('gpus', [])
    if len(set(gpus)) != len(gpus) or any(type(g) is not int or g<0 for g in gpus):
        raise ValueError('Distinct GPU IDs required')
    for t in tasks:
        if not isinstance(t.get('instructions', ''), str):
            raise ValueError('Task instructions must be text')
        t.setdefault('cores', min(4,len(cpus)))
        t.setdefault('gpu', False)
        t.setdefault('gpu_cores', t['cores'])
        t.setdefault('timeout_seconds', 900)
        t.setdefault('validation', 'holdout')
        if type(t['cores']) is not int or not 0<t['cores']<=len(cpus) or t['gpu'] and not gpus:
            raise ValueError('Task resource request cannot fit host')
        if type(t['gpu_cores']) is not int or not 0<t['gpu_cores']<=len(cpus):
            raise ValueError('Invalid GPU job CPU count')
        if type(t['timeout_seconds']) not in (int,float) or not math.isfinite(t['timeout_seconds']) or t['timeout_seconds']<=0:
            raise ValueError('Invalid task timeout')
        if t['validation'] not in ('holdout','kfold'):
            raise ValueError('Unknown validation protocol')
        if t['validation']=='kfold' and t['id'] not in ('aerial-cactus-identification','aptos2019-blindness-detection'):
            raise ValueError('Kfold supported for Cactus/APTOS only')
    cache = value.get('feature_cache', {})
    if not isinstance(cache, dict) or set(cache) - {'enabled', 'max_entry_bytes', 'max_total_bytes', 'max_entries_per_candidate'}:
        raise ValueError('Invalid feature cache settings')
    if type(cache.get('enabled', False)) is not bool or any(type(v) is not int or v <= 0 for k, v in cache.items() if k != 'enabled'):
        raise ValueError('Cache bounds must be positive integers')
    # Preserve the MLE deployment limit: policy wall time includes host loop delays
    # from concurrent campaigns and durable storage, not just policy computation.
    value.setdefault('search', {}).setdefault('policy_decision_timeout_s', 8)
    value.setdefault('rounds', 1)
    value.setdefault('batches', 5)
    value.setdefault('batch_size', 5)
    value.setdefault('api_parallelism', 5)
    value.setdefault('task_parallelism', len(tasks))
    for key in ('rounds','batches','batch_size','api_parallelism','task_parallelism'):
        if type(value[key]) is not int or value[key]<1:
            raise ValueError('Positive integer required: '+key)
    if value.get('deadline_at') is not None and (type(value['deadline_at']) not in (int,float) or not math.isfinite(value['deadline_at'])):
        raise ValueError('Deadline must be a finite UTC timestamp')
    from dream_rsi.config import ModelConfig,CampaignConfig
    model_value = dict(value.get('model', {}))
    if model_value.get('provider') == 'gemini':
        model_value['provider'] = 'external'
    ModelConfig(**model_value).validate()
    managed={'output_dir','campaign_id','outer_rounds','W','K1','K2','max_response_attempts',
             'round_discovery_calls','round_discovery_budget_unit','fixed_batch_size',
             'exploration_protocol','offline_from_original_root','offline_latest_prefix_only',
             'replay_node_budget','discovery_model','developer_model','history_input'}
    if set(value.get('search',{})) & managed or set(value.get('search',{}))-set(CampaignConfig.__dataclass_fields__):
        raise ValueError('Search overrides contain unknown or controller-managed fields')
    if value.get('offline_tier', 'baseline') not in OFFLINE:
        raise ValueError('Unknown offline tier')
    for scope in [value.get('budget',{}).get('total',{}), *value.get('budget',{}).get('tasks',{}).values()]:
        if set(scope)-{'api_usd','execution_seconds','gpu_seconds'} or any(type(x) not in (int,float) or not math.isfinite(x) or x<0 for x in scope.values()):
            raise ValueError('Invalid budget')
    if value['mode']=='real':
        if not {'api_usd','execution_seconds','gpu_seconds'}<=set(value.get('budget',{}).get('total',{})):
            raise ValueError('Real runs require explicit total API/execution/GPU ceilings')
        for key in ('python_environment','image','grading_python','data_dir'):
            if not host.get(key):
                raise ValueError('Missing host setting: '+key)
        model = value['model']
        if model.get('provider') not in ('openai-compatible', 'gemini') or model.get('wire_api','chat')!='chat' or model.get('stream',False):
            raise ValueError('Provider adapters require non-streaming text requests')
        rates = value['provider']['reservation_rates']
        if any(type(rates.get(k)) not in (int,float) or rates[k]<=0 or not math.isfinite(rates[k]) for k in ('input_usd_per_million','output_usd_per_million')):
            raise ValueError('Positive reservation rates required')
        for t in tasks:
            if not Path(t['public_dir']).is_dir():
                raise ValueError('Missing public task data')
    return value


def load(path, host=None):
    value = read(path)
    if host is not None:
        value['host'] = read(host)
    return validate(value)
