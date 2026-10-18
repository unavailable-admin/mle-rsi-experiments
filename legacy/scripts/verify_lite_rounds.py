"""No paid API: four real GPU candidates through the shared scheduler and inherited leases."""
import argparse
import asyncio
from contextlib import ExitStack
from pathlib import Path
import os
import subprocess
import sys
from mle_rsi import REPO
from mle_rsi.common import read, write, lock
from mle_rsi.experiment import initialize, campaign
from mle_rsi.round_resources import ResourcePool
from dream_rsi.seed import make_artifact

async def verify(root):
    spec=read(REPO/'configs/lite22-rounds-5090-2.json')
    spec['development_tasks']=spec['experiment_tasks']=['leaf-classification']
    spec['execution']['gpu_tasks']=['leaf-classification']
    initialize(root,spec,'smoke')
    monitor=subprocess.Popen([sys.executable,str(REPO/'scripts/monitor_resources.py'),'--run-dir',str(root),'--output','/opt/mle-rsi-5090-2/monitoring/readiness','--port','18773','--interval','2'])
    pool=ResourcePool(root,spec['execution']['cpu_groups'],spec['execution']['gpu_pool'])
    c=campaign(root,'leaf-classification','development',resource_pool=pool)
    try:
        await c.bootstrap()
        artifact=make_artifact('Readiness only', (REPO/'examples/rounds_readiness_leaf.py').read_text())
        results=await asyncio.gather(*(c.evaluator.evaluate(artifact,{'attempt_id':f'readiness-{i}'}) for i in range(8)))
        resources=[r.diagnostics['resources'] for r in results]
        assert all(r.valid for r in results)
        assert {r['gpu'] for r in resources}==set(range(8))
        assert {tuple(r['cpu_affinity']) for r in resources}=={tuple(g) for g in spec['execution']['cpu_groups']}
        assert all(r.metrics['validation_score'] != 999 for r in results)
        ledger=read(root/'budget.json')
        assert not ledger['api']
        assert all(v['state']=='completed' for v in ledger['execution'].values())
        write(root/'readiness.json',{'paid_api':False,'official_grade':False,'valid':8,
              'gpu_devices':[r['gpu'] for r in resources],'resources':resources,
              'host_validation_scores':[r.metrics['validation_score'] for r in results]})
    finally:
        c.store.close()
        monitor.terminate()
        monitor.wait(timeout=15)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--run-dir',type=Path,required=True);a=p.parse_args()
    with ExitStack() as stack:
        for cpu in list(range(128)):
            stack.enter_context(lock(Path('/tmp/mle-rsi-resource-leases')/f'cpu-{cpu}',blocking=False))
        os.sched_setaffinity(0,set(range(64)))
        asyncio.run(verify(a.run_dir.resolve()))
