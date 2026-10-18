"""Trusted host subprocesses: process-group cleanup and sampled measurements.
Derived from rsi-eval bench_resources.py at 991d855a8c1cc71ea11a7f1512db54a564e5fd13.
"""
import asyncio
import os
from pathlib import Path
import signal
import time
import psutil
from .io import write


async def run_process(command, directory, timeout, cpus=None, env=None, cwd=None):
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
    if cpus:command=['taskset','-c',','.join(map(str,cpus)),*map(str,command)]
    start=time.monotonic();peak=0;timed_out=False;interrupted=False
    with (directory/'stdout.log').open('wb') as log:
        process=await asyncio.create_subprocess_exec(*map(str,command),cwd=cwd,env=env,
            stdout=log,stderr=asyncio.subprocess.STDOUT,start_new_session=True)
        write(directory/'process.json',{'pid':process.pid,'started_at':time.time(),'command':list(map(str,command))})
        try:
            while process.returncode is None:
                try:
                    parent=psutil.Process(process.pid)
                    total=0
                    for child in [parent,*parent.children(recursive=True)]:
                        try:total+=child.memory_info().rss
                        except psutil.Error:pass
                    peak=max(peak,total)
                except psutil.Error:pass
                if time.monotonic()-start>=timeout:
                    timed_out=True
                    break
                try:await asyncio.wait_for(process.wait(),.1)
                except asyncio.TimeoutError:pass
        except BaseException:
            interrupted=True
            raise
        finally:
            if process.returncode is None:
                try:os.killpg(process.pid,signal.SIGTERM)
                except ProcessLookupError:pass
                try:await asyncio.wait_for(process.wait(),10)
                except asyncio.TimeoutError:
                    try:os.killpg(process.pid,signal.SIGKILL)
                    except ProcessLookupError:pass
                    await process.wait()
            result={'exit_code':process.returncode,'timed_out':timed_out,'interrupted':interrupted,
                    'wall_seconds':time.monotonic()-start,'sampled_peak_tree_rss_bytes':peak,
                    'measurement_notes':['Sampled RSS can double-count shared pages and miss peaks.']}
            write(directory/'resources.json',result)
    return result


async def run_accounted(command,directory,timeout,cpus,ledger,ident,task,input_hash):
    """Reuse known host-process measurements; ambiguous launches remain blocked."""
    from .io import read
    from .recovery import OutcomeUnknown
    from .ledger import EvidenceConflict
    directory=Path(directory)
    row=ledger.get(ident)
    if row is not None and row['input_hash']!=input_hash:
        raise EvidenceConflict('Host process input changed')
    if row is not None and (directory/'resources.json').exists():
        result=read(directory/'resources.json')
    else:
        if row is not None and row['state'] not in ('reserved','not_sent'):
            raise OutcomeUnknown('Host process has no terminal measurement: '+ident)
        ledger.reserve(ident,task,input_hash,{'execution_seconds':timeout+15})
        ledger.record(ident,'running',{'directory':str(directory)})
        try:
            result=await run_process(command,directory,timeout,cpus)
        except BaseException:
            if (directory/'resources.json').exists():
                result=read(directory/'resources.json')
                ledger.record(ident,'completed',{'resources':str(directory/'resources.json')},
                              {'execution_seconds':result['wall_seconds']})
            raise
    ledger.record(ident,'completed',{'resources':str(directory/'resources.json')},
                  {'execution_seconds':result['wall_seconds']})
    return result
