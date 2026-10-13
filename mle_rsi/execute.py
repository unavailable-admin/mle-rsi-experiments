"""One isolated candidate; shared GPU leases and cumulative task ceilings."""
import fcntl
import os
import subprocess
import sys
import time
import signal
from pathlib import Path

from . import WORKSPACE
from .common import Budget, LimitReached, read, write
from bench_container import measure_container


def main():
    def stop(signum, frame):
        raise KeyboardInterrupt('Candidate worker cancelled; cleaning up its container')
    signal.signal(signal.SIGTERM,stop)
    request = read(sys.argv[1])
    task, phase, spec = request['task'], request['phase'], request['spec']
    root, directory = Path(request['root']), Path(request['directory'])
    execution = spec['execution']
    cpu = execution['cpu_ids']
    if not set(cpu) <= os.sched_getaffinity(0): raise RuntimeError('Configured CPUs unavailable')
    gpu_task = task in execution['gpu_tasks']
    lease, gpu = None, None
    try:
        if gpu_task:
            for device in execution['gpu_pool']:
                path = WORKSPACE/f'runs/gpu-leases/gpu-{device}.lock'
                path.parent.mkdir(exist_ok=True)
                handle = path.open('a+')
                try: fcntl.flock(handle, fcntl.LOCK_EX|fcntl.LOCK_NB)
                except BlockingIOError:
                    handle.close(); continue
                used=int(subprocess.check_output(['nvidia-smi',f'--id={device}','--query-gpu=memory.used','--format=csv,noheader,nounits'],text=True).strip())
                if used>512:
                    handle.close(); continue
                lease, gpu = handle, device
                break
            if lease is None: raise LimitReached('No free configured GPU; preserving other experiments')
        budget = Budget(root,spec['budget'])
        allowance = budget.execution_allowance(task,phase,gpu is not None)
        # Leave headroom for timeout detection and container cleanup.
        timeout=min(execution['candidate_timeout_seconds'],allowance-5)
        work=directory/'output';work.mkdir()
        task_data=read(directory/'task.json')
        task_data['budget'].update(wall_seconds=timeout,gpu_count=int(gpu is not None))
        write(directory/'task.json',task_data)
        environment=Path(execution['python_environment'])
        pairs=[(environment,environment,True), (WORKSPACE/'envs/mle-lite-agent',WORKSPACE/'envs/mle-lite-agent',True),
               (WORKSPACE/'tools/python',WORKSPACE/'tools/python',True),
               (WORKSPACE/'data/mlebench'/task/'prepared/public',Path('/data'),True),
               (directory/'agent.py',Path('/agent.py'),True),(directory/'task.json',Path('/task.json'),True),
               (work,Path('/output'),False)]
        for source,target in [(execution.get('pretrained_models'),'/models/pretrained'),(str(WORKSPACE/'cache/torch'),'/models/torch')]:
            if source and Path(source).exists():pairs.append((Path(source).resolve(),Path(target),True))
        mounts=[{'Source':str(s),'Target':str(t),'Type':'bind','ReadOnly':ro} for s,t,ro in pairs]
        env={'CONDA_PREFIX':str(environment),'OMP_NUM_THREADS':'8','OPENBLAS_NUM_THREADS':'8',
             'MKL_NUM_THREADS':'8','PYTHONUNBUFFERED':'1','HF_HUB_OFFLINE':'1','HF_DATASETS_OFFLINE':'1',
             'TORCH_HOME':'/models/torch','BENCH_CONTAINER_IMAGE':execution['image']}
        key=str(directory.relative_to(root))
        budget.start_execution(key,task,phase,timeout+5,gpu is not None)
        try:
            result=measure_container([str(environment/'bin/python'),'/agent.py','--task','/task.json'],
                         '/output',env,directory/'execution',mounts,timeout,cpu,gpu)
        except BaseException:
            # Retain the full reservation on unknown execution accounting.
            raise
        budget.finish_execution(key,result['wall_seconds'],result['gpu_allocated_seconds'],
                                exit_code=result['exit_code'],timed_out=result['timed_out'])
    finally:
        if lease:lease.close()


if __name__=='__main__':main()
