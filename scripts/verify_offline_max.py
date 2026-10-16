"""Half-machine native readiness; prewritten probes, no model calls/private grading."""
import asyncio
from contextlib import ExitStack
from pathlib import Path
import os
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from mle_rsi import REPO, WORKSPACE
from mle_rsi.common import read,write,lock
from mle_rsi.experiment import initialize,campaign
from mle_rsi.adaptive_resources import MixedResourcePool
from dream_rsi.seed import make_artifact

async def verify(root):
    spec=read(REPO/'configs/offline-max-4090-half.json')
    spec['development_tasks']=spec['experiment_tasks']=['leaf-classification']
    spec['execution']['gpu_tasks']=['leaf-classification']
    initialize(root,spec,'smoke')
    pool=MixedResourcePool(root,spec['execution'])
    c=campaign(root,'leaf-classification','development',resource_pool=pool)
    try:
        await c.bootstrap()
        artifact=make_artifact('Offline-max native environment probe; no model calls',
                               (REPO/'examples/fixes_environment_probe.py').read_text())
        results=await asyncio.gather(*(c.evaluator.evaluate(artifact,{'attempt_id':f'readiness-{i}'}) for i in range(4)))
        rows=[{'valid':r.valid,'error':r.error,'metrics':r.metrics,'diagnostics':r.diagnostics} for r in results]
        write(root/'readiness.json',{'paid_api':False,'official_grade':False,'results':rows})
        assert all(r.valid for r in results), 'Inspect readiness.json and executions'
        resources=[r.diagnostics['resources'] for r in results]
        assert {r['gpu'] for r in resources}=={0,1,2,3}
        assert all(len(r['cpu_affinity'])==4 and set(r['cpu_affinity'])<=set(range(32)) for r in resources)
        spec['execution']['gpu_tasks']=[]
        c.task_backend.spec['execution']['gpu_tasks']=[]
        result=await c.evaluator.evaluate(artifact,{'attempt_id':'readiness-cpu'})
        rows.append({'valid':result.valid,'error':result.error,'metrics':result.metrics,'diagnostics':result.diagnostics})
        write(root/'readiness.json',{'paid_api':False,'official_grade':False,'results':rows})
        assert result.valid
        assert len(result.diagnostics['resources']['cpu_affinity'])==8
        ledger=read(root/'budget.json')
        assert not ledger['api'] and all(v['state']=='completed' for v in ledger['execution'].values())
        print('PASS: four 4-core GPU probes and one 8-core CPU probe, no paid API/private grading')
    finally:c.store.close()

if __name__=='__main__':
    root=Path(sys.argv[1]).resolve()
    with ExitStack() as stack:
        for cpu in [*range(32),*range(64,96)]:
            stack.enter_context(lock(Path('/tmp/mle-rsi-resource-leases')/f'cpu-{cpu}',blocking=False))
        os.sched_setaffinity(0,set(range(32)))
        asyncio.run(verify(root))
