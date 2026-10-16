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
        if gpu_task and execution.get('global_pool'):
            gpu = execution['leased_gpu']
            fd = execution['gpu_lease_fd']
            expected = (WORKSPACE/f'runs/gpu-leases/{__import__("socket").gethostname()}/gpu-{gpu}.lock').resolve()
            if gpu not in execution['gpu_pool'] or Path(os.readlink(f'/proc/self/fd/{fd}')).resolve() != expected:
                raise RuntimeError('Invalid inherited GPU lease')
        elif gpu_task:
            for device in execution['gpu_pool']:
                path = WORKSPACE/f'runs/gpu-leases/{__import__("socket").gethostname()}/gpu-{device}.lock'
                path.parent.mkdir(parents=True, exist_ok=True)
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
        key=str(directory.relative_to(root))
        parallel = bool(execution.get('candidate_cpu_groups') or execution.get('global_pool'))
        if parallel:
            timeout = budget.reserve_execution(key,task,phase,execution['candidate_timeout_seconds'],gpu is not None)
        else:
            allowance = budget.execution_allowance(task,phase,gpu is not None)
            timeout=min(execution['candidate_timeout_seconds'],allowance-5)
        work=directory/'output';work.mkdir()
        # This host's FUSE AFS mount cannot use id-mapped writable bind mounts.
        # Only the newly created candidate output belongs to the mapped root;
        # data, code, environments and all other run paths retain their owners.
        if execution.get('container_output_uid') is not None:
            os.chown(work, execution['container_output_uid'], execution['container_output_gid'])

        task_data=read(directory/'task.json')
        task_data['budget'].update(wall_seconds=timeout,gpu_count=int(gpu is not None))
        write(directory/'task.json',task_data)
        environment=Path(execution['python_environment'])
        public=WORKSPACE/'data/mlebench'/task/'prepared/public'
        prepared=root/'prepared'/task
        validation=read(prepared/'manifest.json') if spec.get('first_batch_fixes') else None
        if validation and validation.get('sanitized_public'):public=prepared/validation['sanitized_public']
        pairs=[(environment,environment,True),
               (WORKSPACE/'tools/python',WORKSPACE/'tools/python',True),
               (public,Path('/data'),True),
               (directory/'agent.py',Path('/agent.py'),True),(directory/'task.json',Path('/task.json'),True),
               (work,Path('/output'),False)]
        for dependency in execution.get('python_extra_mounts',[]):
            path=Path(dependency).resolve()
            if not path.is_dir():raise RuntimeError('Missing Python dependency mount: '+str(path))
            pairs.append((path,path,True))
        if validation:
            pairs.append((root/'runtime',Path('/runtime'),True))
            pairs.append((prepared/'manifest.json',Path('/validation/manifest.json'),True))
            if validation['split_file']:pairs.append((prepared/'split.csv',Path('/validation/split.csv'),True))
            for target,source in validation.get('data_overrides',{}).items():
                pairs.append((prepared/source,Path('/data')/target,True))
        for source,target in [(execution.get('pretrained_models'),'/models/pretrained'),(execution.get('torch_cache',str(WORKSPACE/'cache/torch')),'/models/torch')]:
            if source and Path(source).exists():pairs.append((Path(source).resolve(),Path(target),True))
        mounts=[{'Source':str(s),'Target':str(t),'Type':'bind','ReadOnly':ro} for s,t,ro in pairs]
        env={'CONDA_PREFIX':str(environment),'OMP_NUM_THREADS':str(len(cpu)),'OPENBLAS_NUM_THREADS':str(len(cpu)),
             'MKL_NUM_THREADS':str(len(cpu)),'PYTHONUNBUFFERED':'1','HF_HUB_OFFLINE':'1','HF_DATASETS_OFFLINE':'1',
             'TORCH_HOME':'/models/torch','BENCH_CONTAINER_IMAGE':execution['image']}
        key=str(directory.relative_to(root))
        if not parallel:
            budget.start_execution(key,task,phase,timeout+5,gpu is not None)
        try:
            command=[str(environment/'bin/python'),'/runtime/mle_runtime.py','/agent.py'] if validation else [str(environment/'bin/python'),'/agent.py','--task','/task.json']
            result=measure_container(command,
                         '/output',env,directory/'execution',mounts,timeout,cpu,gpu)
        except BaseException:
            # Retain the full reservation on unknown execution accounting.
            raise
        budget.finish_execution(key,result['wall_seconds'],result['gpu_allocated_seconds'],
                                exit_code=result['exit_code'],timed_out=result['timed_out'])
    finally:
        if lease:lease.close()


if __name__=='__main__':main()
