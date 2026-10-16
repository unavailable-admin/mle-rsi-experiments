"""Audited round-boundary continuation into a separate directory (no budget reset)."""
import argparse
import copy
import shutil
import time
from pathlib import Path
from .common import read,write,sha,lock
from .experiment import source_manifest,campaign,frozen
from .rounds import validate,verify_seal
from .medal_retirement import retire_winners
from dream_rsi.storage import CampaignStore


def migrate(source,destination,number=4):
    source,destination=Path(source).resolve(),Path(destination).resolve()
    if destination.exists():raise ValueError('Destination must not exist')
    with lock(source/'controller',blocking=False):
        old=read(source/'frozen-config.json')
        if read(source/'round-status.json').get('state')!='target_reached':
            raise ValueError('Parent must have reached its requested round boundary')
        ledger=read(source/'budget.json') if (source/'budget.json').exists() else {'api':{},'execution':{}}
        if any(v['state'] in ('pending','running','uncertain') for section in ledger.values() for v in section.values()):
            raise ValueError('Parent has unsettled ledger entries')
        checks={}
        for task in old['spec']['experiment_tasks']:
            with_store=CampaignStore(source/'campaigns'/('dev-'+task),read_only=True)
            try:
                cp=with_store.get('checkpoint','active')
                if cp['completed_rounds']!=number or len(with_store.list('node'))!=number*25:
                    raise ValueError('Not an exact completed round: '+task)
            finally:with_store.close()
            out=source/'rounds'/f'{number:04d}'/task
            verify_seal(out)
            result=read(out/'official-result.json')
            if result['state'] not in ('graded','no_submission','verification_only'):
                raise ValueError('Incomplete grading: '+task)
            checks[task]=sha(out/'official-result.json')
        # Independent mutable files; no hard links to the parent's writable state.
        shutil.copytree(source,destination,ignore=shutil.ignore_patterns('*.pid','*-shm','*-wal'))
        # Copy SQLite through its snapshot API, including any checkpointed WAL data.
        import sqlite3
        for db in source.rglob('*.sqlite'):
            with sqlite3.connect(db.as_uri()+'?mode=ro',uri=True) as src, sqlite3.connect(destination/db.relative_to(source)) as dst:
                src.backup(dst)
        spec=copy.deepcopy(old['spec'])
        spec['continuation_execution_fingerprint']=copy.deepcopy(spec['execution'])
        spec['execution']['cpu_groups']=[spec['execution']['cpu_ids'][i:i+4] for i in range(0,len(spec['execution']['cpu_ids']),4)]
        spec['execution']['mixed_resources']={'max_slots':16,'task_cpu_cores':{}}
        spec['round_robin']['retire_on_medal']={'from_round':1,'activation_round':number,'basis':'official_private_medal','exploratory':True}
        validate(spec)
        migration=dict(parent=str(source),created_at=time.time(),completed_round=number,
            parent_frozen_sha256=sha(source/'frozen-config.json'),parent_source_hashes=old['source_hashes'],
            parent_execution=old['spec']['execution'],grading_hashes=checks,
            budget_sha256=sha(source/'budget.json') if (source/'budget.json').exists() else None,
            note='Exploratory continuation: historical protocol IDs preserved; new execution resources differ. Historical diagnostic paths reference retained parent files. Private medals control retirement and allocation.')
        write(destination/'migration.json',migration)
        new=copy.deepcopy(old);new.update(spec=spec,source_hashes=source_manifest(),migration_sha256=sha(destination/'migration.json'))
        write(destination/'frozen-config.json',new)
        for task in spec['experiment_tasks']:
            store=CampaignStore(destination/'campaigns'/('dev-'+task))
            try:
                previous=store.get('metadata','config');updated=copy.deepcopy(previous)
                updated['output_dir']=str(destination/'campaigns')
                store.put('metadata','migration_parent_config',previous)
                store.put('metadata','config',updated)
                store.put('metadata','execution_migration',migration)
            finally:store.close()
        write(destination/'control.json',{'action':'pause','requested_at':time.time()})
        write(destination/'round-status.json',{'state':'paused','round':number+1,'updated_at':time.time()})
        write(destination/'scheduler.json',{'slots':16,'active':[],'waiting':[],'updated_at':time.time()})
        if (destination/'round-report.json').exists():(destination/'round-report.json').unlink()
        frozen(destination)
        # Constructor validates unchanged search configuration and inherited protocol.
        for task in spec['experiment_tasks']:
            from .adaptive_resources import MixedResourcePool
            c=campaign(destination,task,'development',resource_pool=MixedResourcePool(destination,spec['execution'],old['mode']=='mock'))
            c.store.close()
        retired=retire_winners(destination,spec,old['mode'])
        write(destination/'migration-verified.json',{'at':time.time(),'retired':list(retired),'next_round':number+1,
            'budget_preserved':not migration['budget_sha256'] or sha(destination/'budget.json')==migration['budget_sha256']})
    return destination


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--source',type=Path,required=True);p.add_argument('--destination',type=Path,required=True);p.add_argument('--round',type=int,default=4)
    a=p.parse_args();print(migrate(a.source,a.destination,a.round))
