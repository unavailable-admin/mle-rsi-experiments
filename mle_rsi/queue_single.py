"""Run one frozen experiment only after its predecessor finishes successfully."""
import argparse
import subprocess
import sys
import time
from pathlib import Path
from . import REPO
from .common import lock, read, write, sha
from .experiment import frozen


def predecessor_state(root):
    path = root/'single-status.json'
    if not path.exists():
        return 'waiting'
    status = read(path).get('state')
    if status == 'stopped':
        return 'blocked_predecessor_failed'
    if status != 'completed':
        return 'waiting'
    seal = root/'search-complete.json'
    if not seal.exists():
        return 'blocked_missing_completion_seal'
    record = read(root/'frozen-config.json')['spec']
    expected = record['search']['development_rounds'] * record['search']['batches_per_round'] * record['search']['workers']
    data = read(seal)
    if data['candidate_nodes'] != expected:
        return 'blocked_incomplete_search'
    for name, digest in data['artifacts'].items():
        if sha(root/name) != digest:
            return 'blocked_changed_checkpoint'
    try:
        with lock(root/'controller', blocking=False):
            return 'ready'
    except BlockingIOError:
        return 'waiting_for_controller_exit'


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--after', type=Path, required=True)
    p.add_argument('--run-dir', type=Path, required=True)
    a = p.parse_args()
    root, predecessor = a.run_dir.resolve(), a.after.resolve()
    def status(state, **kwargs):
        write(root/'queue-status.json', {'state': state, 'updated_at': time.time(),
              'predecessor': str(predecessor), 'run_dir': str(root), **kwargs})
    with lock(root/'queue', blocking=False):
        try:
            frozen(root)
            # Refuse a second launch instead of resuming or retrying a paid run.
            if (root/'queue-launch.json').exists():
                raise RuntimeError('Queue has already launched this experiment')
            while True:
                state = predecessor_state(predecessor)
                status(state)
                if state.startswith('blocked_'):
                    return
                if state == 'ready':
                    break
                time.sleep(30)
            frozen(root)
            write(root/'queue-launch.json', {'authorized': True, 'at': time.time(),
                  'predecessor': str(predecessor), 'source_worktree': str(REPO)})
            with (root/'controller.log').open('ab') as log:
                child = subprocess.Popen([sys.executable, '-m', 'mle_rsi.single_task',
                    'run', '--run-dir', str(root)], cwd=REPO,
                    stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                    start_new_session=True)
                write(root/'controller.pid', child.pid)
                status('running', controller_pid=child.pid)
                code = child.wait()
            status('completed' if code == 0 else 'needs_inspection', exit_code=code)
        except BaseException as error:
            status('needs_inspection', error=f'{type(error).__name__}: {error}')
            raise

if __name__ == '__main__':
    main()
