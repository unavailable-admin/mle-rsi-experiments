"""Fork a stopped run, resetting one task without resetting any spending."""
import copy
import json
import os
from urllib.parse import quote
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from pathlib import Path
import secrets
import shutil
import sqlite3
import time

from .common import lock, read, write, sha, request_namespace
from .experiment import campaign, source_manifest
from .rounds import unresolved
from .stage2 import inspect_stopped_source
from .validation import verify_prepared
from dream_rsi.storage import CampaignStore


def seed_readable_index(source, destination):
    """Seed primary derived links, avoiding eager decoding of all old records."""
    for name in ('README.md', 'index.md'):
        if (source/name).exists():
            shutil.copy2(source/name, destination/name)
    connection = sqlite3.connect(destination/'campaign.sqlite')
    try:
        rows = connection.execute('SELECT kind,key,payload FROM records').fetchall()
    finally:
        connection.close()
    def seed(row):
        kind, key, payload = row
        component = lambda value: quote(str(value), safe=':_-').replace('.', '%2E') or '%EMPTY'
        path = destination/'evidence'/component(kind)/component(key)/(kind+'.json')
        target = destination/json.loads(payload)['path']
        if not target.is_file():
            raise ValueError('Missing inherited canonical object: '+str(target))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.symlink_to(os.path.relpath(target, path.parent))
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(seed, rows))


def clone_campaign(source, destination):
    """Copy mutable SQLite/workspaces; reference immutable historical objects."""
    destination.mkdir(parents=True)
    old = sqlite3.connect(f'file:{source}/campaign.sqlite?mode=ro', uri=True)
    new = sqlite3.connect(destination/'campaign.sqlite')
    try:
        old.backup(new)
    finally:
        old.close(); new.close()
    objects = destination/'artifacts/objects'
    objects.mkdir(parents=True)
    def link_object(path):
        if not path.name.startswith('.'):
            (objects/path.name).symlink_to(path, target_is_directory=True)
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(link_object, (source/'artifacts/objects').iterdir()))
    # Execution assessments are complete at the drained boundary. Existing
    # attempts reuse them; all future executions get new directories locally.
    executions = destination/'executions'
    executions.mkdir()
    if (source/'executions').exists():
        for path in (source/'executions').iterdir():
            (executions/path.name).symlink_to(path.resolve(), target_is_directory=path.is_dir())
    # Keep full human-readable mirrors in the parent; primary record links
    # satisfy the store's index check and future writes add local detail.
    seed_readable_index(source, destination)
    for name in ('workspaces',):
        if (source/name).exists():
            shutil.copytree(source/name, destination/name)


def prepare(source, destination, task, validation, namespace):
    source, destination, validation = map(lambda p: Path(p).resolve(), (source, destination, validation))
    if destination.exists():
        raise ValueError('Destination already exists')
    with lock(source/'controller', blocking=False):
        old = read(source/'frozen-config.json')
        if read(source/'round-status.json')['state'] not in ('stopped', 'paused', 'target_reached'):
            raise ValueError('Stop and drain the source controller first')
        spec = copy.deepcopy(old['spec'])
        if not spec.get('stage2') or task not in spec['experiment_tasks']:
            raise ValueError('Expected a stage2 run containing the reset task')
        retired = read(source/'retired-tasks.json') if (source/'retired-tasks.json').exists() else {}
        if task in retired:
            raise ValueError('Do not reset a sealed medal task')
        if any(unresolved(source, t) for t in spec['experiment_tasks']):
            raise ValueError('New unresolved requests/executions require reconciliation before forking')
        if old['mode'] == 'real':
            inspect_stopped_source(source)
            meta = verify_prepared(validation)
            if meta['task'] != task or sha(validation/'manifest.json') == sha(source/'prepared'/task/'manifest.json'):
                raise ValueError('Expected a new validation split for the reset task')
        spec['stage2'].setdefault('task_namespaces', {})[task] = namespace
        request_namespace(spec, task)
        ledger = read(source/'budget.json')
        if any(k.startswith(namespace+':') for k in ledger['api']) or any('/'+namespace+'-' in k for k in ledger['execution']):
            raise ValueError('Reset namespace was already used')
        destination.mkdir(parents=True)
        for name in ('runtime', 'prepared'):
            shutil.copytree(source/name, destination/name)
        if old['mode'] == 'real':
            shutil.rmtree(destination/'prepared'/task)
            (destination/'prepared'/task).mkdir()
            for name in ('manifest.json', *meta['file_hashes']):
                dst = destination/'prepared'/task/name
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(validation/name, dst)
            verify_prepared(destination/'prepared'/task)
        tasks = {}
        for t in spec['experiment_tasks']:
            if t == task:
                tasks[t] = {'round': 0, 'protocol_hash': None, 'reset': True}
                continue
            src = source/'campaigns'/f'dev-{t}'
            dst = destination/'campaigns'/f'dev-{t}'
            if t in retired:
                dst.parent.mkdir(parents=True, exist_ok=True)
                dst.symlink_to(src, target_is_directory=True)
            else:
                clone_campaign(src, dst)
            store = CampaignStore(dst, read_only=True)
            try:
                cp = store.get('checkpoint', 'active')
                tasks[t] = {'round': cp['completed_rounds'], 'phase': cp['phase'],
                            'protocol_hash': store.get('metadata', 'protocol_hash'),
                            'nodes': store.connection.execute("SELECT count(*) FROM records WHERE kind='node'").fetchone()[0]}
            finally:
                store.close()
            status_path = source/'task-status'/f'{t}.json'
            if status_path.exists():
                write(destination/'task-status'/f'{t}.json', read(status_path))
        # Keep immutable reports under their original task round numbers.
        for folder in sorted((source/'rounds').glob('*/*')):
            if folder.name == task or not folder.is_dir():
                continue
            dst = destination/'rounds'/folder.parent.name/folder.name
            dst.mkdir(parents=True)
            for path in folder.iterdir():
                (dst/path.name).symlink_to(path.resolve(), target_is_directory=path.is_dir())
        # Saved transport responses prevent resending historical request IDs.
        if (source/'api').exists():
            shutil.copytree(source/'api', destination/'api')
        write(destination/'budget.json', ledger)
        previous = read(source/'stage2-lineage.json')
        lineage = {'parent': str(source), 'created_at': time.time(), 'tasks': tasks,
                   'increment': {'api_usd_total': 0}, 'authorization': 'User approved reset Cactus on the new split; continue others; no additional budget.',
                   'historical_unsettled': previous['historical_unsettled'],
                   'parent_frozen_sha256': sha(source/'frozen-config.json'),
                   'parent_budget_sha256': sha(source/'budget.json'),
                   'reset_task': task, 'namespace': namespace,
                   'immutable_history_dependency': str(source),
                   'note': 'Preserves partial rounds of other tasks. Cactus has no inherited nodes, policy, or old-split scores.'}
        write(destination/'stage2-lineage.json', lineage)
        assets = {str(p.relative_to(destination)): sha(p) for p in
                  [*sorted((destination/'prepared').glob('*/manifest.json')), *sorted((destination/'runtime').glob('*'))] if p.is_file()}
        record = {**old, 'spec': spec, 'source_hashes': source_manifest(), 'asset_hashes': assets,
                  'stage2_lineage_sha256': sha(destination/'stage2-lineage.json')}
        write(destination/'frozen-config.json', record)
        for t in spec['experiment_tasks']:
            if t in retired:
                continue
            cfg = campaign(destination, t, 'development', config_only=True)
            store = CampaignStore(destination/'campaigns'/f'dev-{t}')
            store.put('metadata', 'config', asdict(cfg)); store.close()
            # Constructor verifies inherited runtime/evaluation identities, but
            # does not generate, train, or bootstrap the reset campaign.
            c = campaign(destination, t, 'development', resource_pool=object(), connect_transport=False)
            if t == task:
                assert not c.store.list('node') and c.store.get('checkpoint', 'active') is None
            c.store.close()
        write(destination/'retired-tasks.json', retired)
        if (source/'round-timing.json').exists():
            shutil.copy2(source/'round-timing.json', destination/'round-timing.json')
        write(destination/'web-token.json', {'token': secrets.token_urlsafe(32)})
        write(destination/'control.json', {'action': 'pause', 'requested_at': time.time()})
        write(destination/'round-status.json', {'state': 'ready', 'updated_at': time.time()})
        write(destination/'scheduler.json', {'active': [], 'waiting': []})
        write(destination/'stage2-ready.json', {'frozen_sha256': sha(destination/'frozen-config.json'), 'created_at': time.time()})
        assert read(destination/'budget.json') == ledger == read(source/'budget.json')
        assert record['spec']['budget'] == old['spec']['budget']
    return destination
