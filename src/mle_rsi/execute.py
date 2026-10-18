"""Isolated candidate execution. The worker owns Docker cleanup and evidence."""
import asyncio
import os
from pathlib import Path
import shutil
import signal
import sys
from .io import read,write,digest
from . import runtime
from .process import run_process
from .recovery import execution as recover_execution


def readable(path):
    path=Path(path)
    path.chmod(path.stat().st_mode | (0o555 if path.is_dir() else 0o444))


async def execute(root, directory, ident, input_hash, task, host, slot, ledger):
    directory=Path(directory)
    recovered=recover_execution(directory,ident,input_hash,ledger)
    if recovered is not None:return recovered
    timeout=task['timeout_seconds']
    ledger.reserve(ident,task['id'],input_hash,{'execution_seconds':timeout+30,
                   'gpu_seconds':timeout+30 if slot['gpu'] is not None else 0})
    request={'root':str(root),'directory':str(directory),'task':task,'host':host,'slot':slot}
    write(directory/'execution-request.json',request)
    ledger.record(ident,'running',{'directory':str(directory)})
    try:
        await run_process([sys.executable,'-m','mle_rsi.execute',str(directory/'execution-request.json')],
                          directory/'worker',timeout+90)
    except BaseException:
        try:
            recover_execution(directory,ident,input_hash,ledger)
        except Exception:
            pass  # Retain reservation and the original cancellation/error identity.
        raise
    return recover_execution(directory,ident,input_hash,ledger)


def worker(request):
    from .containers import measure_container
    def stop(signum,frame):raise KeyboardInterrupt('Worker cancelled')
    signal.signal(signal.SIGTERM,stop)
    root=Path(request['root']);directory=Path(request['directory'])
    task,host,slot=request['task'],request['host'],request['slot']
    prepared=root/'prepared'/task['id'];manifest=read(prepared/'manifest.json')
    public=prepared/manifest['sanitized_public'] if manifest.get('sanitized_public') else Path(task['public_dir'])
    output=directory/'output';output.mkdir(exist_ok=True)
    if host.get('container_output_uid') is not None:
        os.chown(output,host['container_output_uid'],host['container_output_gid'])
    environment=Path(host['python_environment'])
    pairs=[(environment,environment,True),(public,Path('/data'),True),
           (directory/'agent.py',Path('/agent.py'),True),(directory/'task.json',Path('/task.json'),True),
           (output,Path('/output'),False),(root/'runtime',Path('/runtime'),True),
           (prepared/'manifest.json',Path('/validation/manifest.json'),True)]
    if manifest.get('split_file'):pairs.append((prepared/'split.csv',Path('/validation/split.csv'),True))
    for target,source in manifest.get('data_overrides',{}).items():pairs.append((prepared/source,Path('/data')/target,True))
    for source in host.get('python_extra_mounts',[]):pairs.append((Path(source),Path(source),True))
    for key,target in [('pretrained_models','/models/pretrained'),('torch_cache','/models/torch')]:
        if host.get(key):pairs.append((Path(host[key]),Path(target),True))
    for name in ('agent.py','task.json'):readable(directory/name)
    mounts=[{'Source':str(s.resolve()),'Target':str(t),'Type':'bind','ReadOnly':ro} for s,t,ro in pairs]
    env={'CONDA_PREFIX':str(environment),'BENCH_CONTAINER_IMAGE':host['image'],'PYTHONUNBUFFERED':'1',
         'HF_HUB_OFFLINE':'1','HF_DATASETS_OFFLINE':'1','TORCH_HOME':'/models/torch',
         **{k:str(len(slot['cpus'])) for k in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS')}}
    return measure_container([str(environment/'bin/python'),'/runtime/mle_runtime.py','/agent.py'],
        '/output',env,directory/'execution',mounts,task['timeout_seconds'],slot['cpus'],slot['gpu'],
        host.get('memory','32g'),host.get('gpu_config_dir'))


if __name__=='__main__':worker(read(sys.argv[1]))
