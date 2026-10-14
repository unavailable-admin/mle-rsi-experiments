import fcntl
import hashlib
import json
import os
import time
from contextlib import contextmanager
from pathlib import Path


def read(path):
    return json.loads(Path(path).read_text())


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    tmp.replace(path)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


@contextmanager
def lock(path, blocking=True):
    # Put fcntl locks on local storage; state and evidence remain on persistent AFS.
    name = hashlib.sha256(str(Path(path).resolve()).encode()).hexdigest()
    directory = Path('/tmp/mle-rsi-locks')
    directory.mkdir(exist_ok=True)
    with (directory / name).open('a') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        yield


class LimitReached(RuntimeError):
    pass


class Budget:
    """One ledger per experiment: development AND final share each task's ceiling."""
    def __init__(self, root, spec):
        self.path = Path(root) / 'budget.json'
        self.spec = spec

    @contextmanager
    def state(self):
        with lock(self.path):
            state = read(self.path) if self.path.exists() else {'api': {}, 'execution': {}}
            yield state
            write(self.path, state)

    def reserve_api(self, key, task, phase, dollars, request_hash):
        with self.state() as s:
            if key in s['api']:
                return dict(s['api'][key])
            rows = [r for r in s['api'].values() if r['task'] == task]
            total = sum(r.get('charged_usd', r['reserved_usd']) for r in rows)
            phase_total = sum(r.get('charged_usd', r['reserved_usd']) for r in rows if r['phase'] == phase)
            cap = self.spec['api_usd_per_task']
            dev_cap = self.spec['development_api_usd_per_task']
            global_cap = self.spec.get('api_usd_total')
            if global_cap is not None and sum(r.get('charged_usd', r['reserved_usd']) for r in s['api'].values()) + dollars > global_cap:
                raise LimitReached('Experiment API ceiling reached (including all pending reservations)')
            if total + dollars > cap or (phase == 'development' and phase_total + dollars > dev_cap):
                raise LimitReached('Per-task API ceiling reached (including reservations and earlier phases)')
            row = dict(task=task, phase=phase, state='pending', reserved_usd=dollars,
                       request_hash=request_hash, started_at=time.time())
            s['api'][key] = row
            return dict(row)

    def update_api(self, key, **values):
        with self.state() as s:
            s['api'][key].update(values)

    def execution_allowance(self, task, phase, gpu):
        with self.state() as s:
            rows = [r for r in s['execution'].values() if r['task'] == task]
            if any(r['state'] == 'running' for r in rows):
                raise LimitReached('Unfinished execution reservation; reconcile before resume')
            # Also bound CPU candidate wall time; CPU-only tasks do not get unlimited search.
            total = sum(r['wall_seconds'] for r in rows)
            used = sum(r['wall_seconds'] for r in rows if r['phase'] == phase)
            remaining = self.spec['execution_seconds_per_task'] - total
            if phase == 'development':
                remaining = min(remaining, self.spec['development_execution_seconds_per_task'] - used)
            if gpu:
                remaining = min(remaining, self.spec['gpu_seconds_per_task'] - sum(r['gpu_seconds'] for r in rows))
            if remaining < 10:
                raise LimitReached('Per-task execution/GPU budget exhausted')
            return remaining

    def start_execution(self, key, task, phase, timeout, gpu):
        with self.state() as s:
            if key in s['execution']:
                raise LimitReached('Execution ID already used')
            s['execution'][key] = dict(task=task, phase=phase, state='running',
                                      wall_seconds=timeout, gpu_seconds=timeout if gpu else 0,
                                      started_at=time.time())

    def finish_execution(self, key, wall, gpu_seconds, **extra):
        with self.state() as s:
            s['execution'][key].update(state='completed', wall_seconds=wall,
                                      gpu_seconds=gpu_seconds, **extra)
