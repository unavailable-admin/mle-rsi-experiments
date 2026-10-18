"""Explicit exploratory continuation in a NEW root; never mutates its source run."""
import argparse
import asyncio
import copy
from dataclasses import asdict
from pathlib import Path
import shutil
import sqlite3
import time

from mle_rsi.common import read, write, sha, lock
from mle_rsi.experiment import initialize, campaign, frozen
from mle_rsi.single_task import completed_search, validate, store_path, save_report
from dream_rsi.storage import CampaignStore


def prepare(source, destination, target=200, additional_cap=6):
    source, destination = source.resolve(), destination.resolve()
    if destination.exists():
        raise ValueError('Extension requires a fresh directory')
    with lock(source/'controller', blocking=False):
        original = frozen(source)
        seal = completed_search(source, original['spec'])
        if read(source/'single-status.json')['state'] != 'completed':
            raise ValueError('Source must have completed successfully')
        inherited = seal['candidate_nodes']
        spec = copy.deepcopy(original['spec'])
        per_round = spec['search']['workers']*spec['search']['batches_per_round']
        if target <= inherited or target % per_round:
            raise ValueError('Target must add complete rounds')
        budget = read(source/'budget.json')
        if any(v['state'] != 'completed' for v in budget['api'].values()):
            raise ValueError('Unsettled source API request')
        if any(v['state'] != 'completed' for v in budget['execution'].values()):
            raise ValueError('Unfinished source execution')
        prior = sum(v['charged_usd'] for v in budget['api'].values())
        spec['search']['development_rounds'] = target//per_round
        spec['single_task']['checkpoint_candidates'] = list(range(inherited+50,target+1,50))
        if not spec['single_task']['checkpoint_candidates'] or spec['single_task']['checkpoint_candidates'][-1] != target:
            spec['single_task']['checkpoint_candidates'].append(target)
        for key in ('api_usd_total','api_usd_per_task','development_api_usd_per_task'):
            spec['budget'][key] = round(prior+additional_cap,8)
        spec['protocol_notes'] += [
            f'Exploratory extension of {source.name}: inherit {inherited} public candidate records; continue to {target}.',
            'Source private evaluations were already inspected by the operator. They are excluded from imported search evidence; this is not an independent preregistered run.',
            f'Preserve historical charges; additional API ceiling USD{additional_cap}. No automatic extension beyond the target.']
        validate(spec)
        initialize(destination,spec,original['mode'])
        # Create/validate the target contract before importing its public history.
        fresh = campaign(destination,'spooky-author-identification','development')
        config = asdict(fresh.config)
        protocol = fresh.protocol_hash
        fresh.store.close()
        src, dst = store_path(source), store_path(destination)
        with _store(src) as old:
            if old.get('metadata','protocol_hash') != protocol:
                raise ValueError('Validation/runtime protocol changed')
            before_config = old.get('metadata','config')
        for rel, digest in original.get('asset_hashes',{}).items():
            if sha(destination/rel) != digest:
                raise ValueError('Prepared assets changed: '+rel)
        # Only the public search artifact store and SQLite are imported. No
        # checkpoints, official results, reports, private labels or API secrets.
        shutil.copytree(src/'artifacts',dst/'artifacts',dirs_exist_ok=True)
        incoming=sqlite3.connect((src/'campaign.sqlite').as_uri()+'?mode=ro',uri=True)
        outgoing=sqlite3.connect(dst/'campaign.sqlite')
        incoming.backup(outgoing);outgoing.close();incoming.close()
        with _store(dst,False) as store:
            cp=store.get('checkpoint','active')
            assert cp['completed_rounds']==inherited//per_round
            assert len(store.list('node'))==inherited
            store.put('metadata','extension_original_config',before_config)
            store.put('metadata','config',config)
        write(destination/'budget.json',budget)
        write(destination/'extension-lineage.json',{
            'source_run':str(source),'source_config_sha256':sha(source/'frozen-config.json'),
            'source_completion_seal_sha256':sha(source/'search-complete.json'),
            'inherited_candidates':inherited,'target_candidates':target,
            'additional_candidate_slots':target-inherited,'inherited_api_usd':prior,
            'additional_api_cap_usd':additional_cap,'cumulative_api_cap_usd':spec['budget']['api_usd_total'],
            'private_evaluation_imported':False,'operator_previously_saw_private_results':True,
            'classification':'exploratory public-history continuation, not independent replication',
            'created_at':time.time()})
        c=campaign(destination,'spooky-author-identification','development')
        try:
            cp=asyncio.run(c.bootstrap())
            assert cp['completed_rounds']==inherited//per_round
            assert len(c.store.list('novelty_claim'))>=inherited
            print('Verified public continuation:',len(c.store.list('node')),'nodes; best validation',cp['best_node'].evaluation.metrics['validation_score'],flush=True)
        finally:c.store.close()
        write(destination/'single-status.json',{'state':'ready_extension','updated_at':time.time()})
        save_report(destination)

from contextlib import contextmanager
@contextmanager
def _store(path,readonly=True):
    store=CampaignStore(path,read_only=readonly)
    try:yield store
    finally:store.close()

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source',type=Path,required=True)
    p.add_argument('--destination',type=Path,required=True)
    p.add_argument('--target',type=int,default=200)
    p.add_argument('--additional-api-cap',type=float,default=6)
    a=p.parse_args();prepare(a.source,a.destination,a.target,a.additional_api_cap)
