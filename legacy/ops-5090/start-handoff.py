#!/usr/bin/env python3
"""Manual entrypoint on the existing 5090 host. Check by default; --start opts in."""
import argparse,json,os,subprocess,time,sys
from pathlib import Path
base=Path('/opt/mle-rsi-next');python='/opt/mle-rsi-fixes/controller-env/bin/python'
p=argparse.ArgumentParser(description=__doc__);p.add_argument('--start',action='store_true');args=p.parse_args()
if not base.exists():raise SystemExit('This entrypoint is for the configured 5090 host only.')
sys.path.insert(0,str(base/'code'))
from mle_rsi.common import read,lock,sha
from mle_rsi.experiment import source_manifest
parent=Path('/opt/mle-rsi-fixes/runs/lite22-001')
release=read(base/'evidence/release.json')
assert source_manifest()==release['source_hashes'],'Deployment code changed; revalidate before starting'
for name,digest in release['scripts'].items():assert sha(base/name)==digest,'Deployment script changed: '+name
boundary=read(base/'evidence/round4-boundary.json')
print('Round-four boundary:',json.dumps(boundary,ensure_ascii=False),flush=True)
if boundary['state']=='running_through_round4':
    cmd=Path('/proc',str(boundary['pid']),'cmdline').read_bytes().split(b'\0')
    assert b'--through-round' in cmd and cmd[cmd.index(b'--through-round')+1]==b'4'
elif boundary['state']!='complete':raise SystemExit('Boundary is not ready; inspect evidence before launching')
status=base/'evidence/handoff.json'
current=read(status)
print('Handoff:',json.dumps(current,ensure_ascii=False),flush=True)
if not args.start:
    print('Checks passed. Add --start to wait, benchmark, migrate and start round five.');sys.exit(0)
if current['state']=='complete':print('Already complete; no restart.');sys.exit(0)
with lock(base/'manual-handoff-launch',blocking=False):
    try:
        with lock(base/'handoff',blocking=False):pass
    except BlockingIOError:raise SystemExit('A handoff process is already running; no duplicate launch.')
    if current['state'] not in ('paused_by_user','waiting_for_round4'):
        raise SystemExit('Partial handoff exists. Inspect it before retrying; no automatic rerun.')
    with (base/'evidence/handoff.log').open('a') as log:
        child=subprocess.Popen([python,str(base/'continue_after_round4.py')],cwd=base/'code',stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
    (base/'evidence/handoff-supervisor.pid').write_text(str(child.pid)+'\n')
    print('Started background supervisor PID',child.pid,'; disconnecting this terminal does not stop it.',flush=True)
    print('Status:',status,'; log:',base/'evidence/handoff.log',flush=True)
    try:
        while True:
            code=child.poll();state=read(status)
            print(time.strftime('%Y-%m-%d %H:%M:%S UTC',time.gmtime()),state['state'],flush=True)
            if code is not None:
                print(json.dumps(state,ensure_ascii=False,indent=2),flush=True)
                if code or state['state']!='complete':sys.exit(1)
                print('New dashboard http://127.0.0.1:18970 ; tree :18971 ; resources :18972');break
            time.sleep(30)
    except KeyboardInterrupt:
        print('\nStopped terminal display only; background handoff continues. PID',child.pid)
