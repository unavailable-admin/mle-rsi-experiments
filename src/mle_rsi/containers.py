"""Isolated candidate execution and cgroup resource sampling for benchmark adapters."""
import json
import os
from pathlib import Path
import subprocess
import time
import docker

from .io import write


def kill_if_running(container):
    """An exit racing our timeout is normal; other daemon errors remain errors."""
    try:
        container.kill()
    except docker.errors.APIError:
        container.reload()
        if container.status not in ('exited', 'dead'):
            raise

def measure_container(command,cwd,env,output,mounts,timeout=14400,cpu_ids=None,gpu=None,memory='32g',gpu_config_dir=None):
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    cfg={'runtime':'sysbox-runc','nano_cpus':8_000_000_000,'shm_size':'4G','mounts':[]}
    if gpu is not None:
        if gpu_config_dir is None:
            raise ValueError('GPU execution requires an explicit gpu_config_dir')
        cfg=json.loads((Path(gpu_config_dir)/f'sysbox-gpu{gpu}.json').read_text())
        cfg['nano_cpus']=8_000_000_000
    cfg['mounts']+=mounts
    cfg['nano_cpus']=len(cpu_ids or [0])*1_000_000_000
    cfg['shm_size']=env.get('BENCH_SHM_SIZE',cfg.get('shm_size','4G'))
    if cpu_ids:cfg['cpuset_cpus']=','.join(map(str,cpu_ids))
    # Deliberately do not copy host credentials/proxies into candidate containers.
    allowed=['CONDA_PREFIX','OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS',
             'SCIKIT_LEARN_DATA','HF_HOME','TORCH_HOME','PYTHONUNBUFFERED','HF_HUB_OFFLINE','HF_DATASETS_OFFLINE']
    clean={k:env[k] for k in allowed if k in env}
    clean['PATH']=str(Path(env['CONDA_PREFIX'])/'bin')+':/usr/local/bin:/usr/bin:/bin'
    clean['CUDA_VISIBLE_DEVICES']=''
    if gpu is not None:
        clean['CUDA_VISIBLE_DEVICES']=subprocess.check_output(['nvidia-smi',f'--id={gpu}','--query-gpu=uuid','--format=csv,noheader'],text=True).strip()
    client=docker.from_env();started=time.monotonic();timed_out=False
    peak_memory=0;peak_vram=0;cpu_seconds=0;samples=0
    image=env.get('BENCH_CONTAINER_IMAGE','python:3.11-slim')
    container=client.containers.create(image,command,working_dir=str(cwd),
            user='0:0',network_disabled=True,use_config_proxy=False,environment=clean,
            mem_limit=memory,labels={'benchmark.candidate':'true'},**cfg)
    write(output/'container.json', {'container_id':container.id,
        'created_at':time.time(),'timeout_seconds':timeout,'gpu':gpu})
    result = None
    try:
        image_id=container.image.id;container.start()
        while True:
            container.reload()
            if container.status in ['exited','dead']:break
            stats=container.stats(stream=False)
            mem=stats.get('memory_stats',{})
            peak_memory=max(peak_memory,mem.get('usage',0),mem.get('max_usage',0))
            cpu_seconds=max(cpu_seconds,stats.get('cpu_stats',{}).get('cpu_usage',{}).get('total_usage',0)/1e9)
            if gpu is not None:
                try:
                    top=container.top();i=top['Titles'].index('PID');pids={int(p[i]) for p in (top['Processes'] or [])}
                    q=subprocess.check_output(['nvidia-smi','--query-compute-apps=pid,used_memory','--format=csv,noheader,nounits'],text=True)
                    used=0
                    for line in q.splitlines():
                        try:
                            pid,amount=line.split(',')
                            if int(pid) in pids:used+=int(amount)*1024**2
                        except ValueError:pass
                    peak_vram=max(peak_vram,used)
                except docker.errors.APIError as exc:
                    # Sampling is diagnostic, not the candidate's result. Sysbox
                    # top can race cgroup teardown while status still says running.
                    with (output/'sampling-errors.log').open('a') as log:
                        log.write(str(exc)+'\n')
                    container.reload()
            samples+=1
            if time.monotonic()-started>timeout:
                timed_out=True;kill_if_running(container);break
            time.sleep(.5)
        code=container.wait()['StatusCode']
        (output/'stdout.log').write_bytes(container.logs())
        elapsed=time.monotonic()-started
        result={'exit_code':code,'timed_out':timed_out,'wall_seconds':elapsed,
                'sampled_cpu_seconds':cpu_seconds,'sampled_peak_cgroup_memory_bytes':peak_memory,
                'sampled_peak_process_vram_bytes':peak_vram,'gpu':gpu,
                'gpu_allocated_seconds':elapsed if gpu is not None else 0,'samples':samples,
                'image_id':image_id,'container_id':container.id,'runtime':'sysbox-runc',
                'network_disabled':True,'cpu_affinity':cpu_ids,'memory_limit':memory,'shm_size':cfg['shm_size'],
                'measurement_notes':['Sampled cgroup CPU can miss the final tail; peaks can miss short transients.',
                                     'Cgroup memory includes page cache; it differs from host process-tree RSS.',
                                     'Container UID 0 is namespaced by Sysbox; host inputs are read-only.']}
        write(output/'resources.json',result)
        return result
    except BaseException as exc:
        # Persist verified termination/accounting before cleanup removes the only
        # container evidence. If the daemon is unreachable keep the reservation.
        try:
            container.reload()
            if container.status not in ('exited','dead'):
                kill_if_running(container)
            code=container.wait(timeout=30)['StatusCode']
            elapsed=time.monotonic()-started
            evidence={'exit_code':code,'timed_out':timed_out,'wall_seconds':elapsed,
                'gpu_allocated_seconds':elapsed if gpu is not None else 0,
                'container_id':container.id,'accounting_verified':True,
                'interrupted':True,'error_type':type(exc).__name__,'error':str(exc)}
            write(output/'interrupted-resources.json',evidence)
        except BaseException as accounting_error:
            (output/'accounting-error.json').write_text(json.dumps({'error':str(accounting_error)})+'\n')
        raise
    finally:
        try:
            container.remove(force=True)
        except docker.errors.NotFound:
            pass
        except Exception as exc:
            (output/'cleanup-error.json').write_text(json.dumps({'type':type(exc).__name__,'error':str(exc)})+'\n')
            if result is None:
                raise
        finally:
            client.close()


def recover_container(output):
    """Read a surviving, terminated container after controller/worker loss.

    Running or vanished containers without measurements remain unresolved.
    Never launches or kills anything during this evidence lookup.
    """
    from datetime import datetime
    from .io import read
    output=Path(output)
    if not (output/'container.json').exists():return None
    receipt=read(output/'container.json')
    client=docker.from_env()
    try:
        try:container=client.containers.get(receipt['container_id'])
        except docker.errors.NotFound:return None
        container.reload()
        if container.status not in ('exited','dead'):return None
        state=container.attrs['State']
        finished=datetime.fromisoformat(state['FinishedAt'].replace('Z','+00:00')).timestamp()
        elapsed=max(0.,finished-receipt['created_at'])
        (output/'stdout.log').write_bytes(container.logs())
        result={'container_id':container.id,'exit_code':state['ExitCode'],
                'timed_out':elapsed>receipt['timeout_seconds'], 'wall_seconds':elapsed,
                'gpu_allocated_seconds':elapsed if receipt['gpu'] is not None else 0,
                'gpu':receipt['gpu'],'accounting_verified':True,'recovered':True,
                'measurement_notes':['Recovered allocation upper bound from receipt to Docker finish timestamp; sampled peaks unavailable.']}
        write(output/'resources.json',result)
        container.remove()
        return result
    finally:client.close()
