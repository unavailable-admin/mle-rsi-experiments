"""Reconcile external evidence only; never edits Dream-RSI internal tables."""
from pathlib import Path
from .io import read
from .ledger import EvidenceConflict


class OutcomeUnknown(RuntimeError):
    pass


def execution(root, ident, input_hash, ledger):
    row=ledger.get(ident)
    if row is None:return None
    if row['input_hash']!=input_hash:raise EvidenceConflict('Execution input changed')
    directory=Path(root)
    if not any((directory/'execution'/name).exists() for name in ('resources.json','interrupted-resources.json')) and (directory/'execution/container.json').exists():
        from .containers import recover_container
        recover_container(directory/'execution')
    for name in ('resources.json','interrupted-resources.json'):
        path=directory/'execution'/name
        if path.exists():
            result=read(path)
            if name.startswith('interrupted') and not result.get('accounting_verified'):continue
            ledger.record(ident,'completed',{'resources':str(path)},
                          {'execution_seconds':result['wall_seconds'],'gpu_seconds':result['gpu_allocated_seconds']})
            return result
    if row['state'] in ('running','unknown','completed'):
        # A surviving container is never launched again. The operator can supply
        # verified termination/resources evidence; reservation remains untouched.
        raise OutcomeUnknown('Execution has no terminal measurement: '+ident)
    return None
