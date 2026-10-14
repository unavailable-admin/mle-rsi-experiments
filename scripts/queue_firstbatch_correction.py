import json, os, subprocess, sys, time, hashlib
from pathlib import Path
P=Path('/mnt/afs/yangsen1/runs/dream-rsi-gpt-firstbatch-001')
R=Path('/mnt/afs/yangsen1/runs/dream-rsi-gpt-firstbatch-guarded-001')
W=Path('/mnt/afs/yangsen1/projects/mle-rsi-firstbatch-guarded')
B=Path('/mnt/afs/yangsen1/runs/dream-rsi-gpt-halfday-001')
S=P/'corrective-queue.json'
def read(p):return json.loads(p.read_text())
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def status(state,**kw):
    tmp=S.with_suffix('.tmp');tmp.write_text(json.dumps(dict(state=state,at=time.time(),pid=os.getpid(),corrective_run=str(R),**kw),indent=2));tmp.replace(S)
def live(pid):
    try:return Path(f'/proc/{pid}/stat').read_text().split()[2]!='Z'
    except FileNotFoundError:return False
try:
    status('waiting_for_pilot_audit')
    while True:
        s=read(P/'supervisor.json')
        if s['state']!='running' and not live(s['pid']):break
        time.sleep(30)
    assert s['state']=='completed',s
    status('auditing_pilot')
    subprocess.run([sys.executable,'scripts/report_firstbatch.py',str(P)],cwd='/mnt/afs/yangsen1/projects/mle-rsi-experiments',check=True)
    a=read(P/'firstbatch-audit.json');assert a['all_checks_passed'],[c for c in a['checks'] if not c['passed']]
    budget=read(P/'budget.json');rows=list(budget.get('api',{}).values())
    accounted=sum(x.get('charged_usd',x.get('reserved_usd',0)) for x in rows)
    spec=read(W/'configs/gpt-firstbatch-guarded-dev4-lite22.json')
    spec['budget']['api_usd_total']=round(20-accounted-0.000001,6)
    assert spec['budget']['api_usd_total']>10
    status('preparing_corrected_run',pilot_accounted_usd=accounted,corrective_cap=spec['budget']['api_usd_total'])
    os.chdir(W);sys.path.insert(0,str(W))
    from mle_rsi.__main__ import preflight
    from mle_rsi.experiment import initialize
    checks=preflight(spec)
    initialize(R,spec,'real')
    (R/'preflight.json').write_text(json.dumps(checks,indent=2))
    (R/'authorization-and-lineage.json').write_text(json.dumps(dict(previous_run=str(B),previous_budget_sha256=sha(B/'budget.json'),pilot_run=str(P),pilot_budget_sha256=sha(P/'budget.json'),pilot_accounted_usd=accounted,combined_new_api_ceiling_usd=20,corrective_api_cap_usd=spec['budget']['api_usd_total'],authorization='User requested first batch and a full experiment; corrective host guard required to fulfill bounded repair. No old ledger reset.',source_worktree=str(W)),indent=2))
    (R/'corrective-verification.json').write_text((Path('/mnt/afs/yangsen1/runs/dream-rsi-firstbatch-verification-001')/'corrective-verification.json').read_text())
    status('running_corrected_experiment',pilot_accounted_usd=accounted,corrective_cap=spec['budget']['api_usd_total'])
    with (R/'supervisor.log').open('a') as log:
        p=subprocess.Popen([sys.executable,'-m','mle_rsi.supervisor','--run-dir',str(R)],cwd=W,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
    (R/'launch.json').write_text(json.dumps(dict(pid=p.pid,launched_at=time.time(),run_dir=str(R)),indent=2))
    code=p.wait();assert code==0,code
    subprocess.run([sys.executable,'scripts/report_firstbatch.py',str(R)],cwd=W,check=True)
    status('corrected_experiment_finished',supervisor=read(R/'supervisor.json'))
except BaseException as e:
    status('needs_inspection',error=f'{type(e).__name__}: {e}')
    raise
