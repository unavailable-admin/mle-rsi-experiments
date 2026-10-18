"""Audited round-boundary export and half-machine offline-max continuation."""
import argparse
import asyncio
import copy
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import sqlite3
import subprocess
import sys
import time
from dataclasses import asdict
from contextlib import contextmanager
from . import REPO, WORKSPACE
from .common import read, write, sha, lock
from .experiment import campaign, frozen, source_manifest
from .rounds import verify_seal, validate, control
from dream_rsi.storage import CampaignStore
from dream_rsi.codec import digest, plain

SEARCH = dict(policy_tuning_trials=48, policy_tuning_scenarios=4,
              policy_versions_per_round=5, replay_node_budget_growth=.7,
              counterfactual_episodes=4, counterfactual_alternatives=3,
              counterfactual_steps=8, replay_scenarios=4)
DEFAULT_EXPORT = WORKSPACE/'runs/code-releases/lite22-5090-round4'
DEFAULT_RUN = WORKSPACE/'runs/dream-rsi-offline-max-001'

@contextmanager
def opened(path, readonly=True):
    store = CampaignStore(path, read_only=readonly)
    try: yield store
    finally: store.close()


def inspect_boundary(source, boundary=4):
    record = read(source/'frozen-config.json')
    state = read(source/'round-status.json')
    if state['state'] not in ('target_reached', 'stopped', 'paused') or state.get('blocked'):
        raise ValueError('Parent must be stopped at a complete boundary without blocked tasks')
    ledger = read(source/'budget.json') if (source/'budget.json').exists() else {'api':{},'execution':{}}
    if any(v['state'] in ('pending','running','uncertain') for group in ledger.values() for v in group.values()):
        raise ValueError('Parent has unsettled API/execution entries')
    tasks = {}
    for task in record['spec']['experiment_tasks']:
        with opened(source/'campaigns'/('dev-'+task)) as store:
            cp = store.get('checkpoint','active')
            if cp['phase'] != 'ROUND_COMPLETED' or cp['completed_rounds'] != boundary or len(store.list('node')) != boundary*25:
                raise ValueError('Not an exact completed boundary: '+task)
            tasks[task] = {'protocol_hash':store.get('metadata','protocol_hash'),
                           'backend':store.get('metadata','task_backend'),
                           'checkpoint_sha256':digest(plain(cp))}
        for number in range(1,boundary+1):
            folder = source/'rounds'/f'{number:04d}'/task
            verify_seal(folder)
            result = read(folder/'official-result.json')
            allowed = ('graded','no_submission') if record['mode']=='real' else ('verification_only',)
            if result['state'] not in allowed: raise ValueError('Incomplete grading: '+task)
    if record['mode']=='real':
        for task in tasks:
            prepared=source/'prepared'/task
            for rel, expected in read(prepared/'manifest.json')['file_hashes'].items():
                if sha(prepared/rel)!=expected:
                    raise ValueError('Prepared validation data changed: '+task+'/'+rel)
    for rel, expected in record.get('asset_hashes',{}).items():
        if sha(source/rel) != expected: raise ValueError('Parent asset changed: '+rel)
    return record, tasks


def copy_boundary(source, target, tasks):
    target.mkdir(parents=True, exist_ok=False)
    for name in ('prepared','runtime','rounds','api'):
        if (source/name).exists(): shutil.copytree(source/name,target/name)
    for name in ('frozen-config.json','budget.json','round-status.json','round-timing.json'):
        if (source/name).exists(): shutil.copy2(source/name,target/name)
    for task in tasks:
        src=source/'campaigns'/('dev-'+task); dst=target/'campaigns'/('dev-'+task)
        dst.mkdir(parents=True)
        # All immutable public-search blobs and snapshots live in artifacts.
        shutil.copytree(src/'artifacts',dst/'artifacts')
        with sqlite3.connect((src/'campaign.sqlite').as_uri()+'?mode=ro',uri=True) as a, sqlite3.connect(dst/'campaign.sqlite') as b:
            a.backup(b)
    # Candidate execution scratch is deliberately omitted. Sealed round submissions
    # are retained and used when an inherited incumbent remains best after migration.


def export(source, destination, boundary=4):
    source,destination=source.resolve(),destination.resolve()
    if destination.exists(): raise ValueError('Export destination already exists')
    staging=destination.with_name(destination.name+'.partial')
    with lock(source/'controller',blocking=False):
        record,tasks=inspect_boundary(source,boundary)
        copy_boundary(source,staging,tasks)
        files={str(p.relative_to(staging)):sha(p) for p in sorted(staging.rglob('*')) if p.is_file()}
        write(staging/'export-seal.json',dict(source=str(source),boundary=boundary,tasks=tasks,
            parent_config_sha256=sha(source/'frozen-config.json'),files=files,created_at=time.time()))
        staging.rename(destination)
    return destination


def verify_export(source):
    seal=read(source/'export-seal.json')
    for rel,expected in seal['files'].items():
        if sha(source/rel)!=expected: raise ValueError('Export changed: '+rel)
    inspect_boundary(source,seal['boundary'])
    return seal


def target_spec(original, config):
    # Preserve task definitions, models and cumulative budget; replace only host
    # execution paths/resources, then explicitly enable the new search protocol.
    spec=copy.deepcopy(original)
    spec['execution']=copy.deepcopy(config['execution'])
    spec['execution']['host_name']=socket.gethostname()
    spec['model']['credential_file']=config['model']['credential_file']
    spec['search'].update(SEARCH)
    spec['round_robin'].update(active_tasks=4,active_batches=4,
        retire_on_medal={'from_round':1,'activation_round':4,'basis':'official_private_medal','exploratory':True})
    spec['offline_max']={'boundary':4,'refresh_before_online':True,'version':1}
    spec.pop('continuation_execution_fingerprint',None)
    spec['protocol_notes']=['Exploratory cross-host continuation: inherited validation history; new offline/search and execution protocol.',
        'GPU0-3, physical CPU0-31; 4-core GPU and 8-core CPU jobs; four concurrent offline task loops.',
        '48 tuning trials, 5 policy versions, 70% growing replay and counterfactual enabled.']
    validate(spec)
    return spec


def prepare(source, destination, config, boundary=4):
    source,destination=source.resolve(),destination.resolve()
    if destination.exists(): raise ValueError('New run directory required')
    seal=verify_export(source)
    if seal['boundary']!=boundary: raise ValueError('Wrong export boundary')
    old=read(source/'frozen-config.json')
    spec=target_spec(old['spec'],config)
    spec['offline_max']['boundary']=boundary
    spec['round_robin']['retire_on_medal']['activation_round']=boundary
    # Copy directly into the final location so stored output_dir and lineage are
    # stable. A partial preparation has NO ready marker and cannot launch.
    copy_boundary(source,destination,seal['tasks'])
    lineage=dict(parent=str(source),boundary=boundary,tasks=seal['tasks'],
        export_seal_sha256=sha(source/'export-seal.json'),parent_execution=old['spec']['execution'],
        parent_search=old['spec']['search'],parent_budget=old['spec']['budget'],
        budget_sha256=sha(source/'budget.json') if (source/'budget.json').exists() else None,
        classification='exploratory private-feedback allocation; cross-host public-history continuation')
    write(destination/'offline-max-lineage.json',lineage)
    # Inventory describes the 4090 environment; fixed validation assets stay byte-identical.
    if old['mode']=='real':
        python=Path(spec['execution']['python_environment'])/'bin/python'
        subprocess.run([str(python),str(REPO/'mle_rsi/runtime_inventory.py'),str(WORKSPACE),
            str(destination/'runtime/inventory.json'),json.dumps(spec['execution'])],check=True,timeout=120)
    assets={str(p.relative_to(destination)):sha(p) for p in
        [*sorted((destination/'prepared').glob('*/manifest.json')),*sorted((destination/'runtime').glob('*'))] if p.is_file()}
    record=copy.deepcopy(old)
    record.update(spec=spec,source_hashes=source_manifest(),asset_hashes=assets,
                  offline_max_lineage_sha256=sha(destination/'offline-max-lineage.json'))
    write(destination/'frozen-config.json',record)
    for task, evidence in seal['tasks'].items():
        cfg=campaign(destination,task,'development',config_only=True)
        with opened(destination/'campaigns'/('dev-'+task),False) as store:
            store.put('metadata','offline_max_parent_config',store.get('metadata','config'))
            store.put('metadata','offline_max_parent_runtime',store.get('metadata','runtime'))
            store.put('metadata','config',asdict(cfg))
        c=campaign(destination,task,'development',resource_pool=object(),connect_transport=False)
        try:
            current=c.task_backend.fingerprint()
            for key in ('task','metric','description_sha256','contract','validation_manifest','mock'):
                if current[key]!=evidence['backend'][key]: raise ValueError('Validation changed: '+task+' '+key)
        finally: c.store.close()
    if lineage['budget_sha256'] and sha(destination/'budget.json')!=lineage['budget_sha256']:
        raise ValueError('Budget was not preserved')
    from .medal_retirement import retire_winners
    retired=retire_winners(destination,spec,old['mode'])
    write(destination/'web-token.json',{'token':secrets.token_urlsafe(32)})
    control(destination,'pause')
    write(destination/'round-status.json',{'state':'ready_offline_refresh','round':boundary+1,'updated_at':time.time()})
    write(destination/'offline-max-ready.json',{'code_dir':str(REPO),'boundary':boundary,'retired':list(retired),
        'frozen_config_sha256':sha(destination/'frozen-config.json'),'created_at':time.time()})
    return destination


async def refresh(c, root, boundary):
    """Must complete before this task can admit fifth-round candidates."""
    result=await c.refresh_offline(boundary)
    versions=[v for key,v in c.store.list('policy') if key.startswith(f'offline-max-r{boundary:04d}-')]
    tunes=[v for key,v in c.store.list('policy_tuning') if key.startswith(f'offline-max-r{boundary:04d}-')]
    counter=[v for key,v in c.store.list('counterfactual_feedback') if key.startswith(f'dream:offline-max-r{boundary:04d}-')]
    if len(versions)!=5 or not any(t.get('selected') is not None for t in tunes) or len(counter)!=4:
        raise RuntimeError('Offline-max did not exercise five versions, parameter tuning and four counterfactual reports; inspect evidence')
    write(root/'offline-max'/f'{c.task_backend.task_id}.json',{**result,
        'tuning_versions':len(tunes),'actual_trials':[len(t.get('trials',[])) for t in tunes],
        'counterfactual_reports':len(counter),'completed_at':time.time()})


class OfflinePrompts:
    def __init__(self, wrapped): self.wrapped=wrapped
    def build(self, base, feedback):
        messages=self.wrapped.build(base,feedback)
        messages[0]={**messages[0], 'content':messages[0]['content'] +
            "\nOffline-max requires a nonempty literal Policy.PARAM_SPACE with useful numeric knobs "
            "actually read by the policy, each assigned a numeric class literal within its bounds. "
            "Host tuning runs up to 48 combinations before evaluation. Do not add unused knobs. "
            "Use counterfactual feedback as evidence; it is simulated, not a measured training result."}
        return messages


def preflight(config):
    e=config['execution']
    if e['cpu_ids']!=list(range(32)) or e['gpu_pool']!=[0,1,2,3]:
        raise ValueError('This launcher requires the agreed half-machine allocation')
    if not set(e['cpu_ids'])<=os.sched_getaffinity(0): raise ValueError('CPU affinity unavailable')
    for path in (Path(e['python_environment'])/'bin/python',Path(e['pretrained_models']),Path(config['model']['credential_file'])):
        if not path.exists(): raise ValueError('Missing configured path: '+str(path))
    for dependency in e.get('python_extra_mounts',[]):
        if not Path(dependency).is_dir():raise ValueError('Missing Python dependency mount: '+dependency)
    import xml.etree.ElementTree as ET
    devices=ET.fromstring(subprocess.check_output(['nvidia-smi','-q','-x'])).findall('gpu')
    for gpu in e['gpu_pool']:
        cfg=read(Path(e['gpu_config_dir'])/f'sysbox-gpu{gpu}.json')
        expected='/dev/nvidia'+devices[gpu].findtext('minor_number')
        if expected+':'+expected+':rw' not in cfg['devices']:
            raise ValueError('Stale GPU device mapping: '+str(gpu))
    subprocess.run(['docker','image','inspect',e['image']],stdout=subprocess.DEVNULL,check=True)
    result=subprocess.check_output(['nvidia-smi','--query-gpu=index,name','--format=csv,noheader'],text=True)
    if len([line for line in result.splitlines() if '4090' in line])!=8: raise ValueError('Expected this eight-4090 host')
    return {'cpu_ids':e['cpu_ids'],'gpu_pool':e['gpu_pool'],'code_dir':str(REPO)}


def start(source,root,config):
    from .offline_max_launch import launch
    preflight(config)
    with lock(root.parent/(root.name+'-launcher'),blocking=False):
        if not root.exists(): prepare(source,root,config)
        ready=read(root/'offline-max-ready.json')
        if ready['code_dir']!=str(REPO) or ready['frozen_config_sha256']!=sha(root/'frozen-config.json'):
            raise ValueError('Prepared deployment changed')
        frozen(root)
        if read(root/'offline-max-lineage.json')['parent']!=str(source.resolve()): raise ValueError('Source differs from prepared lineage')
        from .__main__ import preflight as check_provider_and_runtime
        check_provider_and_runtime(frozen(root)['spec'])  # authenticated GET only, no generation
        launch(root)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('command',choices=['export','check','prepare','start'])
    p.add_argument('--source',type=Path,default=DEFAULT_EXPORT)
    p.add_argument('--destination',type=Path)
    p.add_argument('--run-dir',type=Path,default=DEFAULT_RUN)
    p.add_argument('--config',type=Path,default=REPO/'configs/offline-max-4090-half.json')
    a=p.parse_args()
    if a.command=='export':
        print(export(a.source,a.destination or DEFAULT_EXPORT)); return
    config=read(a.config)
    if a.command=='check': print(json.dumps(preflight(config),indent=2)); return
    if a.command=='prepare': print(prepare(a.source,a.run_dir,config)); return
    start(a.source,a.run_dir.resolve(),config)

if __name__=='__main__': main()
