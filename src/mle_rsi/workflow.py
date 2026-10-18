"""Single-host resumable job queues and gated development/final stages.

Each job uses the ordinary controller and its own immutable run. Dependencies,
public-history inheritance and gates are data, not another agent search loop.
"""
from pathlib import Path
from .io import read, write, digest, lock
from .config import validate
from .controller import initialize, run
from .reporting import status
from .migration import export_run, import_run


def load(path):
    path = Path(path).resolve()
    value = read(path)
    if value.get('schema') != 1 or not isinstance(value.get('jobs'), list) or not value['jobs']:
        raise ValueError('Workflow requires schema=1 and nonempty jobs')
    seen = set()
    jobs = []
    for job in value['jobs']:
        ident = job['id']
        if not isinstance(ident, str) or not ident or any(c not in 'abcdefghijklmnopqrstuvwxyz0123456789-_' for c in ident) or ident in seen:
            raise ValueError('Workflow job IDs must be unique and portable')
        dependencies = job.get('after', [])
        if not isinstance(dependencies, list) or any(d not in seen for d in dependencies):
            raise ValueError('List dependency jobs before their dependents')
        inherit = job.get('inherit_from')
        if inherit is not None and inherit not in dependencies:
            raise ValueError('History source must be an explicit dependency')
        gate = job.get('gate', {})
        if set(gate) - {'min_valid_candidates', 'max_api_usd', 'require_medal'}:
            raise ValueError('Unknown workflow gate')
        if 'min_valid_candidates' in gate and (type(gate['min_valid_candidates']) is not int or gate['min_valid_candidates'] < 0):
            raise ValueError('Invalid candidate gate')
        if 'max_api_usd' in gate and (type(gate['max_api_usd']) not in (int, float) or gate['max_api_usd'] < 0):
            raise ValueError('Invalid cost gate')
        if 'require_medal' in gate and type(gate['require_medal']) is not bool:
            raise ValueError('Invalid medal gate')
        config = job['config']
        if isinstance(config, str):
            config = read(path.parent / config)
        config = validate(config)
        jobs.append({**job, 'config': config})
        seen.add(ident)
    return {'schema': 1, 'jobs': jobs}


def gate_result(job, dependencies):
    gate = job.get('gate', {})
    reasons = []
    for name, result in dependencies.items():
        states = [v['state'] for v in result['tasks'].values()]
        if any(s not in ('completed', 'retired', 'excluded') for s in states):
            reasons.append(name + ': dependency did not complete')
        active = [v for v in result['tasks'].values() if v['state'] != 'excluded']
        if any(v.get('summary', {}).get('valid_candidates', 0) < gate.get('min_valid_candidates', 0) for v in active):
            reasons.append(name + ': insufficient valid candidates')
        if 'max_api_usd' in gate and result['budget']['totals']['api_usd'] > gate['max_api_usd']:
            reasons.append(name + ': cost gate exceeded')
        if gate.get('require_medal') and not result['medals']:
            reasons.append(name + ': medal gate not met')
    return reasons


async def execute(path, root):
    value = load(path)
    root = Path(root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    frozen = root / 'workflow.json'
    with lock(str(root / 'workflow-controller')):
        if frozen.exists() and read(frozen) != value:
            raise ValueError('Workflow is immutable; create a new workflow directory')
        write(frozen, value)
        state_path = root / 'workflow-status.json'
        state = read(state_path) if state_path.exists() else {'jobs': {}}
        for job in value['jobs']:
            ident = job['id']
            prior = state['jobs'].get(ident, {})
            if prior.get('state') == 'completed':
                continue
            dependencies = {d: status(root / 'jobs' / d) for d in job.get('after', [])}
            reasons = gate_result(job, dependencies)
            if reasons:
                state['jobs'][ident] = {'state': 'blocked', 'reasons': reasons}
                write(state_path, state)
                raise ValueError('Workflow gate blocked ' + ident + ': ' + '; '.join(reasons))
            directory = root / 'jobs' / ident
            if not directory.exists():
                initialize(directory, job['config'])
            elif not (directory / 'ready.json').exists():
                raise ValueError('Incomplete job initialization: ' + ident)
            if job.get('inherit_from'):
                source = root / 'jobs' / job['inherit_from']
                source_spec = read(source / 'config.json')
                source_tasks = {t['id'] for t in source_spec['tasks'] if t['id'] not in source_spec.get('excluded_tasks', [])}
                for task in job['config']['tasks']:
                    if task['id'] not in source_tasks or task['id'] in job['config'].get('excluded_tasks', []):
                        continue
                    bundle = root / 'transfers' / ident / task['id']
                    if not (bundle / 'seal.json').exists():
                        export_run(source, task['id'], bundle)
                    await import_run(directory, task['id'], bundle)
            state['jobs'][ident] = {'state': 'running', 'run_dir': str(directory)}
            write(state_path, state)
            try:
                await run(directory)
                result = status(directory)
                if any(v['state'] not in ('completed', 'retired', 'excluded') for v in result['tasks'].values()):
                    raise RuntimeError('Job stopped before completion')
                state['jobs'][ident] = {'state': 'completed', 'run_dir': str(directory), 'medals': result['medals']}
            except BaseException as exc:
                state['jobs'][ident] = {'state': 'blocked', 'run_dir': str(directory), 'error': str(exc)}
                raise
            finally:
                write(state_path, state)
        return state
