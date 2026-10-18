"""Portable public histories and accounting, imported only into new runs.

Legacy storage-format-2 campaigns are read through Dream-RSI's read-only store.
No active source run, its configuration, snapshots or internal tables are edited.
"""
from dataclasses import replace
from pathlib import Path
import shutil
import tempfile
import sqlite3
from dream_rsi.checkpoint_io import export_history, import_history, verify, seal, check_import_target
from dream_rsi.storage import CampaignStore
from .io import read, write, sha, digest, lock
from . import validation, tasks, release
from .backend import assemble
from .ledger import Ledger
from .resources import ResourcePool


def validation_identity(root, task_id, public_dir=None, mock=False):
    if mock:
        return {'task': task_id, 'mode': 'mock', 'protocol': 1}
    prepared = Path(root) / 'prepared' / task_id
    validation.verify_prepared(prepared)
    manifest = read(prepared / 'manifest.json')
    if public_dir is None:
        raise ValueError('Legacy export requires an explicit public data directory')
    public = Path(public_dir)
    if not public.is_dir():
        raise ValueError('Missing public data directory')
    return {'task': task_id, 'manifest': manifest, 'metric': tasks.metric(manifest),
            'public_files': {str(p.relative_to(public)): sha(p) for p in sorted(public.rglob('*')) if p.is_file()}}


def accounting_snapshot(root):
    root = Path(root)
    if (root / 'ledger.sqlite').exists():
        db = sqlite3.connect((root / 'ledger.sqlite').as_uri() + '?mode=ro', uri=True)
        try:
            import json
            rows = [{'id': r[0], 'task': r[1], 'input_hash': r[2], 'state': r[3],
                     'amounts': json.loads(r[4]), 'evidence': json.loads(r[5])}
                    for r in db.execute('SELECT id,task,input_hash,state,amounts,evidence FROM entries ORDER BY id')]
        finally:
            db.close()
    else:
        old = read(root / 'budget.json')
        rows = []
        for kind in ('api', 'execution'):
            for key, value in sorted(old[kind].items()):
                amounts = {'api_usd': value.get('charged_usd', value.get('reserved_usd', 0))} if kind == 'api' else {
                    'execution_seconds': value['wall_seconds'], 'gpu_seconds': value.get('gpu_seconds', 0)}
                rows.append({'id': kind + ':' + key, 'task': value['task'], 'input_hash': digest(value),
                             'state': value['state'], 'amounts': amounts, 'evidence': value})
    # All source costs, including later discarded rounds or excluded tasks, are
    # preserved. Forking/rollback cannot erase spending or unknown reservations.
    return {'source_id': digest(str(root.resolve())), 'rows': rows}


def export_run(root, task_id, destination, through_round=None, campaign_id=None, public_dir=None):
    destination = Path(destination).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    with lock(str(destination) + '.export'):
        if destination.exists():
            raise FileExistsError(destination)
        # Only publish the final name after both core history and platform
        # accounting/outputs have been sealed successfully.
        with tempfile.TemporaryDirectory(prefix='.history-export-', dir=destination.parent) as staging:
            partial = Path(staging) / 'bundle'
            result = _export_run(root, task_id, partial, through_round, campaign_id, public_dir)
            partial.rename(destination)
            return result


def _export_run(root, task_id, destination, through_round=None, campaign_id=None, public_dir=None):
    root, destination = Path(root).resolve(), Path(destination).resolve()
    spec = read(root / 'config.json') if (root / 'config.json').exists() else None
    if spec:
        task = next((t for t in spec['tasks'] if t['id'] == task_id), None)
        if task is None:
            raise ValueError('Task not in source run')
        public_dir = task.get('public_dir', public_dir)
    campaign_id = campaign_id or task_id
    if not campaign_id or Path(campaign_id).name != campaign_id or campaign_id in ('.', '..'):
        raise ValueError('Invalid source campaign ID')
    identity = validation_identity(root, task_id, public_dir, bool(spec and spec['mode'] == 'mock'))
    export_history(root / 'campaigns' / campaign_id, destination, identity, through_round)
    exported = CampaignStore(destination / 'campaign', read_only=True)
    outputs = {}
    try:
        for _, payload in exported.list('export_tree'):
            for node in payload['nodes']:
                report = node.evaluation
                path = report.diagnostics.get('output_dir')
                if not path or not report.evaluated or not report.valid or path in outputs:
                    continue
                source = Path(path)
                expected = report.diagnostics.get('submission_sha256')
                if not expected or sha(source / 'submission.csv') != expected:
                    raise ValueError('Historical selected output is unavailable or changed')
                relative = 'outputs/' + digest(path)
                target = destination / relative
                target.mkdir(parents=True)
                for name in ('submission.csv', 'validation_predictions.csv', 'training-contract.json', 'candidate-result.json', 'validation-split.json'):
                    item = source / name
                    if item.exists():
                        if item.is_symlink() or not item.is_file():
                            raise ValueError('Unsafe historical candidate output')
                        shutil.copyfile(item, target / name)
                outputs[path] = relative
    finally:
        exported.close()
    write(destination / 'platform.json', {'schema': 1, 'task': task_id, 'validation_identity': identity,
          'accounting': accounting_snapshot(root), 'outputs': outputs,
          'source_run': str(root), 'source_kind': 'platform' if spec else 'legacy'})
    return seal(destination)


async def import_run(root, task_id, bundle):
    root, bundle = Path(root).resolve(), Path(bundle).resolve()
    sealed = verify(bundle)
    meta = read(bundle / 'platform.json')
    if meta['schema'] != 1 or meta['task'] != task_id:
        raise ValueError('Exported task does not match destination')
    spec = read(root / 'config.json')
    release.verify(root, spec)
    task = next(t for t in spec['tasks'] if t['id'] == task_id)
    identity = validation_identity(root, task_id, task.get('public_dir'), spec['mode'] == 'mock')
    if identity != meta['validation_identity']:
        raise ValueError('Task data, metric or validation protocol differs')
    with lock(str(root / 'controller')):
        receipt_path = root / 'imports' / sealed['bundle_id'] / 'receipt.json'
        if receipt_path.exists():
            return read(receipt_path)
        ledger = Ledger(root, spec.get('budget', {}))
        campaign = None
        try:
            pool = ResourcePool(spec['host'], mock=spec['mode'] == 'mock')
            # Import performs no generation or execution; real external model
            # assembly still requires a transport object, never a live request.
            import asyncio
            from .provider import model_service
            service = model_service(root, task_id, ledger, {**spec['provider'], 'model': spec['model']}, asyncio.Semaphore(1)) if spec['mode'] == 'real' else None
            campaign = assemble(root, task, spec, ledger, pool, service)
            await check_import_target(campaign, sealed['bundle_id'])
            for row in meta['accounting']['rows']:
                ident = 'inherited:' + meta['accounting']['source_id'] + ':' + row['id']
                ledger.reserve(ident, row['task'], row['input_hash'], row['amounts'])
                state = 'completed' if row['state'] == 'completed' else 'unknown'
                ledger.record(ident, state, {'source_run': meta['source_run'], 'source_record': row}, row['amounts'])
            outputs = {}
            for old, relative in meta['outputs'].items():
                source = bundle / relative
                if bundle not in source.resolve().parents:
                    raise ValueError('Unsafe output mapping')
                target = receipt_path.parent / 'outputs' / digest(old)
                if not target.exists():
                    shutil.copytree(source, target)
                # A crashed previous import may have copied only part of a tree.
                for original in source.iterdir():
                    if not (target / original.name).is_file() or sha(original) != sha(target / original.name):
                        shutil.copyfile(original, target / original.name)
                outputs[old] = str(target)
            def adapt(report):
                diagnostic = dict(report.diagnostics)
                if diagnostic.get('output_dir') in outputs:
                    diagnostic['output_dir'] = outputs[diagnostic['output_dir']]
                metrics = dict(report.metrics)
                if 'raw_metric' not in metrics and 'validation_score' in metrics:
                    metrics['raw_metric'] = metrics['validation_score']
                if report.evaluated and report.valid and spec['mode'] == 'real':
                    raw = metrics.get('raw_metric')
                    if raw is None or abs(tasks.search_score(raw, identity['metric']) - report.score) > 1e-10:
                        raise ValueError('Historical raw metric/search score mapping differs')
                return replace(report, diagnostics=diagnostic, metrics=metrics)
            receipt = await import_history(campaign, bundle, identity, adapt)
            write(receipt_path, receipt)
            write(root / 'status' / (task_id + '.json'), {'task': task_id, 'state': 'imported',
                'summary': campaign.summary(), 'import_receipt': receipt})
            return receipt
        finally:
            if campaign:
                campaign.close()
            ledger.close()
