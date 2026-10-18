"""Read-only acceptance audit; writes only its own evidence file."""
import json,sys,time,urllib.request
from pathlib import Path
sys.path.insert(0,'/opt/mle-rsi-next/code')
from mle_rsi.common import read,write,sha,lock
from mle_rsi.experiment import frozen,source_manifest
from mle_rsi.rounds import verify_seal
from dream_rsi.storage import CampaignStore
base=Path('/opt/mle-rsi-next');old=Path('/opt/mle-rsi-fixes/runs/lite22-001');new=base/'runs/lite22-medal-001'
result={'at':time.time(),'passed':False,'checks':{}}
checks=result['checks']
def require(name,value):
 checks[name]=bool(value)
 if not value:raise RuntimeError(name)
try:
 require('handoff_complete',read(base/'evidence/handoff.json')['state']=='complete')
 require('parent_round4_terminal',read(old/'round-status.json').get('state')=='target_reached' and read(old/'round-status.json').get('through_round')==4)
 with lock(old/'controller',blocking=False):pass
 checks['parent_controller_released']=True
 config=frozen(new);migration=read(new/'migration.json')
 require('parent_configuration_unchanged',sha(old/'frozen-config.json')==migration['parent_frozen_sha256'])
 require('parent_budget_unchanged',sha(old/'budget.json')==migration['budget_sha256'])
 a,b=read(old/'budget.json'),read(new/'budget.json')
 require('inherited_ledger_entries_identical',all(b[k].get(i)==v for k,rows in a.items() for i,v in rows.items()))
 require('budget_limits_preserved',config['spec']['budget']==read(old/'frozen-config.json')['spec']['budget'])
 require('deadline_preserved',read(new/'round-timing.json')==read(old/'round-timing.json'))
 require('parent_source_unchanged',all(sha(Path('/opt/mle-rsi-fixes/code')/path)==digest for path,digest in migration['parent_source_hashes'].items()))
 benchmark=read(base/'verification/cpu-comparison/summary.json')
 require('native_baselines_valid',all(v['results']['8']['valid'] for v in benchmark['tasks'].values()))
 require('native_core_exceptions_applied',all(config['spec']['execution']['mixed_resources']['task_cpu_cores'].get(t,4)==v['recommended_cores'] for t,v in benchmark['tasks'].items()))
 require('released_source_matches',source_manifest()==read(base/'evidence/release.json')['source_hashes'])
 retired=read(new/'retired-tasks.json');tasks=config['spec']['experiment_tasks'];counts={}
 for task in tasks:
  source=CampaignStore(old/'campaigns'/('dev-'+task),read_only=True)
  target=CampaignStore(new/'campaigns'/('dev-'+task),read_only=True)
  try:
   require('parent_round4_'+task,source.get('checkpoint','active')['completed_rounds']==4 and len(source.list('node'))==100)
   for kind,key,payload in source.connection.execute("SELECT kind,key,payload FROM records WHERE kind IN ('node','round_best','history','policy_version')"):
    require('history_'+task+'_'+kind+'_'+key,target._encoded_record(kind,key)==json.loads(payload))
   counts[task]=len(target.list('node'))
   if task in retired:require('frozen_candidate_count_'+task,counts[task]==25*retired[task].get('activation_round',retired[task]['round']))
  finally:source.close();target.close()
  for root in (old,new):verify_seal(root/'rounds/0004'/task)
 for task,entry in retired.items():
  folder=new/entry['checkpoint'];official=read(folder/'official-result.json')
  require('retirement_evidence_'+task,official['state']=='graded' and official['valid_submission'] and official['any_medal'])
  for name,digest in entry['hashes'].items():require('retirement_hash_'+task+'_'+name,sha(folder/name)==digest)
 require('retired_tasks_no_new_spending',all(v['task'] not in retired or v.get('started_at',0)<=retired[v['task']]['retired_at'] for k,rows in b.items() for i,v in rows.items() if i not in a[k]))
 require('new_candidates_completed',any(n>100 for t,n in counts.items() if t not in retired) or len(retired)==len(tasks))
 http=urllib.request.build_opener(urllib.request.ProxyHandler({}))
 dashboard=json.load(http.open('http://127.0.0.1:18970/report.json',timeout=30))
 require('dashboard_correct_run',dashboard['run_dir']==str(new) and dashboard['retired_count']==len(retired))
 require('no_blocked_tasks',all(r['state']!='blocked' for r in dashboard['rows']))
 require('tree_http_ok',http.open('http://127.0.0.1:18971/',timeout=10).status==200)
 metrics=json.load(http.open('http://127.0.0.1:18972/metrics.json',timeout=10))
 require('resource_monitor_fresh',metrics['run_dir']==str(new) and time.time()-metrics['latest']['at']<30)
 pid=int((new/'controller.pid').read_text())
 require('new_controller_correct_cwd',Path('/proc',str(pid),'cwd').resolve()==base/'code')
 require('new_controller_correct_run',str(new).encode() in Path('/proc',str(pid),'cmdline').read_bytes().split(b'\0'))
 require('fifth_round_running',dashboard['controller_alive'] and dashboard['state'].get('round',0)>=5 and not dashboard['state'].get('blocked'))
 scheduler=read(new/'scheduler.json');active=scheduler['active'];cpus=[c for a in active for c in a['cpus']];gpus=[a['gpu'] for a in active if a['gpu'] is not None]
 require('cpu_disjoint_and_bounded',len(cpus)==len(set(cpus)) and set(cpus)<=set(config['spec']['execution']['cpu_ids']))
 require('gpu_exclusive_and_bounded',len(gpus)==len(set(gpus)) and set(gpus)<=set(config['spec']['execution']['gpu_pool']))
 require('mixed_slot_limit',scheduler.get('policy')=='mixed-4-8' and len(active)<=16)
 require('no_retired_task_active',all(a['task'] not in retired for a in active))
 result.update(passed=True,retired=list(retired),candidate_counts=counts,active=active,ports=[18970,18971,18972],budget=dashboard['budget'])
except Exception as exc:
 result['error']=type(exc).__name__+': '+str(exc)
finally:
 write(base/'evidence/completion-audit.json',result)
 print(json.dumps({'passed':result['passed'],'checks':len(checks),'error':result.get('error')},ensure_ascii=False))
if not result['passed']:sys.exit(1)
