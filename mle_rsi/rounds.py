"""Exploratory Lite22 waves: persistent public search, private reports between rounds."""
import argparse
import asyncio
from contextlib import ExitStack
import copy
import os
from pathlib import Path
import secrets
import shutil
import signal
import time
import traceback

from . import REPO
from .backend import task_ids
from .common import lock, read, write, sha, load_spec
from .experiment import initialize, frozen, campaign, grade_selected
from .round_resources import ResourcePool


class RoundPaused(Exception):
    """A durable batch boundary, not an interrupted attempt."""


def validate(spec):
    tasks = spec['experiment_tasks']
    if not tasks or len(tasks) != len(set(tasks)) or not set(tasks) <= set(task_ids()):
        raise ValueError('Invalid task list')
    if spec['development_tasks'] != tasks:
        raise ValueError('Each task must have its own cumulative development campaign')
    execution = spec['execution']
    groups = execution['cpu_groups']
    flat = [c for g in groups for c in g]
    if execution.get('mixed_resources'):
        if any(len(g)!=4 for g in groups) or len(set(flat))!=len(flat) or set(flat)!=set(execution['cpu_ids']):
            raise ValueError('Mixed pool requires disjoint four-CPU groups')
        if not 1 <= execution['mixed_resources'].get('max_slots',16) <= 16:
            raise ValueError('Invalid mixed slot limit')
        if any(v not in (4,8) for v in execution['mixed_resources'].get('task_cpu_cores',{}).values()):
            raise ValueError('Mixed CPU allocations must be four or eight')
        if not execution['gpu_pool'] or len(set(execution['gpu_pool']))!=len(execution['gpu_pool']):
            raise ValueError('Invalid GPU pool')
    elif not 1 <= len(groups) <= 8 or any(len(g) != 8 for g in groups) or len(set(flat)) != 8*len(groups) or set(flat) != set(execution['cpu_ids']):
        raise ValueError('Run requires one to eight disjoint eight-CPU slots')
    if not execution.get('mixed_resources') and (len(execution['gpu_pool']) != len(groups) or len(set(execution['gpu_pool'])) != len(groups)):
        raise ValueError('Run requires one distinct GPU per execution slot')
    if execution.get('candidate_cpu_groups'):
        raise ValueError('Use shared slots, not per-task candidate CPU groups')
    if not spec.get('round_robin', {}).get('exploratory_private_feedback'):
        raise ValueError('Roundwise private reporting must be explicitly exploratory')
    if spec['search']['workers'] != 5 or spec['search']['batches_per_round'] != 5:
        raise ValueError('Each round must contain 25 candidate slots')
    if not spec.get('diversity_trial') or not spec.get('mandatory_bounded_repair'):
        raise ValueError('Diversity and bounded repair are required')
    return tasks


def control(root, action):
    if action not in ('pause', 'resume', 'stop'):
        raise ValueError('Unknown control action')
    with lock(root/'round-control'):
        value = {'action': action, 'requested_at': time.time()}
        write(root/'control.json', value)
    return value


def action(root):
    return read(root/'control.json').get('action', 'resume') if (root/'control.json').exists() else 'resume'


def status(root, task, state, **extra):
    write(root/'task-status'/f'{task}.json', {'task': task, 'state': state, 'updated_at': time.time(), **extra})


def export_round(root, task, store, number):
    out = root/'rounds'/f'{number:04d}'/task
    seal = out/'seal.json'
    if seal.exists():
        verify_seal(out)
        return out
    best = store.get('round_best', str(number))
    if best is None:
        raise RuntimeError('Cannot export unfinished round')
    node = best['best_node']
    out.mkdir(parents=True, exist_ok=True)
    choice = None
    if node.parent_id is not None and node.evaluation.valid:
        diagnostics = copy.deepcopy(dict(node.evaluation.diagnostics))
        (out/'solver.py').write_text(node.artifact.source)
        if diagnostics.get('output_dir'):
            source = Path(diagnostics['output_dir'])/'submission.csv'
            lineage_path = root/'offline-max-lineage.json'
            if lineage_path.exists() and not source.exists():
                # An inherited winner may remain best on the new host. Locate its
                # immutable exported submission by hash, never rewrite old nodes.
                expected = diagnostics['submission_sha256']
                matches = [p for p in sorted((root/'rounds').glob('*/'+task+'/submission.csv')) if sha(p)==expected]
                if not matches: raise RuntimeError('Inherited best submission missing: '+task)
                source = matches[-1]
            if sha(source) != diagnostics['submission_sha256']:
                raise RuntimeError('Candidate submission changed')
            shutil.copyfile(source, out/'submission.csv')
            diagnostics['original_output_dir'] = diagnostics['output_dir']
            diagnostics['output_dir'] = str(out.resolve())
        choice = {'node_id': node.node_id, 'score': node.score, 'metrics': node.evaluation.metrics,
                  'diagnostics': diagnostics, 'source_hash': node.artifact.source_hash,
                  'selection_basis': 'host public validation only', 'selected_at': time.time()}
    write(out/'selected.json', choice)
    write(seal, {'round': number, 'candidate_slots': number*25, 'private_used_for_selection': False,
                 'artifacts': {p.name: sha(p) for p in out.iterdir() if p.name in ('solver.py','submission.csv','selected.json')}})
    return out


def verify_seal(out):
    for name, digest in read(out/'seal.json')['artifacts'].items():
        if sha(out/name) != digest:
            raise RuntimeError('Checkpoint changed: '+str(out/name))


def unresolved(root, task):
    if not (root/'budget.json').exists():
        return False
    b = read(root/'budget.json')
    return any(v['task'] == task and v['state'] in ('pending', 'running', 'uncertain')
               for section in ('api','execution') for v in b[section].values())


async def run(root, through_round=None):
    record = frozen(root)
    spec = record['spec']
    tasks = validate(spec)
    if through_round is not None and through_round < 1:
        raise ValueError('through-round must be positive')
    pool = ResourcePool(root, spec['execution']['cpu_groups'], spec['execution']['gpu_pool'], record['mode']=='mock')
    if spec['execution'].get('mixed_resources'):
        from .adaptive_resources import MixedResourcePool
        pool = MixedResourcePool(root, spec['execution'], record['mode']=='mock')
    from .medal_retirement import retire_winners
    retired = retire_winners(root, spec, record['mode'])
    api_gate = asyncio.Semaphore(spec['model']['request_parallelism'])
    batch_gate = asyncio.Semaphore(spec['round_robin'].get('active_batches', 4))
    batch_held = set()
    # Bound policy subprocess load; all tasks still share the execution queue.
    task_gate = asyncio.Semaphore(spec['round_robin'].get('active_tasks', 8))
    campaigns = {}
    blocked = {}
    timing = root/'round-timing.json'
    if not timing.exists():
        write(timing, {'started_at': time.time(), 'deadline': time.time()+spec['runtime']['overall_seconds']})
    deadline = read(timing)['deadline']
    # Shared-filesystem bursts can delay asyncio's child-reap callback beyond the
    # upstream 5s default when many task policy workers close together. Keep a
    # finite, explicit cleanup grace; never retry an uncertain model request.
    from dream_rsi import sandbox
    previous_cleanup_grace = sandbox.PROCESS_CLEANUP_TIMEOUT_S
    sandbox.PROCESS_CLEANUP_TIMEOUT_S = spec['round_robin'].get('process_cleanup_seconds', 30)
    def overall(state, **extra):
        write(root/'round-status.json', {'state': state, 'updated_at': time.time(), **extra})
    async def grade_checkpoint(task, number):
        out = export_round(root, task, campaigns[task].store, number)
        if (out/'official-result.json').exists():
            if read(out/'official-result.json')['state'] == 'grading_failed':
                raise RuntimeError('Previous grading failed; inspect before retry')
            return
        status(root, task, 'grading', round=number)
        async with pool.slot(task, False, 'grading') as slot:
            verify_seal(out)
            result = await asyncio.to_thread(grade_selected, root, task, {'selected': read(out/'selected.json')},
                                    output_dir=out, cpu_ids=list(slot['cpus']))
            if result['state'] == 'grading_failed':
                raise RuntimeError('Private grading failed; inspect retained artifacts')
    async def one(task, number):
        c = campaigns[task]
        async with task_gate:
            # A pause does not launch another task's not-yet-started round.
            if action(root) in ('pause', 'stop') or time.time() >= deadline:
                return
            try:
                if spec.get('offline_max'):
                    boundary = spec['offline_max']['boundary']
                    marker = root/'offline-max'/f'{task}.json'
                    if not marker.exists():
                        from .offline_max import refresh
                        status(root, task, 'offline_refresh', round=boundary)
                        await refresh(c, root, boundary)
                    if action(root) in ('pause','stop') or time.time() >= deadline:
                        return
                cp = c.checkpoints.load(c.config.campaign_id)
                # Recover report-only work first, including private grading after a crash.
                for past in range(1, cp['completed_rounds']+1):
                    await grade_checkpoint(task, past)
                if cp['completed_rounds'] < number:
                    status(root, task, 'searching', round=number)
                    print(f'ROUND {number} START {task}', flush=True)
                    await c.run_round(number)
                    expected = number * 25
                    if len(c.store.list('node')) != expected:
                        raise RuntimeError(f'Incomplete round: expected {expected} candidate slots')
                    await grade_checkpoint(task, number)
                status(root, task, 'round_complete', round=number)
                print(f'ROUND {number} COMPLETE {task}', flush=True)
            except RoundPaused:
                status(root, task, 'paused', round=number)
            except Exception as exc:
                reason = f'{type(exc).__name__}: {exc}'
                blocked[task] = reason
                status(root, task, 'blocked', round=number, reason=reason)
                (root/'task-status'/f'{task}.log').write_text(traceback.format_exc())
                print(f'BLOCKED {task}: {reason}', flush=True)
            finally:
                if task in batch_held:
                    batch_held.remove(task)
                    batch_gate.release()
    try:
        for task in tasks:
            if task in retired:
                status(root, task, 'retired_medal', round=retired[task]['round'])
                continue
            overall('preparing', task=task)
            old_path = root/'task-status'/f'{task}.json'
            old = read(old_path) if old_path.exists() else {}
            if old.get('state') == 'blocked' or unresolved(root, task):
                blocked[task] = old.get('reason', 'Unsettled API/execution: explicit reconciliation required')
                status(root, task, 'blocked', reason=blocked[task])
                continue
            c = campaign(root, task, 'development', resource_pool=pool, api_gate=api_gate)
            async def before_batch(task=task):
                if action(root) in ('pause','stop') or time.time() >= deadline:
                    raise RoundPaused()
                await batch_gate.acquire()
                if action(root) in ('pause','stop') or time.time() >= deadline:
                    batch_gate.release()
                    raise RoundPaused()
                batch_held.add(task)
            async def after_batch(task=task):
                batch_held.remove(task)
                batch_gate.release()
            c.scheduler.before_batch = before_batch
            c.scheduler.after_batch = after_batch
            campaigns[task] = c
            await c.bootstrap()
        if spec.get('offline_max'):
            # Finish the initial refresh wave before any new online work. No
            # candidate/grading/private inputs enter this refresh stage.
            from .offline_max import refresh
            async def initial_refresh(task, c):
                async with task_gate:
                    if action(root) in ('pause','stop') or time.time() >= deadline:
                        return
                    marker=root/'offline-max'/f'{task}.json'
                    if marker.exists(): return
                    try:
                        status(root,task,'offline_refresh',round=spec['offline_max']['boundary'])
                        await refresh(c,root,spec['offline_max']['boundary'])
                    except Exception as exc:
                        blocked[task]=f'{type(exc).__name__}: {exc}'
                        status(root,task,'blocked',reason=blocked[task])
                        (root/'task-status'/f'{task}.log').write_text(traceback.format_exc())
            overall('offline_refresh',round=spec['offline_max']['boundary'])
            await asyncio.gather(*(initial_refresh(t,c) for t,c in campaigns.items()))
        while True:
            if action(root) == 'stop':
                overall('stopped', blocked=blocked)
                break
            if time.time() >= deadline:
                overall('budget_stop', reason='Wall-clock window expired; no new rounds', blocked=blocked)
                break
            if action(root) == 'pause':
                overall('paused', blocked=blocked)
                await asyncio.sleep(2)
                continue
            retired = retire_winners(root, spec, record['mode'])
            for task,entry in retired.items():
                status(root,task,'retired_medal',round=entry['round'])
            if len(retired)==len(tasks):
                overall('all_medaled', retired=len(retired))
                break
            active = [t for t in tasks if t in campaigns and t not in blocked and t not in retired]
            if not active:
                overall('blocked', blocked=blocked)
                break
            completed = {t: campaigns[t].checkpoints.load(campaigns[t].config.campaign_id)['completed_rounds'] for t in active}
            pending_reports = [t for t in active if any(
                not (root/'rounds'/f'{n:04d}'/t/'official-result.json').exists()
                for n in range(1, completed[t]+1))]
            if pending_reports:
                await asyncio.gather(*(one(t, completed[t]) for t in pending_reports))
                continue
            number = min(completed.values()) + 1
            if through_round is not None and number > through_round:
                overall('target_reached', through_round=through_round, blocked=blocked)
                break
            # Catch up lagging tasks before advancing the next wave.
            eligible = [t for t in active if completed[t] < number]
            overall('running', round=number, blocked=blocked)
            await asyncio.gather(*(one(t, number) for t in eligible))
            if action(root) in ('pause', 'stop'):
                continue
            # Fixed-denominator wave summary is derived by the report, never used by search.
            from .round_dashboard import report
            write(root/'round-report.json', report(root))
    finally:
        for c in campaigns.values():
            c.store.close()
        sandbox.PROCESS_CLEANUP_TIMEOUT_S = previous_cleanup_grace


def release_task(root, task):
    record = frozen(root)
    if task not in record['spec']['experiment_tasks']:
        raise ValueError('Unknown task')
    if unresolved(root, task):
        raise RuntimeError('Unsettled request/execution: reconcile before explicit retry')
    # Audit record is retained; source/config drift still cannot resume this run.
    p = root/'task-status'/f'{task}.json'
    with lock(root/'controller', blocking=False):
        old = read(p)
        write(root/'interventions'/f'{time.time_ns()}-{task}.json', {'action': 'retry-task', 'previous': old})
        status(root, task, 'ready')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['init','run','status','web','pause','resume','stop','retry-task'])
    parser.add_argument('--run-dir', type=Path, required=True)
    parser.add_argument('--config', type=Path, default=REPO/'configs/lite22-rounds-half.json')
    parser.add_argument('--mode', choices=['real','mock'], default='real')
    parser.add_argument('--through-round', type=int, help='Optional round checkpoint; default continues until pause/budget')
    parser.add_argument('--port', type=int, default=18770)
    parser.add_argument('--task')
    args = parser.parse_args()
    root = args.run_dir.resolve()
    if args.command == 'init':
        spec = load_spec(args.config)
        validate(spec)
        initialize(root, spec, args.mode)
        write(root/'web-token.json', {'token': secrets.token_urlsafe(32)})
        control(root, 'resume')
        status_path = root/'round-status.json'
        write(status_path, {'state': 'ready', 'updated_at': time.time()})
        print(root)
        return
    if args.command in ('pause','resume','stop'):
        if not (root/'frozen-config.json').exists():
            raise ValueError('Run is not initialized')
        print(control(root, args.command))
        return
    if args.command == 'retry-task':
        release_task(root, args.task)
        return
    from .round_dashboard import report, serve
    if args.command == 'status':
        import json
        print(json.dumps(report(root), ensure_ascii=False, indent=2))
        return
    if args.command == 'web':
        serve(root, args.port)
        return
    with ExitStack() as stack:
        stack.enter_context(lock(root/'controller', blocking=False))
        record = frozen(root)
        validate(record['spec'])
        expected_host = record['spec']['execution'].get('host_name')
        if expected_host and expected_host != __import__('socket').gethostname():
            raise RuntimeError('Run is pinned to host '+expected_host)
        if record['mode'] == 'real':
            cpus = record['spec']['execution']['cpu_ids']
            if not set(cpus) <= os.sched_getaffinity(0):
                raise RuntimeError('Configured CPU affinity unavailable')
            os.sched_setaffinity(0, cpus)
            siblings = set()
            for cpu in cpus:
                text = Path(f'/sys/devices/system/cpu/cpu{cpu}/topology/thread_siblings_list').read_text()
                for group in text.strip().split(','):
                    ends = group.split('-')
                    siblings.update(range(int(ends[0]), int(ends[-1])+1))
            write(root/'round-status.json', {'state': 'waiting_for_resources', 'updated_at': time.time()})
            # All-or-nothing: fail visibly on contention, never retain partial leases while waiting.
            for cpu in sorted(siblings):
                stack.enter_context(lock(Path('/tmp/mle-rsi-resource-leases')/f'cpu-{cpu}', blocking=False))
        write(root/'controller.pid', os.getpid())
        signal.signal(signal.SIGTERM, lambda *_: control(root, 'stop'))
        signal.signal(signal.SIGINT, lambda *_: control(root, 'pause'))
        try:
            asyncio.run(run(root, args.through_round))
        except BaseException as exc:
            write(root/'round-status.json', {'state': 'stopped', 'reason': f'{type(exc).__name__}: {exc}', 'updated_at': time.time()})
            raise


if __name__ == '__main__':
    main()
