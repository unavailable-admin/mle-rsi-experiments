"""No paid API or private grading: end-to-end readiness with real candidate mounts."""
import argparse
import asyncio
from contextlib import ExitStack
from pathlib import Path
import os
from mle_rsi import REPO
from mle_rsi.common import read,write,lock
from mle_rsi.experiment import initialize,campaign
from mle_rsi.round_resources import ResourcePool
from dream_rsi.seed import make_artifact

async def verify(root, devices):
    spec=read(REPO/'configs/lite22-fixes-5090.json')
    spec['development_tasks']=spec['experiment_tasks']=['leaf-classification']
    spec['execution']['gpu_tasks']=['leaf-classification'] if devices else []
    spec['execution']['gpu_pool']=devices or [0]
    spec['execution']['cpu_groups']=[list(range(i*8,i*8+8)) for i in range(len(devices) or 1)]
    spec['execution']['cpu_ids']=[c for g in spec['execution']['cpu_groups'] for c in g]
    initialize(root,spec,'smoke')
    pool=ResourcePool(root,spec['execution']['cpu_groups'],spec['execution']['gpu_pool'])
    c=campaign(root,'leaf-classification','development',resource_pool=pool)
    try:
        await c.bootstrap()
        artifact=make_artifact('Environment readiness only',(REPO/'examples/fixes_environment_probe.py').read_text())
        reports=await asyncio.gather(*(c.evaluator.evaluate(artifact,{'attempt_id':f'readiness-{i}'}) for i in range(len(devices) or 1)))
        resources=[r.diagnostics['resources'] for r in reports]
        write(root/'readiness.json',{'all_valid':all(r.valid for r in reports),'paid_api':False,'official_grade':False,
            'results':[{'valid':r.valid,'error':r.error,'metrics':r.metrics,'resources':r.diagnostics['resources']} for r in reports]})
        assert all(r.valid for r in reports), 'See execution stdout and readiness.json'
        assert {r['gpu'] for r in resources}==set(devices or [None])
        assert all(r.metrics['validation_score']!=999 for r in reports)
        ledger=read(root/'budget.json');assert not ledger['api']
        assert all(v['state']=='completed' for v in ledger['execution'].values())
    finally:c.store.close()

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--run-dir',type=Path,required=True);p.add_argument('--cpu-only',action='store_true');args=p.parse_args()
    with ExitStack() as stack:
        for cpu in range(128):stack.enter_context(lock(Path('/tmp/mle-rsi-resource-leases')/f'cpu-{cpu}',blocking=False))
        os.sched_setaffinity(0,set(range(64)))
        asyncio.run(verify(args.run_dir.resolve(),[] if args.cpu_only else list(range(8))))
