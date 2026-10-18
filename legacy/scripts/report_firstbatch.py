"""Read-only result audit and comparison; never launches or regrades candidates."""
import argparse
import asyncio
from collections import Counter
import hashlib
import json
from pathlib import Path
import sqlite3
import time


def read(path):return json.loads(path.read_text())
def sha(path):
    digest=hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda:handle.read(1024*1024),b''):digest.update(chunk)
    return digest.hexdigest()


def repair_outcomes(campaign):
    """Read committed search edges, preserving actual parent and child evidence."""
    database=campaign/'campaign.sqlite'
    if not database.exists():return []
    def resolve(value):
        return read(campaign/value['path']) if isinstance(value,dict) and '$ref' in value else value
    with sqlite3.connect(f'file:{database}?mode=ro',uri=True) as connection:
        nodes=[resolve(json.loads(row[0])) for row in connection.execute("SELECT payload FROM records WHERE kind='node'")]
    by_id={node['node_id']:node for node in nodes};rows=[]
    for node in nodes:
        parent=by_id.get(node['parent_id'])
        if parent is None:continue
        before=resolve(parent['evaluation']);after=resolve(node['evaluation'])
        if before['valid']:continue
        attempt=resolve(node['attempt']);prior_attempt=resolve(parent['attempt'])
        rows.append({'parent':parent['node_id'],'child':node['node_id'],
            'parent_attempt':prior_attempt['attempt_id'],'child_attempt':attempt['attempt_id'],
            'parent_failure':before['fail_class']['value'],'child_stage':attempt['stage']['value'],
            'child_valid':after['valid'],'child_failure':after['fail_class']['value'],
            'child_metric':after.get('metrics',{})})
    return rows


def historical_phases(root):
    phases={}
    for phase,prefix in [('development','dev'),('final','final')]:
        outcomes=Counter();seconds=0;gpu_seconds=0;tasks={}
        for campaign in sorted((root/'campaigns').glob(prefix+'-*')):
            counts=Counter();elapsed=0;programs=set();submissions=set();valid_scores=set()
            for path in campaign.glob('executions/*/assessment.json'):
                a=read(path);counts[a['fail_class']]+=1
                resources=a.get('diagnostics',{}).get('resources',{})
                elapsed+=resources.get('wall_seconds',0)
                gpu_seconds+=resources.get('gpu_allocated_seconds',0)
                if (path.parent/'agent.py').exists():programs.add(sha(path.parent/'agent.py'))
                if a['valid']:
                    valid_scores.add(a['metric']['validation_score'])
                    output=path.parent/'output/submission.csv'
                    recorded=a.get('diagnostics',{}).get('submission_sha256')
                    if recorded:submissions.add(recorded)
                    elif output.exists():submissions.add(sha(output))
            outcomes.update(counts);seconds+=elapsed
            tasks[campaign.name[len(prefix)+1:]]={'outcomes':dict(counts),'candidate_seconds':elapsed,
                'distinct_programs':len(programs),'distinct_valid_scores':len(valid_scores),'distinct_valid_submissions':len(submissions)}
        phases[phase]={'outcomes':dict(outcomes),'attempts':sum(outcomes.values()),
            'candidate_seconds':seconds,'gpu_allocated_seconds':gpu_seconds,'tasks':tasks}
    return phases


def report(root, baseline):
    config=read(root/'frozen-config.json');spec=config['spec'];state=read(root/'supervisor.json')
    summary=read(root/'summary.json');previous=read(baseline/'summary.json')
    old={r['task']:r for r in previous['rows']};rows=[];checks=[];phases={}
    for phase,prefix,expected in [('development','dev',spec['search']['development_rounds']*spec['search']['batches_per_round']*spec['search']['workers']),('final','final',spec['search']['final_batches']*spec['search']['workers'])]:
        counters=Counter();seconds=0;gpu=0;task_resources={}
        tasks=spec['development_tasks'] if phase=='development' else config['final_tasks']
        for task in tasks:
            campaign=root/'campaigns'/f'{prefix}-{task}'
            assessments=list(campaign.glob('executions/*/assessment.json'))
            task_counter=Counter();task_seconds=0;task_cpu=0;task_gpu=0;gpu_active=0
            valid_scores=set();submissions=set();programs=set();best_candidate=None
            result_path=root/'outcomes'/phase/task/'result.json'
            result=read(result_path) if result_path.exists() else {}
            checks.append({'check':f'{phase}/{task}: complete candidate budget','passed':result.get('state')=='completed' and len(assessments)==expected,'attempts':len(assessments),'expected':expected})
            for path in assessments:
                a=read(path);counters[a['fail_class']]+=1;r=a.get('diagnostics',{}).get('resources',{})
                if (path.parent/'agent.py').exists():programs.add(sha(path.parent/'agent.py'))
                seconds+=r.get('wall_seconds',0);gpu+=r.get('gpu_allocated_seconds',0)
                task_counter[a['fail_class']]+=1
                task_seconds+=r.get('wall_seconds',0);task_cpu+=r.get('sampled_cpu_seconds',0)
                task_gpu+=r.get('gpu_allocated_seconds',0)
                gpu_active+=int(r.get('sampled_peak_process_vram_bytes',0)>0)
                if a['valid']:
                    if best_candidate is None or a['score']>best_candidate['quality_score']:
                        best_candidate={'attempt':path.parent.name,'quality_score':a['score'],'public_metric':a['metric']['validation_score'],'program_sha256':sha(path.parent/'agent.py')}
                    valid_scores.add(a['metric']['validation_score'])
                    submissions.add(a['diagnostics']['submission_sha256'])
                    independent=a.get('diagnostics',{}).get('independent_validation',{})
                    checks.append({'check':f'{phase}/{task}/{path.parent.name}: independent score','passed':independent.get('independently_scored') is True and independent.get('validation_score')==a['metric']['validation_score']})
                    if spec.get('mandatory_bounded_repair'):
                        checks.append({'check':f'{phase}/{task}/{path.parent.name}: public submission schema','passed':independent.get('submission_contract',{}).get('public_schema_checked') is True})
                    checks.append({'check':f'{phase}/{task}/{path.parent.name}: fixed manifest','passed':independent.get('manifest_sha256')==sha(root/'prepared'/task/'manifest.json')})
                    checks.append({'check':f'{phase}/{task}/{path.parent.name}: selected artifact intact','passed':a['diagnostics'].get('submission_sha256')==sha(path.parent/'output/submission.csv')})
            task_resources[task]={'outcomes':dict(task_counter),'candidate_seconds':task_seconds,
                'sampled_cpu_seconds':task_cpu,'gpu_allocated_seconds':task_gpu,'candidates_with_observed_vram':gpu_active,
                'distinct_programs':len(programs),'distinct_valid_scores':len(valid_scores),'distinct_valid_submissions':len(submissions),
                'best_candidate':best_candidate,'repairs':repair_outcomes(campaign)}
        phases[phase]={'outcomes':dict(counters),'attempts':sum(counters.values()),'candidate_seconds':seconds,'gpu_allocated_seconds':gpu,'tasks':task_resources}
    for row in summary['rows']:
        task=row['task'];prior=old[task]
        def official(r):
            reports=r.get('official_report',{}).get('competition_reports',[])
            return reports[0] if reports else {}
        current=official(row);before=official(prior)
        rows.append({'task':task,'state':row['state'],'valid':row['valid_submission'],'medal':row['any_medal'],
            'score':current.get('score'),'bronze':current.get('bronze_threshold'),'lower_is_better':current.get('is_lower_better'),
            'previous_score':before.get('score'),'previous_valid':prior['valid_submission'],'previous_medal':prior['any_medal']})
        if row['state']=='graded':
            checks.append({'check':task+': official submission exists','passed':Path(current['submission_path']).is_file()})
    ledger=read(root/'budget.json');api=list(ledger.get('api',{}).values())
    charged=sum(r.get('charged_usd',0) for r in api);reserved=sum(r.get('reserved_usd',0) for r in api if 'charged_usd' not in r)
    checks.append({'check':'API total cap including unknown reservations','passed':charged+reserved<=spec['budget']['api_usd_total']+1e-8})
    checks.append({'check':'All API requests reconciled','passed':all('charged_usd' in r for r in api)})
    freeze=read(root/'policy-freeze.json') if (root/'policy-freeze.json').exists() else {}
    checks.append({'check':'Frozen policy unchanged, private scores not used for selection','passed':freeze.get('private_scores_used') is False and freeze.get('policy_sha256')==sha(root/'frozen-policy.py') if (root/'frozen-policy.py').exists() else False})
    for name,expected in config['source_hashes'].items():
        checks.append({'check':'Source unchanged: '+name,'passed':sha(Path(__file__).resolve().parents[1]/name)==expected})
    for name,expected in config.get('asset_hashes',{}).items():
        asset=root/name
        checks.append({'check':'Runtime/validation asset unchanged: '+name,'passed':sha(asset)==expected})
        if asset.name=='manifest.json':
            for relative,wanted in read(asset).get('file_hashes',{}).items():
                checks.append({'check':'Prepared data unchanged: '+str(asset.parent.relative_to(root)/relative),
                    'passed':sha(asset.parent/relative)==wanted})
    lineage=read(root/'authorization-and-lineage.json')
    checks.append({'check':'Previous budget ledger unchanged','passed':sha(baseline/'budget.json')==lineage['previous_budget_sha256']})
    combined_accounting=None
    if lineage.get('pilot_run'):
        pilot=Path(lineage['pilot_run']);pilot_rows=list(read(pilot/'budget.json').get('api',{}).values())
        pilot_accounted=sum(r.get('charged_usd',r.get('reserved_usd',0)) for r in pilot_rows)
        combined_accounting={'pilot_accounted_usd':pilot_accounted,'corrected_accounted_usd':charged+reserved,
            'combined_accounted_usd':pilot_accounted+charged+reserved,'ceiling_usd':lineage['combined_new_api_ceiling_usd']}
        checks.append({'check':'Pilot ledger preserved','passed':sha(pilot/'budget.json')==lineage['pilot_budget_sha256']})
        checks.append({'check':'Combined new experiment API cap including reservations','passed':combined_accounting['combined_accounted_usd']<=combined_accounting['ceiling_usd']+1e-8})
    if spec.get('mandatory_bounded_repair'):
        from audit_firstbatch_policy import audit
        probe=asyncio.run(audit((root/'frozen-policy.py').read_text(),guard=True))
        (root/'effective-policy-behavior.json').write_text(json.dumps(probe,indent=2)+'\n')
        checks.append({'check':'Frozen selection used mandatory host repair guard','passed':freeze.get('bounded_repair_guard') is True})
        checks.append({'check':'Effective frozen policy repair behavior probes','passed':probe['all_probe_checks_passed']})
        for phase,phase_result in phases.items():
            for task,task_result in phase_result['tasks'].items():
                repairs=task_result['repairs'];parents=[r['parent'] for r in repairs]
                failed_children={r['child'] for r in repairs if not r['child_valid']}
                checks.append({'check':f'{phase}/{task}: actual repairs once per parent, debug stage, no failed-debug chains',
                    'passed':len(parents)==len(set(parents)) and not failed_children.intersection(parents) and all(r['child_stage']=='debug' for r in repairs)})
    timing=read(root/'phase-timing.json');start=timing['development']['started_at'];final_start=timing.get('final',{}).get('started_at');end=state.get('finished_at')
    durations={'development_and_freeze_seconds':final_start-start if final_start else None,'final_seconds':end-final_start if end and final_start else None,'total_seconds':end-start if end else None}
    value={'checked_at':time.time(),'state':state['state'],'checks':checks,'all_checks_passed':all(c['passed'] for c in checks),
        'phases':phases,'previous_phases':historical_phases(baseline),'groups':summary['groups'],'previous_groups':previous['groups'],'api_charged_usd':charged,'api_reserved_usd':reserved,'api_requests':len(api),
        'durations':durations,'combined_accounting':combined_accounting,'tasks':rows,'limitations':['Package comparison, not a single-factor causal attribution.','Some public splits differ from the previous candidate-owned splits.','Public metric recomputation verifies outputs, not arbitrary training-code isolation from validation labels.','Single seed; final tasks have prior observations.']}
    (root/'firstbatch-audit.json').write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
    lines=['第一批修复实验结果',str(root),'状态: '+state['state'],f'核验: {sum(c["passed"] for c in checks)}/{len(checks)}',
        f'API 已结算 ${charged:.6f}; 未决预留 ${reserved:.6f}; 请求 {len(api)}',
        '耗时: '+json.dumps(durations),'本轮: '+json.dumps(summary['groups']),'上一轮: '+json.dumps(previous['groups']),
        '候选执行: '+json.dumps(phases),'任务\t当前分数\t上一轮分数\t当前奖牌\t上一轮奖牌']
    lines += [f'{r["task"]}\t{r["score"]}\t{r["previous_score"]}\t{r["medal"]}\t{r["previous_medal"]}' for r in rows]
    lines += ['限制: '+text for text in value['limitations']]
    (root/'results-zh.txt').write_text('\n'.join(lines)+'\n')
    return value


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('root',type=Path);p.add_argument('--baseline',type=Path,default=Path('/mnt/afs/yangsen1/runs/dream-rsi-gpt-halfday-001'));args=p.parse_args()
    result=report(args.root,args.baseline)
    print(json.dumps({k:result[k] for k in ['state','all_checks_passed','groups','api_charged_usd','api_reserved_usd','durations']},ensure_ascii=False,indent=2))
