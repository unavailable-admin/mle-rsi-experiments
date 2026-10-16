"""Verified handoff: wait for round four, benchmark, migrate, then serve and run."""
import os,sys,time,subprocess,json
from pathlib import Path
sys.path.insert(0,'/opt/mle-rsi-next/code')
from mle_rsi.common import read,write,lock,sha
from mle_rsi.experiment import frozen,source_manifest
from mle_rsi.migrate_rounds import migrate
from mle_rsi.rounds import control,validate
base=Path('/opt/mle-rsi-next');parent=Path('/opt/mle-rsi-fixes/runs/lite22-001');root=base/'runs/lite22-medal-001'
python='/opt/mle-rsi-fixes/controller-env/bin/python'
status=base/'evidence/handoff.json'
env=dict(os.environ,MLE_GPU_CONFIG_DIR='/opt/mle-rsi-fixes/gpu-configs',PYTHONDONTWRITEBYTECODE='1')
def state(name,**kwargs):write(status,dict(state=name,at=time.time(),**kwargs))
def service(name,args):
    with (root/(name+'.log')).open('a') as log:
        p=subprocess.Popen([python,*args],cwd=base/'code',env=env,stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
    (root/(name+'.pid')).write_text(str(p.pid)+'\n')
    return p
try:
 with lock(base/'handoff',blocking=False):
    seal=read(base/'evidence/release.json')
    assert seal['tests_passed'] and seal['source_hashes']==source_manifest()
    for path,digest in seal['scripts'].items():assert sha(base/path)==digest
    state('waiting_for_round4')
    while True:
        boundary=read(base/'evidence/round4-boundary.json')
        if boundary['state']=='complete':break
        if boundary['state']=='needs_inspection':raise RuntimeError('Old controller needs inspection')
        time.sleep(5)
    assert source_manifest()==seal['source_hashes']
    state('benchmarking_cpu_allocations')
    with (base/'evidence/native-comparison.log').open('a') as log:
        subprocess.run([python,str(base/'benchmark_resources.py')],cwd=base/'code',env=env,stdout=log,stderr=subprocess.STDOUT,check=True)
    benchmark=read(base/'verification/cpu-comparison/summary.json')
    assert all(v['results']['8']['valid'] for v in benchmark['tasks'].values()), 'Baseline public replay invalid'
    state('migrating')
    migrate(parent,root,4)
    # Per-task CPU exceptions are part of the new frozen configuration, before launch.
    config=read(root/'frozen-config.json')
    config['spec']['execution']['mixed_resources']['task_cpu_cores']={t:v['recommended_cores'] for t,v in benchmark['tasks'].items() if v['recommended_cores']==8}
    config['resource_benchmark_sha256']=sha(base/'verification/cpu-comparison/summary.json')
    write(root/'frozen-config.json',config)
    frozen(root);validate(config['spec'])
    services=[service('dashboard',['-m','mle_rsi.rounds','web','--run-dir',str(root),'--port','18970']),
        service('tree-viewer',['-m','mle_rsi','web','--run-dir',str(root),'--port','18971']),
        service('resource-monitor',['scripts/monitor_resources.py','--run-dir',str(root),'--output',str(base/'monitoring/formal'),'--port','18972'])]
    import urllib.request
    http=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    for attempt in range(20):
        try:
            assert json.load(http.open('http://127.0.0.1:18970/report.json',timeout=10))['run_dir']==str(root)
            assert http.open('http://127.0.0.1:18971/',timeout=10).status==200
            assert json.load(http.open('http://127.0.0.1:18972/metrics.json',timeout=10))['run_dir']==str(root)
            break
        except Exception:
            if any(p.poll() is not None for p in services):raise RuntimeError('Viewer exited')
            time.sleep(2)
    else:raise RuntimeError('Viewer checks failed')
    control(root,'resume')
    child=service('controller',['-m','mle_rsi.rounds','run','--run-dir',str(root)])
    state('verifying_launch',pid=child.pid,run_dir=str(root))
    for attempt in range(120):
        current=read(root/'round-status.json')
        if current.get('state')=='all_medaled':
            state('complete',run_dir=str(root),retired=22,remaining=[],ports=[18970,18971,18972],reason='All tasks already medaled');break
        if child.poll() is not None:raise RuntimeError('New controller exited: '+str(child.returncode))
        if current.get('state')=='running' and current.get('round')==5:
            data=json.load(http.open('http://127.0.0.1:18970/report.json',timeout=30))
            if data['controller_alive'] and (data['scheduler']['active'] or data['budget']['unsettled_requests']):
                state('complete',pid=child.pid,run_dir=str(root),retired=data['retired_count'],remaining=data['remaining_tasks'],ports=[18970,18971,18972],budget=data['budget']);break
        if current.get('state') in ('blocked','stopped','budget_stop'):raise RuntimeError(str(current))
        time.sleep(5)
    else:raise RuntimeError('Fifth round launch did not verify within ten minutes')
except Exception as exc:
 state('needs_inspection',error=type(exc).__name__+': '+str(exc));raise
