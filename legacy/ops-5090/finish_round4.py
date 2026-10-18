"""Drain old controller, then resume the identical frozen run through round four."""
import os, sys, time, subprocess, json
from pathlib import Path
sys.path.insert(0, '/opt/mle-rsi-fixes/code')
from mle_rsi.common import read, write, lock
from mle_rsi.rounds import control
from mle_rsi.experiment import frozen
root=Path('/opt/mle-rsi-fixes/runs/lite22-001')
status=Path('/opt/mle-rsi-next/evidence/round4-boundary.json')
with lock(Path('/opt/mle-rsi-next/finish-round4'),blocking=False):
    frozen(root)
    control(root,'stop')
    write(status,{'state':'draining','requested_at':time.time(),'target':4})
    while True:
        try:
            with lock(root/'controller',blocking=False):
                break
        except BlockingIOError:
            time.sleep(5)
    b=read(root/'budget.json')
    assert not any(v['state'] in ('running','pending','uncertain') for group in b.values() for v in group.values()), 'Unsettled ledger; not restarting'
    assert read(root/'round-status.json')['state']=='stopped', 'Controller did not stop cleanly'
    frozen(root)
    control(root,'resume')
    env=dict(os.environ,MLE_GPU_CONFIG_DIR='/opt/mle-rsi-fixes/gpu-configs',PYTHONDONTWRITEBYTECODE='1')
    with (root/'controller.log').open('a') as log:
        child=subprocess.Popen(['/opt/mle-rsi-fixes/controller-env/bin/python','-m','mle_rsi.rounds','run','--run-dir',str(root),'--through-round','4'],cwd='/opt/mle-rsi-fixes/code',env=env,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
        write(status,{'state':'running_through_round4','pid':child.pid,'at':time.time(),'target':4})
        code=child.wait()
    final=read(root/'round-status.json')
    write(status,{'state':'complete' if code==0 and final['state']=='target_reached' else 'needs_inspection','returncode':code,'controller_status':final,'at':time.time()})
