"""Prepare an audited per-task boundary continuation; never launch by default."""
import argparse
import copy
from dataclasses import asdict
import json
from pathlib import Path
import re
import secrets
import shutil
import sqlite3
import time
import subprocess
import os
import socket

from .common import read, write, sha, lock
from .experiment import source_manifest, campaign, frozen
from .rounds import verify_seal, round_slot_evidence
from dream_rsi.storage import CampaignStore

INCREMENT = {'api_usd_total':150, 'api_usd_per_task':15,
             'development_api_usd_per_task':15, 'execution_seconds_per_task':180000,
             'development_execution_seconds_per_task':180000, 'gpu_seconds_per_task':180000}


def boundary_audit(source):
    source=Path(source).resolve()
    record=read(source/'frozen-config.json')
    if read(source/'round-status.json')['state'] not in ('stopped','paused','target_reached'):
        raise ValueError('Source controller must be stopped')
    retired=read(source/'retired-tasks.json') if (source/'retired-tasks.json').exists() else {}
    tasks={}
    for task in record['spec']['experiment_tasks']:
        store=CampaignStore(source/'campaigns'/('dev-'+task),read_only=True)
        try:
            choices=[]
            trees=dict(store.list('tree'))
            for out in sorted((source/'rounds').glob('*/'+task)):
                if not (out/'seal.json').exists() or not (out/'official-result.json').exists():continue
                verify_seal(out)
                result=read(out/'official-result.json')
                if result['state'] not in ('graded','no_submission','verification_only'):continue
                number=int(out.parent.name)
                summary=store.get('round_summary',str(number))
                if summary is None or store.get('round_best',str(number)) is None:continue
                # Historical seals count physical nodes; a complete prefix cannot
                # contain missing slots. Audit all five batches of every tree.
                valid=True
                for n in range(1,number+1):
                    tree=trees.get(f'tree-{n:04d}')
                    if tree is None or len(tree['rounds'])!=5:valid=False;break
                    if any(len(step.parent_ids)!=5 or step.missing_parent_ids for step in tree['rounds']):valid=False;break
                if valid:choices.append(number)
            if not choices:raise ValueError('No sealed complete boundary: '+task)
            n=max(choices)
            tasks[task]={'round':n,'retired':task in retired,
                'protocol_hash':store.get('metadata','protocol_hash'),
                'backend':store.get('metadata','task_backend'),
                'seal_sha256':sha(source/'rounds'/f'{n:04d}'/task/'seal.json')}
        finally:store.close()
    return tasks


def budget_for_stage(ledger, previous, tasks):
    budget=copy.deepcopy(previous)
    budget['api_usd_total']=sum(v.get('charged_usd',v['reserved_usd']) for v in ledger['api'].values())+INCREMENT['api_usd_total']
    budget['task_caps']={}
    for task in tasks:
        api=[v for v in ledger['api'].values() if v['task']==task]
        execution=[v for v in ledger['execution'].values() if v['task']==task]
        budget['task_caps'][task]={
            'api_usd_per_task':sum(v.get('charged_usd',v['reserved_usd']) for v in api)+15,
            'development_api_usd_per_task':sum(v.get('charged_usd',v['reserved_usd']) for v in api if v['phase']=='development')+15,
            'execution_seconds_per_task':sum(v['wall_seconds'] for v in execution)+180000,
            'development_execution_seconds_per_task':sum(v['wall_seconds'] for v in execution if v['phase']=='development')+180000,
            'gpu_seconds_per_task':sum(v['gpu_seconds'] for v in execution)+180000}
    return budget


def _within(key, number):
    rounds=[int(x) for x in re.findall(r'(?:tree-|(?:^|:)r)(\d{4})',key)]
    return all(n<=number for n in rounds)


def import_store(source, destination, number):
    """Import only public boundary evidence, rebuilding caches from retained nodes."""
    old=CampaignStore(source,read_only=True)
    destination.mkdir(parents=True)
    # Content-addressed objects may include later bytes but are inaccessible to
    # search without records; preserve all referenced snapshots without rewriting.
    shutil.copytree(source/'artifacts'/'objects',destination/'artifacts'/'objects')
    new=CampaignStore(destination)
    try:
        numbered={'activation','dream_progress','frozen_pool','round_best','round_summary'}
        keyed={'attempt','attempt_input','evaluation_report','node','online_manifest','paired_feedback',
               'policy','request','response','usage','tree','world','novelty_rejection','provider_error','model_request_skip','policy_fallback'}
        for kind in sorted(numbered|keyed):
            for key,value in old.list(kind):
                keep=int(key)<=number if kind in numbered else _within(key,number)
                if keep:new.put(kind,key,value)
        from dream_rsi.tree import DiscoveryTree
        nodes={node.node_id:node for _,tree in new.list('tree') for node in DiscoveryTree.from_payload(tree).nodes}
        for node in nodes.values():
            for kind,key in (('artifact',node.artifact.artifact_id),('snapshot',node.snapshot_id)):
                value=old.get(kind,key)
                if value is not None:new.put(kind,key,value)
        for key in ('protocol_hash','task_backend'):
            new.put('metadata',key,old.get('metadata',key))
        summary=new.get('round_summary',str(number));pool=new.get('frozen_pool',str(number))
        checkpoint={'campaign_id':source.name,'phase':'ROUND_COMPLETED','completed_rounds':number,
            'round_index':number,'tree_id':f'tree-{number:04d}',
            'history_ids':tuple(f'tree-{i:04d}' for i in range(1,number+1)),
            'best_node':new.get('round_best',str(number))['best_node'],
            'incumbent_id':summary['selected_id'],'selected_id':summary['selected_id'],
            'selected_mean_V':summary['selected_mean_V'],'evaluated_versions':summary['evaluated_versions'],
            'online_report':summary['online'],'pool_hash':pool['pool_hash'],
            'beta1':pool['beta1'],'beta2':pool['beta2'],'protocol_hash':old.get('metadata','protocol_hash')}
        new.put('checkpoint','active',checkpoint)
        from .diversity import source_fingerprint, record_prediction_fingerprints
        for _,node in new.list('node'):
            new.put('novelty_claim',source_fingerprint(node.artifact.source),
                    {'attempt_id':node.attempt.attempt_id,'source_hash':node.artifact.source_hash})
        round_slot_evidence(new,number)
    finally:old.close();new.close()


def prepare(source,destination):
    source,destination=Path(source).resolve(),Path(destination).resolve()
    if destination.exists():raise ValueError('Destination already exists')
    with lock(source/'controller',blocking=False):
        tasks=boundary_audit(source)
        old=read(source/'frozen-config.json');spec=copy.deepcopy(old['spec'])
        live_evidence=inspect_stopped_source(source) if old['mode']=='real' else {'mode':'mock'}
        ledger=read(source/'budget.json')
        # Keep every historical reservation verbatim. New requests use a distinct
        # stage namespace; archived uncertain requests are never replayed.
        spec['budget']=budget_for_stage(ledger,spec['budget'],tasks)
        spec.pop('continuation_execution_fingerprint',None);spec.pop('offline_max',None)
        spec['stage2']={'version':1,'candidate_resources':True,'training_consistency':True,
                        'timeout_bundles':True,'feature_cache':True,'offline_tier':'baseline'}
        from .offline_tiers import apply_tier
        apply_tier(spec)
        spec['execution']['gpu_config_dir']=spec['execution'].get('gpu_config_dir','/opt/mle-rsi-fixes/gpu-configs')
        spec['execution']['task_timeout_seconds']={t:1800 for t in tasks if t in (
            'tabular-playground-series-may-2022','ranzcr-clip-catheter-line-classification','siim-isic-melanoma-classification')}
        destination.mkdir(parents=True)
        for name in ('prepared','runtime'):
            if (source/name).exists():shutil.copytree(source/name,destination/name)
            else:(destination/name).mkdir()
        # Install the new helper without touching the parent run.
        from . import REPO
        shutil.copyfile(REPO/'mle_rsi/candidate_runtime.py',destination/'runtime/mle_runtime.py')
        for task,evidence in tasks.items():
            n=evidence['round']
            for i in range(1,n+1):
                src=source/'rounds'/f'{i:04d}'/task
                if src.exists():shutil.copytree(src,destination/'rounds'/f'{i:04d}'/task)
            import_store(source/'campaigns'/('dev-'+task),destination/'campaigns'/('dev-'+task),n)
        write(destination/'budget.json',ledger)
        lineage={'parent':str(source),'tasks':tasks,'created_at':time.time(),
            'parent_frozen_sha256':sha(source/'frozen-config.json'),'parent_budget_sha256':sha(source/'budget.json'),
            'increment':INCREMENT,'authorization':'User authorized an additional $150 global/$15 per task and 50h execution/GPU per task on 2026-10-17',
            'source_stop_evidence':live_evidence,
            'historical_unsettled':{section:{k:v for k,v in rows.items() if v['state'] in ('pending','running','uncertain')}
                                    for section,rows in ledger.items()},
            'note':'Exploratory public-selected continuation. Incomplete later search excluded, all spending retained. No historical request retried.'}
        write(destination/'stage2-lineage.json',lineage)
        assets={str(p.relative_to(destination)):sha(p) for p in [*sorted((destination/'prepared').glob('*/manifest.json')),*sorted((destination/'runtime').glob('*'))] if p.is_file()}
        record={**old,'spec':spec,'source_hashes':source_manifest(),'asset_hashes':assets,
                'stage2_lineage_sha256':sha(destination/'stage2-lineage.json')}
        write(destination/'frozen-config.json',record)
        for task in tasks:
            cfg=campaign(destination,task,'development',config_only=True)
            store=CampaignStore(destination/'campaigns'/('dev-'+task))
            store.put('metadata','config',asdict(cfg));store.close()
            c=campaign(destination,task,'development',resource_pool=object(),connect_transport=False)
            current=c.task_backend.fingerprint()
            for field in ('task','metric','description_sha256','contract','validation_manifest','mock'):
                if current[field]!=tasks[task]['backend'][field]:raise ValueError('Inherited public validation changed: '+task+' '+field)
            c.store.close()
        if (source/'retired-tasks.json').exists():shutil.copyfile(source/'retired-tasks.json',destination/'retired-tasks.json')
        write(destination/'web-token.json',{'token':secrets.token_urlsafe(32)})
        write(destination/'control.json',{'action':'pause','requested_at':time.time()})
        write(destination/'round-status.json',{'state':'ready','updated_at':time.time()})
        write(destination/'scheduler.json',{'slots':16,'active':[],'waiting':[]})
        write(destination/'stage2-ready.json',{'frozen_sha256':sha(destination/'frozen-config.json'),'created_at':time.time()})
    return destination


def inspect_stopped_source(source):
    import docker
    client=docker.from_env()
    try:
        owned=[]
        for container in client.containers.list():
            if any(str(m.get('Source','')).startswith(str(source)+'/') for m in container.attrs.get('Mounts',[])):
                owned.append(container.id)
        if owned:raise ValueError('Source still has running containers: '+str(owned))
        return {'checked_at':time.time(),'source_running_containers':owned,
                'historical_unsettled_cost_policy':'retain full reservation; actual missing accounting not invented'}
    finally:client.close()


def local_preflight(root):
    record=frozen(root);spec=record['spec'];execution=spec['execution']
    if record['mode']=='mock':return {'mode':'mock'}
    if execution['host_name']!=socket.gethostname():raise ValueError('Wrong execution host')
    if not set(execution['cpu_ids'])<=os.sched_getaffinity(0):raise ValueError('CPU allocation unavailable')
    for path in [Path(execution['python_environment'])/'bin/python',Path(execution['pretrained_models']),Path(spec['model']['credential_file'])]:
        if not path.exists():raise ValueError('Missing runtime input: '+str(path))
    from .validation import verify_prepared
    for task in spec['experiment_tasks']:verify_prepared(root/'prepared'/task)
    subprocess.run(['docker','image','inspect',execution['image']],check=True,stdout=subprocess.DEVNULL)
    import xml.etree.ElementTree as ET
    devices=ET.fromstring(subprocess.check_output(['nvidia-smi','-q','-x'])).findall('gpu')
    for gpu in execution['gpu_pool']:
        cfg=read(Path(execution['gpu_config_dir'])/f'sysbox-gpu{gpu}.json')
        device='/dev/nvidia'+devices[gpu].findtext('minor_number')
        if device+':'+device+':rw' not in cfg['devices']:raise ValueError('GPU mapping changed')
    return {'host':socket.gethostname(),'assets_verified':True,'gpu_mapping_verified':True,'paid_calls':0,'training_started':False}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('command',choices=('audit','prepare','check','view','start'))
    p.add_argument('--source',type=Path,default=Path('/opt/mle-rsi-next/runs/lite22-medal-001'))
    p.add_argument('--run-dir',type=Path,default=Path('/mnt/afs/yangsen1/experiments/lite22-stage2-001/run'))
    a=p.parse_args()
    if a.command=='audit':print(json.dumps(boundary_audit(a.source),indent=2));return
    if a.command=='prepare':print(prepare(a.source,a.run_dir));return
    record=frozen(a.run_dir)
    if sha(a.run_dir/'frozen-config.json')!=read(a.run_dir/'stage2-ready.json')['frozen_sha256']:
        raise ValueError('Prepared run changed')
    result=local_preflight(a.run_dir)
    if a.command=='check':print(json.dumps(result));return
    from .offline_max_launch import launch
    with lock(a.run_dir/'launcher',blocking=False):launch(a.run_dir,ports=(19170,19171,19172),controller=a.command=='start')

if __name__=='__main__':main()
