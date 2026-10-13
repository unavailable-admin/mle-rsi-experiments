"""Build native Dream-RSI campaigns, then freeze a policy across development worlds."""
import asyncio
import json
import math
import subprocess
import sys
import time
from dataclasses import replace
from pathlib import Path

from . import REPO, WORKSPACE
from .backend import MLEBackend, task_ids
from .common import LimitReached, read, sha, write
from .provider import GeminiTransport, load_key
from dream_rsi.campaign import Campaign
from dream_rsi.codec import digest, plain
from dream_rsi.config import (BenchmarkProtocol, BudgetConfig, CampaignConfig, HistoryInputConfig,
                              ModelConfig, ResourceLimits)
from dream_rsi.errors import BudgetExceeded, ExternalUnknown
from dream_rsi.policy import best_first_policy_source
from dream_rsi.replay import PoolEvaluator, objective_from, replay_settings
from dream_rsi.storage import CampaignStore
from dream_rsi.tree import DiscoveryTree


def source_manifest():
    paths=list((REPO/'mle_rsi').glob('*.py'))
    paths+=list((REPO/'third_party/dream-rsi/my_rsi/src').rglob('*.py'))
    paths+=list((REPO/'third_party/dream-rsi/my_rsi/prompts').glob('*.txt'))
    paths+=list((REPO/'third_party/rsi-eval/src').glob('*.py'))
    paths+=list((REPO/'examples').glob('*.py'))
    return {str(p.relative_to(REPO)):sha(p) for p in sorted(paths)}


def initialize(root, spec, mode='real'):
    root=Path(root)
    root.mkdir(parents=True,exist_ok=False)
    if len(spec['development_tasks']) != len(set(spec['development_tasks'])) or not set(spec['development_tasks']) <= set(task_ids()):
        raise ValueError('Invalid development task list')
    # Separate verification runs can never be resumed as paid experiments.
    write(root/'frozen-config.json',{'spec':spec,'mode':mode,'created_at':time.time(),
          'source_hashes':source_manifest(),'final_tasks':task_ids(),
          'submodules':{name:subprocess.check_output(['git','rev-parse','HEAD'],cwd=REPO/'third_party'/name,text=True).strip()
                        for name in ['dream-rsi','rsi-eval']}})
    return root


def frozen(root):
    value=read(Path(root)/'frozen-config.json')
    if value['source_hashes']!=source_manifest():
        raise RuntimeError('Sources changed after initialization; use a new run directory')
    return value


def campaign(root, task, phase, *, initial_source=None):
    root=Path(root);record=frozen(root);spec=record['spec'];mode=record['mode']
    s=spec['search'];model=spec['model']
    is_dev=phase=='development'
    prefix='dev' if is_dev else 'final'
    cid=f'{prefix}-{task}'
    mock_model=mode!='real'
    mc=ModelConfig(provider='mock' if mock_model else 'openai-compatible',
                   base_url=model['base_url']+'/v1',model='mock-mle-verification' if mock_model else model['name'],
                   api_key_env=model['api_key_env'],reasoning_effort=model['reasoning_effort'],temperature=1,
                   max_output_tokens=model['discovery_output_tokens'],context_tokens=512000,
                   tokenizer='conservative-utf8',timeout_s=900,timeout_attempts=1)
    batches=s['batches_per_round'] if is_dev else s['final_batches']
    cfg=CampaignConfig(campaign_id=cid,output_dir=str(root/'campaigns'),
          outer_rounds=s['development_rounds'] if is_dev else 1,W=s['workers'],K1=batches,
          K2=s['replay_steps'],M=s['policy_versions_per_round'] if is_dev else 1,
          master_seed=0,max_response_attempts=1,round_discovery_calls=batches*s['workers'],
          round_discovery_budget_unit='nodes',fixed_batch_size=True,
          fixed_exploration=not is_dev,exploration_protocol='branching-workspaces-v3',
          offline_from_original_root=True,offline_latest_prefix_only=True,any_node_parents=True,
          initial_policy='best-first',initial_policy_width=s['workers'],replay_missing_action='virtual',
          replay_virtual_model='calibrated',replay_virtual_depth=1,replay_node_budget=batches*s['workers'],
          replay_objective='root_gain',replay_scoring='anytime_auc',replay_imputed_weight=1.,
          replay_beta_mode='policy_default',replay_free_refill=False,
          replay_virtual_per_parent=2,replay_virtual_ratio=1.,replay_virtual_max_gain=1.1,
          scheduling_betas=(.6,),replay_scenarios=s['replay_scenarios'],replay_parallelism=1,
          persistent_policy_workers=True,policy_decision_timeout_s=8,policy_memory_bytes=1024**3,
          policy_view_diagnostics='summary',beta1=.005,beta2=.0025,
          benchmark=BenchmarkProtocol(profile='smoke',timing_runs=1),
          history_input=HistoryInputConfig(mode='evidence',discovery_tokens=150000,developer_tokens=200000),
          discovery_model=mc,developer_model=replace(mc,max_output_tokens=model['developer_output_tokens']),
          budget=BudgetConfig(max_discovery_calls=100,max_developer_calls=30,max_tokens=3000000))
    backend=MLEBackend(task,root,spec,phase,mock=mode=='mock')
    def discovery_responder(messages,request_id):
        if mode=='smoke':
            code=(REPO/'examples/leaf_smoke.py').read_text()
        else:
            code=f'# MOCK verification candidate {request_id}\nprint("not a real ML result")\n'
        return json.dumps({'proposal':'Verification-only candidate','source':code,'language':'python',
                           'mechanism_tags':['verification']})
    def developer_responder(messages,request_id):
        return json.dumps({'source':best_first_policy_source(width=1),'change_note':'Mock revision: narrow batch policy'})
    c=Campaign(cfg,task_backend=backend,initial_policy_source_override=initial_source,
               discovery_responder=discovery_responder if mock_model else None,
               developer_responder=developer_responder if mock_model else None)
    if not mock_model:
        load_key(spec)
        transport=GeminiTransport(root,spec,task,phase,cid)
        c.agent.client.transport=transport
        c.developer.client.transport=transport
    return c


def best_candidate(c):
    nodes={}
    for _,payload in c.store.list('tree'):
        for n in DiscoveryTree.from_payload(payload).nodes:
            if n.parent_id is not None and n.evaluation.valid:nodes[n.node_id]=n
    return max(nodes.values(),key=lambda n:n.score,default=None)


async def run_task(root, task, phase, policy=None):
    root=Path(root);out=root/'outcomes'/phase/task
    if (out/'result.json').exists():return read(out/'result.json')
    c=campaign(root,task,phase,initial_source=policy)
    result={'task':task,'phase':phase,'state':'running','started_at':time.time()}
    write(out/'status.json',result)
    try:
        try:
            await c.run()
            result['state']='completed'
        except (LimitReached,BudgetExceeded,ExternalUnknown) as exc:
            result.update(state='stopped',reason=f'{type(exc).__name__}: {exc}')
        best=best_candidate(c)
        result.update(finished_at=time.time(),completed_rounds=(c.checkpoints.load(c.config.campaign_id) or {}).get('completed_rounds',0),
                      candidate_nodes=len(c.store.list('node')),usage=c.ledger.summary())
        if best is not None:
            choice={'node_id':best.node_id,'score':best.score,'source_hash':best.artifact.source_hash,
                    'metrics':best.evaluation.metrics,'diagnostics':best.evaluation.diagnostics,
                    'selection_basis':'public validation only','selected_at':time.time()}
            write(out/'selected.json',choice)
            result['selected']=choice
        else:result['selected']=None
        write(out/'result.json',result)
        return result
    finally:c.store.close()


async def develop(root):
    spec=frozen(root)['spec']
    results=[]
    for task in spec['development_tasks']:
        print('DEVELOP',task,flush=True)
        result=await run_task(root,task,'development')
        results.append(result)
        print('RESULT',task,result['state'],'rounds='+str(result['completed_rounds']),result.get('reason',''),flush=True)
        api_rows=read(root/'budget.json').get('api',{}).values() if (root/'budget.json').exists() else []
        if any(r['state']=='uncertain' for r in api_rows):
            print('STOP: unresolved API outcome; later tasks were not started. Inspect budget.json.',flush=True)
            break
    write(Path(root)/'development.json',results)
    return results


async def freeze_policy(root):
    root=Path(root);record=frozen(root);spec=record['spec']
    if (root/'policy-freeze.json').exists():return read(root/'policy-freeze.json')
    cases=[];sources={}
    try:
        for task in spec['development_tasks']:
            result=read(root/'outcomes/development'/task/'result.json')
            if result['completed_rounds']<spec['search']['development_rounds']:
                raise RuntimeError(f'{task}: full online/offline development did not complete; do not silently freeze a partial experiment')
            c=campaign(root,task,'development');cases.append((task,c))
            for _,p in c.store.list('policy'):
                if p.status=='valid' and p.evaluation is not None:sources[digest(p.source)]=p.source
        initial=best_first_policy_source(width=spec['search']['workers'])
        sources[digest(initial)]=initial
        rows=[]
        for key,source in sorted(sources.items()):
            scores={};valid=True
            for task,c in cases:
                cp=c.checkpoints.load(c.config.campaign_id);pool=c._pool(cp)
                ev=PoolEvaluator(objective_from(c.config,cp['beta1'],cp['beta2']),c.config.W,c.store,c.policy_limits,
                                 persistent_policy_workers=True,fixed_batch_size=c.config.fixed_batch_size,**replay_settings(c.config))
                bundle=await ev.evaluate(source,pool,c.config.K2)
                if bundle.mean_V is None:valid=False
                scores[task]=bundle.mean_V
            rows.append({'source_hash':key,'scores':scores,'mean_replay_value':sum(scores.values())/len(scores) if valid else None})
        eligible=[r for r in rows if r['mean_replay_value'] is not None]
        if not eligible:raise RuntimeError('No policy passed all four development replay worlds')
        chosen=max(eligible,key=lambda r:(r['mean_replay_value'],r['source_hash']==digest(initial)))
        path=root/'frozen-policy.py';path.write_text(sources[chosen['source_hash']])
        result={'created_at':time.time(),'policy_sha256':sha(path),'chosen':chosen,'candidates':rows,
                'selection':'equal mean replay value across development tasks; only public-validation histories',
                'private_scores_used':False,'mode':record['mode'],
                'development_tasks':spec['development_tasks'],'final_tasks':record['final_tasks']}
        write(root/'policy-freeze.json',result)
        return result
    finally:
        for _,c in cases:c.store.close()


def grade_selected(root,task,result):
    root=Path(root);record=frozen(root);out=root/'outcomes/final'/task
    report=out/'official-result.json'
    if report.exists():return read(report)
    if record['mode']!='real':
        value={'task':task,'state':'verification_only','valid_submission':False,'any_medal':False,
               'note':'Mock/smoke verification never claims official results'}
    elif not result['selected']:
        value={'task':task,'state':'no_submission','valid_submission':False,'any_medal':False}
    else:
        choice=read(out/'selected.json')
        submission=Path(choice['diagnostics']['output_dir'])/'submission.csv'
        if sha(submission)!=choice['diagnostics']['submission_sha256']:raise RuntimeError('Selected submission changed')
        entries=out/'submissions.jsonl'
        entries.write_text(json.dumps({'competition_id':task,'submission_path':str(submission)})+'\n')
        grading=out/'grading'
        if grading.exists():raise RuntimeError('Previous grading interrupted; inspect before rerunning')
        from bench_resources import measure
        r=measure([str(WORKSPACE/'envs/mlebench/bin/python'),str(REPO/'third_party/rsi-eval/src/mle_grade.py'),
                   'grade','--submission',str(entries),'--output-dir',str(grading),
                   '--data-dir',str(WORKSPACE/'data/mlebench')],WORKSPACE,__import__('os').environ.copy(),
                   out/'grading-resources',timeout=1800,cpu_ids=record['spec']['execution']['cpu_ids'])
        files=list(grading.glob('*_grading_report.json'))
        if r['exit_code'] or not files:
            value={'task':task,'state':'grading_failed','valid_submission':False,'any_medal':False}
        else:
            official=read(files[0])
            # Preserve the official schema and expose only report-time aggregates.
            details=official.get('competition_reports',[])
            value={'task':task,'state':'graded','valid_submission':official['total_valid_submissions']==1,
                   'any_medal':official.get('total_medals',0)>0,'official_report':official}
            # Official mlebench currently uses submissions: [{...medal flags...}].
            def flags(obj):
                if isinstance(obj,dict):
                    return obj.get('any_medal') is True or any(flags(v) for v in obj.values())
                return isinstance(obj,list) and any(flags(v) for v in obj)
            value['any_medal']=flags(official)
    write(report,value)
    return value


async def final_evaluate(root,tasks=None):
    root=Path(root);record=frozen(root);selection=read(root/'policy-freeze.json')
    path=root/'frozen-policy.py'
    if sha(path)!=selection['policy_sha256']:raise RuntimeError('Frozen policy changed')
    selected=tasks or record['final_tasks']
    if not set(selected)<=set(record['final_tasks']):raise ValueError('Task outside frozen Lite22 list')
    for task in selected:
        print('FINAL',task,flush=True)
        result=await run_task(root,task,'final',path.read_text())
        grade_selected(root,task,result)
        summarize(root)


def summarize(root):
    root=Path(root);record=read(root/'frozen-config.json');dev=set(record['spec']['development_tasks'])
    rows=[]
    for task in record['final_tasks']:
        p=root/'outcomes/final'/task/'official-result.json'
        rows.append(read(p) if p.exists() else {'task':task,'state':'not_completed','valid_submission':False,'any_medal':False})
    groups={}
    for name,group in [('development',[r for r in rows if r['task'] in dev]),
                       ('remaining',[r for r in rows if r['task'] not in dev]),('all',rows)]:
        n=len(group)
        groups[name]={'tasks':n,'completed':sum(r['state']!='not_completed' for r in group),
                      'valid_submissions':sum(r['valid_submission'] for r in group),
                      'medals':sum(r['any_medal'] for r in group),
                      'medal_rate':sum(r['any_medal'] for r in group)/n if n else None}
    value={'mode':record['mode'],'groups':groups,'rows':rows,'updated_at':time.time(),
           'notes':record['spec']['protocol_notes']}
    if (root/'budget.json').exists():value['budget']=read(root/'budget.json')
    write(root/'summary.json',value)
    return value
