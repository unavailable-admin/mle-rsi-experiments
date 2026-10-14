import argparse
import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

from . import REPO, WORKSPACE
from .backend import task_ids
from .common import lock, read, write, load_spec
from .experiment import initialize, frozen, develop, freeze_policy, final_evaluate, summarize


def preflight(spec):
    import docker
    from dream_rsi.sandbox import BwrapSandbox
    BwrapSandbox().preflight()
    checks={'bwrap_policy_isolation':True, 'tasks':len(task_ids())}
    for task in task_ids():
        assert (WORKSPACE/'data/mlebench'/task/'prepared/public/description.md').is_file(),task
    execution=spec['execution']
    groups=execution.get('cpu_groups',[execution['cpu_ids']])
    flat=[cpu for group in groups for cpu in group]
    assert all(groups) and len(flat)==len(set(flat)) and set(flat)==set(execution['cpu_ids'])
    assert set(execution['cpu_ids']) <= os.sched_getaffinity(0)
    assert Path(execution['python_environment'],'bin/python').exists()
    client=docker.from_env()
    try:
        checks['docker']=client.version()['Version']
        checks['image']=client.images.get(execution['image']).id
    finally:client.close()
    checks['gpu_pool']=execution['gpu_pool']
    checks['credentials_configured']=bool(os.environ.get(spec['model']['api_key_env'])) or bool(spec['model'].get('credential_file') and Path(spec['model']['credential_file']).is_file())
    # Authenticated read-only connectivity check; no generation or billing call.
    import httpx
    from .provider import load_key
    load_key(spec)
    with httpx.Client(timeout=30,trust_env=spec['model'].get('trust_env',True)) as transport:
        response=transport.get(spec['model']['base_url'].rstrip('/')+'/v1/models',
            headers={'Authorization':'Bearer '+os.environ[spec['model']['api_key_env']]})
        response.raise_for_status()
        ids={row['id'] for row in response.json()['data']}
        required={spec['model']['name'],spec['model'].get('developer_name',spec['model']['name'])}
        if not required <= ids:
            raise RuntimeError('Configured model absent from provider model list')
    checks['authenticated_model_list']=True
    checks['generation_api_tested']=False
    checks['paid_api_called']=False
    print(json.dumps(checks,indent=2))
    return checks


def main():
    p=argparse.ArgumentParser(description='Pinned Dream-RSI MLE online/offline experiment')
    p.add_argument('command',choices=['preflight','init','dev','freeze','final','run','status','web','upgrade-budget'])
    p.add_argument('--config',type=Path,default=REPO/'configs/gemini-dev4-lite22.json')
    p.add_argument('--run-dir',type=Path)
    p.add_argument('--mode',choices=['real','mock','smoke'],default='real')
    p.add_argument('--tasks',nargs='+',help='Optional final task subset; all22 report keeps unfinished tasks visible')
    p.add_argument('--port',type=int,default=8766)
    a=p.parse_args()
    if a.command=='preflight':preflight(load_spec(a.config));return
    if a.run_dir is None:p.error('--run-dir required')
    root=a.run_dir.resolve()
    if a.command=='init':
        initialize(root,load_spec(a.config),a.mode);print(root);return
    if a.command=='web':
        from dream_rsi.web.__main__ import main as viewer
        viewer(['--runs-dir',str(root/'campaigns'),'--port',str(a.port)]);return
    if a.command=='status':
        result=summarize(root)
        print(json.dumps({'mode':result['mode'],'groups':result['groups'],'budget':budget_summary(result.get('budget',{}))},indent=2))
        for path in sorted((root/'outcomes').glob('*/*/status.json')):
            final=path.with_name('result.json')
            row=read(final if final.exists() else path)
            print(row['phase'],row['task'],row['state'],row.get('reason',''))
        return
    if a.command=='upgrade-budget':
        with lock(root/'controller',blocking=False):
            upgrade_budget(root)
        return
    record=frozen(root)
    os.sched_setaffinity(0,record['spec']['execution']['cpu_ids'])
    with lock(root/'controller',blocking=False):
        async def run():
            if a.command in ('dev','run'):await develop(root)
            if a.command in ('freeze','run'):await freeze_policy(root)
            if a.command in ('final','run'):await final_evaluate(root,a.tasks)
        asyncio.run(run())


def upgrade_budget(root):
    """Explicit, archived migration; caller must hold the controller lock."""
    from .experiment import source_manifest
    import time
    config_path=root/'frozen-config.json'
    before=read(config_path)
    current=source_manifest()
    changed={p for p in set(current)|set(before['source_hashes']) if current.get(p)!=before['source_hashes'].get(p)}
    allowed={'mle_rsi/common.py','mle_rsi/experiment.py','mle_rsi/__main__.py'}
    if not changed<=allowed:
        raise RuntimeError('Migration refuses unrelated source changes: '+str(sorted(changed-allowed)))
    archive=root/'budget-upgrade-before.json'
    if not archive.exists():write(archive,before)
    before['spec']['budget']['development_api_usd_per_task']=before['spec']['budget']['api_usd_per_task']
    before['source_hashes']=current
    before.setdefault('migrations',[]).append({'at':time.time(),'change':'Uncertain API calls retain full reservations; permit new calls within shared task cap; development may use remaining total cap','changed_sources':sorted(changed)})
    write(config_path,before)
    # Restore only requests proven to have stopped locally before transport reservation.
    import hashlib
    from dataclasses import replace
    from dream_rsi.storage import CampaignStore
    ledger=read(root/'budget.json') if (root/'budget.json').exists() else {'api':{}}
    restored=[]
    for directory in sorted((root/'campaigns').glob('dev-*')):
        if not (directory/'campaign.sqlite').exists():continue
        store=CampaignStore(directory)
        recovered=[]
        try:
            for request_id,error in store.list('provider_error'):
                if error.get('error_type')!='LimitReached':continue
                usage=store.get('usage',request_id)
                ident=directory.name+':'+request_id
                transport_path=root/'api'/directory.name/hashlib.sha256(request_id.encode()).hexdigest()[:24]/'request.json'
                if usage is None or usage.status!='external_unknown':continue
                if ident in ledger['api'] or transport_path.exists() or store.get('response',request_id) is not None:continue
                store.put('budget_recovery',request_id,{'previous_usage':usage,'provider_error':error,'reason':'No transport reservation or request artifact: rejected locally before send','at':time.time()})
                store.put('usage',request_id,replace(usage,status='reserved'))
                recovered.append(request_id)
        finally:store.close()
        if recovered:
            task=directory.name.removeprefix('dev-')
            result=root/'outcomes/development'/task/'result.json'
            if result.exists() and read(result).get('state')=='stopped':
                result.rename(result.with_name('result-before-budget-upgrade-'+str(time.time_ns())+'.json'))
            restored.append({'task':task,'requests':recovered})
    write(root/'budget-recovery.json',{'at':time.time(),'restored':restored,'old_uncertain_requests_replayed':False})
    print('Budget policy upgraded; all charges and reservations preserved. No experiment launched.')
    print('Locally blocked requests restored:',json.dumps(restored))


def budget_summary(value):
    rows=list(value.get('api',{}).values());execution=list(value.get('execution',{}).values())
    return {'api_usd':sum(r.get('charged_usd',0) for r in rows),
            'api_reserved_usd':sum(r['reserved_usd'] for r in rows if 'charged_usd' not in r),
            'api_accounted_usd':sum(r.get('charged_usd',r['reserved_usd']) for r in rows),
            'unsettled_requests':sum(r['state'] in ('pending','uncertain') for r in rows),
            'gpu_hours':sum(r['gpu_seconds'] for r in execution)/3600,
            'candidate_wall_hours':sum(r['wall_seconds'] for r in execution)/3600}


if __name__=='__main__':main()
