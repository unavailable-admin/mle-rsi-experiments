import os,json,fcntl,socket,time,shutil,subprocess,sys
from pathlib import Path
from mle_rsi import WORKSPACE
from mle_rsi.common import Budget,read,write,sha
from mle_rsi.mount_permissions import readable_input
from bench_container import measure_container
root=Path('/mnt/afs/yangsen1/experiments/lite22-stage2-001/run');base=root.parent/'evidence/cactus-recheck-boundary'
spec=read(root/'frozen-config.json')['spec'];ex=spec['execution'];task='aerial-cactus-identification'
plan=read(base/'recheck-plan.json');assert sha(base/'manifest.json')==plan['manifest_sha256']
public=WORKSPACE/'data/mlebench'/task/'prepared/public'
gpu=7;cpus=list(range(56,64));active=read(root/'scheduler.json')['active'];assert not any(set(cpus)&set(a['cpus']) for a in active)
leasepath=WORKSPACE/f'runs/gpu-leases/{socket.gethostname()}/gpu-{gpu}.lock'
lease=leasepath.open('a+');fcntl.flock(lease,fcntl.LOCK_EX|fcntl.LOCK_NB)
used=int(subprocess.check_output(['nvidia-smi',f'--id={gpu}','--query-gpu=memory.used','--format=csv,noheader,nounits'],text=True).strip());assert used<512
os.environ['MLE_GPU_CONFIG_DIR']=ex['gpu_config_dir'];os.sched_setaffinity(0,cpus)
budget=Budget(root,spec['budget']);results=[]
for c in plan['candidates']:
 name=Path(c['source']).stem;run=base/'retrain-seed0'/name;run.mkdir(parents=True,exist_ok=False)
 assert sha(base/c['source'])==c['sha256'];shutil.copyfile(base/c['source'],run/'agent.py')
 out=run/'output';out.mkdir();os.chown(out,ex['container_output_uid'],ex['container_output_gid'])
 write(run/'task.json',read(Path('/opt/mle-rsi-next/runs/lite22-medal-001/campaigns/dev-aerial-cactus-identification/executions/tree-0001-r0-a0/task.json')))
 for p in [run/'agent.py',run/'task.json',base/'manifest.json',base/'split.csv']:readable_input(p)
 pairs=[(public,'/data',True),(root/'runtime','/runtime',True),(run/'agent.py','/agent.py',True),(run/'task.json','/task.json',True),(base/'manifest.json','/validation/manifest.json',True),(base/'split.csv','/validation/split.csv',True),(out,'/output',False)]
 for p in [ex['python_environment'],WORKSPACE/'tools/python',*ex.get('python_extra_mounts',[])]:pairs.append((p,str(p),True))
 mounts=[{'Source':str(s),'Target':t,'Type':'bind','ReadOnly':ro} for s,t,ro in pairs]
 env={'CONDA_PREFIX':ex['python_environment'],'BENCH_CONTAINER_IMAGE':ex['image'],'OMP_NUM_THREADS':'8','OPENBLAS_NUM_THREADS':'8','MKL_NUM_THREADS':'8','PYTHONUNBUFFERED':'1'}
 key='stage2:cactus-public-recheck:split20261017:seed0:attempt2:'+name
 timeout=budget.reserve_execution(key,task,'development',900,True);assert timeout==900
 print('START',name,flush=True)
 res=measure_container([ex['python_environment']+'/bin/python','/runtime/mle_runtime.py','/agent.py'],'/output',env,run/'execution',mounts,timeout,cpus,gpu)
 budget.finish_execution(key,res['wall_seconds'],res['gpu_allocated_seconds'],exit_code=res['exit_code'],timed_out=res['timed_out'],purpose='public_split_recheck')
 write(run/'resources.json',res)
 assert res['exit_code']==0,res
 command=[ex['python_environment']+'/bin/python','-c','import json,sys;from mle_rsi.validation import score;print(json.dumps(score(sys.argv[1],sys.argv[2],sys.argv[3])))',str(base),str(out),str(public)]
 assessment=json.loads(subprocess.check_output(command,text=True));write(run/'assessment.json',assessment)
 results.append({'source':c['source'],'source_sha256':c['sha256'],'training_seed':0,'resources':res,'assessment':assessment})
 write(base/'retrain-seed0/results.json',results)
 print('DONE',name,json.dumps(assessment),flush=True)
write(base/'retrain-seed0/completed.json',{'at':time.time(),'runs':len(results),'paid_api_calls':0,'private_grading_calls':0,'budget_ledger':str(root/'budget.json')})
