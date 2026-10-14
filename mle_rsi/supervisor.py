"""Durable development -> freeze -> final controller with phase deadlines."""
import argparse
import asyncio
from contextlib import ExitStack
import json
import os
from pathlib import Path
import time
import traceback

from .common import lock, read, write
from .experiment import develop, final_evaluate, freeze_policy, frozen, summarize
from .__main__ import budget_summary


def phase_deadline(root, phase, seconds, overall_deadline):
    path=root/'phase-timing.json'
    timing=read(path) if path.exists() else {}
    if phase not in timing:
        timing[phase]={'started_at':time.time(),'deadline':min(time.time()+seconds,overall_deadline)}
        write(path,timing)
    return timing[phase]['deadline']


def report(root, state):
    summary=summarize(root)
    dev=[]
    for p in sorted((root/'outcomes/development').glob('*/result.json')):
        row=read(p)
        dev.append({k:row.get(k) for k in ['task','state','completed_rounds','candidate_nodes','reason','selected']})
    result={**state,'development':dev,'groups':summary['groups'],'final':summary['rows'],
            'budget':budget_summary(summary.get('budget',{})),'report_updated_at':time.time()}
    write(root/'completion-report.json',result)
    lines=['GPT MLE half-day experiment', 'State: '+state['state'],
           'API charged USD: '+str(result['budget']['api_usd']),
           'API unsettled reservations USD: '+str(result['budget']['api_reserved_usd']),
           'Development:']
    for row in dev:
        lines.append(f"{row['task']}: {row['state']}; rounds={row['completed_rounds']}; nodes={row['candidate_nodes']}; {row.get('reason') or ''}")
    lines += ['Final groups: '+json.dumps(summary['groups']), 'Final outcomes:']
    lines += [f"{r['task']}: {r['state']}; valid={r['valid_submission']}; medal={r['any_medal']}" for r in summary['rows']]
    validation='Public validation uses host-owned splits and independently recomputed metrics.' if frozen(root)['spec'].get('first_batch_fixes') else 'Public validation is candidate-reported.'
    lines += [validation+' Private scores are report-only.',
              'No-submission and stopped tasks are not successful completed evaluations.']
    if state.get('error'):lines.append(state['error'])
    (root/'completion-report.txt').write_text('\n'.join(lines)+'\n')
    return result


async def run(root):
    record=frozen(root);spec=record['spec'];path=root/'supervisor.json'
    old=read(path) if path.exists() else {}
    if old.get('state') in ('completed','completed_with_stops','development_incomplete'):
        return report(root,old)
    started=old.get('started_at',time.time())
    state={'pid':os.getpid(),'started_at':started,'state':'running','phase':'development',
           'overall_deadline':started+spec['runtime']['overall_seconds']}
    write(path,state)
    try:
        phase_deadline(root,'development',spec['runtime']['development_seconds'],state['overall_deadline'])
        rows=await develop(root)
        if any(r['state']!='completed' or r['completed_rounds']<spec['search']['development_rounds'] for r in rows):
            state.update(state='development_incomplete',error='Full development did not complete; policy not frozen and final evaluation not started.')
            return
        state['phase']='freeze';write(path,state)
        remaining=min(1800,max(0,state['overall_deadline']-time.time()))
        async with asyncio.timeout(remaining):
            await freeze_policy(root)
        state['phase']='final';write(path,state)
        phase_deadline(root,'final',spec['runtime']['final_seconds'],state['overall_deadline'])
        await final_evaluate(root)
        results=[read(p) for p in (root/'outcomes/final').glob('*/result.json')]
        state['state']='completed' if len(results)==len(record['final_tasks']) and all(r['state']=='completed' for r in results) else 'completed_with_stops'
    except BaseException as exc:
        state.update(state='failed',error=f'{type(exc).__name__}: {exc}')
        (root/'supervisor-error.log').write_text(traceback.format_exc())
        raise
    finally:
        state['finished_at']=time.time();write(path,state)
        report(root,state)
        print('EXPERIMENT FINISHED',json.dumps(state),flush=True)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--run-dir',type=Path,required=True)
    args=parser.parse_args();root=args.run_dir.resolve();spec=frozen(root)['spec']
    os.sched_setaffinity(0,spec['execution']['cpu_ids'])
    with ExitStack() as stack:
        stack.enter_context(lock(root/'controller',blocking=False))
        # Reserve physical cores, including the sibling hardware threads.
        siblings=set()
        for cpu in spec['execution']['cpu_ids']:
            text=Path(f'/sys/devices/system/cpu/cpu{cpu}/topology/thread_siblings_list').read_text().strip()
            for group in text.split(','):
                ends=group.split('-');siblings.update(range(int(ends[0]),int(ends[-1])+1))
        for cpu in sorted(siblings):
            stack.enter_context(lock(Path('/tmp/mle-rsi-resource-leases')/f'cpu-{cpu}',blocking=False))
        asyncio.run(run(root))


if __name__=='__main__':main()
