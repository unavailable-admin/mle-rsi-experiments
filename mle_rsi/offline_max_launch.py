"""Start all services from the same sealed checkout; verify before paid admission."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import urllib.request
from . import REPO
from .common import read,write,lock
from .rounds import control


def launch(root, ports=(19070,19071,19072), controller=True):
    # Includes the online controller's entire lifetime; duplicate starts are rejected.
    with lock(root/'controller',blocking=False): pass
    os.sched_setaffinity(0,read(root/'frozen-config.json')['spec']['execution']['cpu_ids'])
    services=[('dashboard',['-m','mle_rsi.rounds','web','--run-dir',str(root),'--port',str(ports[0])]),
        ('tree-viewer',['-m','mle_rsi','web','--run-dir',str(root),'--port',str(ports[1])]),
        ('resource-monitor',['scripts/monitor_resources.py','--run-dir',str(root),
            '--output',str(root/'monitoring'),'--port',str(ports[2])])]
    env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1',
         'MLE_GPU_CONFIG_DIR':read(root/'frozen-config.json')['spec']['execution']['gpu_config_dir']}
    processes=[]
    def service(name,args):
        with (root/(name+'.log')).open('a') as log:
            proc=subprocess.Popen([sys.executable,*args],cwd=REPO,env=env,stdin=subprocess.DEVNULL,
                stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
        write(root/(name+'.pid'),proc.pid)
        return proc
    def running(name):
        pidfile=root/(name+'.pid')
        if not pidfile.exists():return False
        pid=str(read(pidfile))
        proc=Path('/proc')/pid
        if not proc.exists():return False
        args=proc.joinpath('cmdline').read_bytes().split(b'\0')
        if str(root).encode() not in args or proc.joinpath('cwd').resolve()!=REPO:
            raise RuntimeError('Viewer PID ownership mismatch: '+name)
        return True
    for port,(name,args) in zip(ports,services):
        if running(name):continue
        with socket.socket() as sock: sock.bind(('127.0.0.1',port))
        processes.append(service(name,args))
    http=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    for attempt in range(20):
        try:
            data=json.load(http.open(f'http://127.0.0.1:{ports[0]}/report.json',timeout=10))
            assert data['run_dir']==str(root)
            assert http.open(f'http://127.0.0.1:{ports[1]}/',timeout=10).status==200
            data=json.load(http.open(f'http://127.0.0.1:{ports[2]}/metrics.json',timeout=10))
            assert data['run_dir']==str(root)
            break
        except Exception:
            if any(p.poll() is not None for p in processes):raise RuntimeError('Viewer exited; inspect logs')
            time.sleep(1)
    else:raise RuntimeError('Viewer readiness failed; no paid work launched')
    if not controller:
        print('Viewers ready; experiment remains unstarted')
        return
    control(root,'resume')
    proc=service('controller',['-m','mle_rsi.rounds','run','--run-dir',str(root)])
    time.sleep(2)
    if proc.poll() is not None:raise RuntimeError('Controller exited; inspect controller.log')
    print(f'Controller {proc.pid}; run {root}; code {REPO}')
    print(f'Dashboard http://127.0.0.1:{ports[0]}/ | trees :{ports[1]} | resources :{ports[2]}')
