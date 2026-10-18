"""Replay fixed public candidates with 8/4 CPU cores; no LLM calls or private scoring."""
import concurrent.futures, json, os, shutil, subprocess, sys, time
from contextlib import ExitStack
from pathlib import Path
sys.path.insert(0,'/opt/mle-rsi-next/code')
from mle_rsi.common import read,write,lock
parent=Path('/opt/mle-rsi-fixes/runs/lite22-001')
root=Path('/opt/mle-rsi-next/verification/cpu-comparison')
python='/opt/mle-rsi-fixes/controller-env/bin/python'

def trial(task,index):
    choice=read(parent/'rounds/0003'/task/'selected.json')
    original=Path(choice['diagnostics']['original_output_dir']).parent
    results={}
    for cores in (8,4):
        directory=root/task/str(cores);directory.mkdir(parents=True,exist_ok=False)
        for name in ('agent.py','task.json'):shutil.copy2(original/name,directory/name)
        request=read(original/'request.json')
        request.update(root=str(root),directory=str(directory))
        execution=request['spec']['execution']
        for key in ('global_pool','leased_gpu','gpu_lease_fd','candidate_cpu_groups'):execution.pop(key,None)
        execution.update(cpu_ids=list(range(index*8,index*8+cores)),gpu_pool=[index])
        task_data=read(directory/'task.json');task_data['budget']['cpu_cores']=cores;write(directory/'task.json',task_data)
        write(directory/'request.json',request)
        env=dict(os.environ,MLE_GPU_CONFIG_DIR='/opt/mle-rsi-fixes/gpu-configs',PYTHONDONTWRITEBYTECODE='1')
        with (directory/'worker.log').open('w') as log:
            result=subprocess.run([python,'-m','mle_rsi.execute',str(directory/'request.json')],cwd='/opt/mle-rsi-next/code',env=env,stdout=log,stderr=subprocess.STDOUT,timeout=1200)
        if result.returncode:raise RuntimeError('Benchmark worker failed: '+str(directory))
        resources=read(directory/'execution/resources.json')
        results[str(cores)]=resources
        if resources['exit_code']==0 and not resources['timed_out']:
            env.update({k:str(cores) for k in ('OMP_NUM_THREADS','MKL_NUM_THREADS','OPENBLAS_NUM_THREADS')})
            with (directory/'score.log').open('w') as log:
                cmd=['taskset','-c',','.join(map(str,execution['cpu_ids'])),execution['python_environment']+'/bin/python','/opt/mle-rsi-next/code/mle_rsi/validation.py','score',str(root/'prepared'/task),str(directory/'output'),str(directory/'score.json'),'--public',str(Path('/mnt/afs/yangsen1/data/mlebench')/task/'prepared/public')]
                r=subprocess.run(cmd,env=env,stdout=log,stderr=subprocess.STDOUT,timeout=300)
                results[str(cores)]['valid']=r.returncode==0
        else:results[str(cores)]['valid']=False
        write(root/task/'results.json',results)
    ratio=results['4']['wall_seconds']/results['8']['wall_seconds']
    recommendation=4 if results['4']['valid'] and ratio<=1.25 else 8
    return task,dict(results=results,ratio=ratio,recommended_cores=recommendation)

with lock(parent/'controller',blocking=False),ExitStack() as stack:
    assert read(parent/'round-status.json')['state']=='target_reached'
    for cpu in range(128):stack.enter_context(lock(Path('/tmp/mle-rsi-resource-leases')/f'cpu-{cpu}',blocking=False))
    root.mkdir(parents=True,exist_ok=False)
    for name in ('prepared','runtime'):(root/name).symlink_to(parent/name,target_is_directory=True)
    tasks=['aerial-cactus-identification','dog-breed-identification','ranzcr-clip-catheter-line-classification']
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        results=dict(pool.map(lambda x:trial(*x),[(t,i) for i,t in enumerate(tasks)]))
    write(root/'summary.json',dict(at=time.time(),tasks=results,api_calls=0,private_scoring=False))
    print(json.dumps({t:{'ratio':v['ratio'],'recommended_cores':v['recommended_cores']} for t,v in results.items()}))
