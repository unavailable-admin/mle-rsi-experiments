"""Summarize observed utilization; distinguish host activity from experiment slots."""
import argparse
import json
from pathlib import Path
import statistics
import time


def quantiles(values):
    values=sorted(values)
    if not values:return None
    return {'count':len(values),'mean':statistics.mean(values),'p50':values[len(values)//2],
            'p90':values[min(len(values)-1,int(.9*len(values)))],'max':max(values)}


def summarize(root, samples, minutes):
    timing_path=root/'round-timing.json'
    if not timing_path.exists():return {'state':'awaiting_experiment_start','run_dir':str(root)}
    timing=json.loads(timing_path.read_text())
    cutoff=max(time.time()-minutes*60,timing['started_at'])
    rows=[]
    with samples.open() as f:
        for line in f:
            try:row=json.loads(line)
            except ValueError:continue
            if row['at']>=cutoff:rows.append(row)
    if len(rows)<2:return {'state':'insufficient_samples','samples':len(rows)}
    rows.sort(key=lambda r:r['at'])
    weighted=[(r,min(15,max(0,n['at']-r['at']))) for r,n in zip(rows,rows[1:])]
    duration=sum(w for r,w in weighted)
    if not duration:return {'state':'insufficient_time_coverage'}
    def avg(fn):return sum(fn(r)*w for r,w in weighted)/duration
    cfg=json.loads((root/'frozen-config.json').read_text())['spec']
    slots=len(cfg['execution']['cpu_groups'])
    grants={(a['task'],a['started_at']):a.get('queue_wait_seconds',0) for r in rows for a in r['active']}
    gpuid=sorted({g['index'] for r in rows for g in r['gpu']})
    gpu={str(i):quantiles([g['util'] for r in rows for g in r['gpu'] if g['index']==i and g['util'] is not None]) for i in gpuid}
    elapsed=rows[-1]['at']-rows[0]['at']
    ledger_path=root/'budget.json'
    ledger=json.loads(ledger_path.read_text()) if ledger_path.exists() else {'api':{},'execution':{}}
    api=[v['response_seconds'] for v in ledger['api'].values() if v.get('started_at',0)>=cutoff and 'response_seconds' in v]
    affinity=cfg['execution'].get('gpu_cpu_affinity',{})
    gpu_slot_time=sum(sum(a.get('gpu') is not None for a in r['active'])*w for r,w in weighted)
    remote_slot_time=sum(sum(a.get('gpu') is not None and str(a['gpu']) in affinity and not set(a['cpus'])<=set(affinity[str(a['gpu'])]) for a in r['active'])*w for r,w in weighted)
    findings=[]
    occupancy=avg(lambda r:len(r['active'])/slots)
    no_execution_with_api=avg(lambda r:float(not r['active'] and r['api_unsettled']>0))
    cpu=avg(lambda r:r['cpu_allocated_percent'])
    if no_execution_with_api>.2:findings.append('More than 20% of observed time has no active execution but unsettled API requests. Inspect request latency/billing and batch admission before increasing hardware.')
    if occupancy>.8 and cpu<40:findings.append('Execution slots are mostly occupied while allocated CPU utilization is below 40%; inspect per-candidate CPU threads, GPU waits and I/O before changing slot sizes.')
    if avg(lambda r:float(bool(r['waiting'])))>.2:findings.append('Execution queue is nonempty for more than 20% of the observed time; compare GPU and CPU utilization with queued task types.')
    return {'state':'observed','run_dir':str(root),'window_seconds':elapsed,'sampled_seconds':duration,
            'samples':len(rows),'cpu_allocated_mean_percent':cpu,
            'cpu_host_mean_percent':avg(lambda r:r['cpu_host_percent']),
            'execution_slot_occupancy_percent':100*occupancy,
            'gpu_host_percent_by_device':gpu,
            'cross_numa_gpu_slot_time_percent':100*remote_slot_time/gpu_slot_time if gpu_slot_time and affinity else None,
            'queue_nonempty_time_percent':100*avg(lambda r:float(bool(r['waiting']))),
            'no_execution_with_unsettled_api_time_percent':100*no_execution_with_api,
            'grant_queue_wait_seconds':quantiles(list(grants.values())),
            'api_response_seconds':quantiles(api),
            'completed_executions_per_hour':(rows[-1]['execution_completed']-rows[0]['execution_completed'])*3600/elapsed,
            'findings':findings,
            'limits':['Host CPU/GPU includes other workloads. Queue and slots refer to this experiment.',
                      'API unsettled includes billing/uncertain requests; it is not proof of model-generation starvation.',
                      'Five-second sampling can miss short bursts; hardware saturation does not prove best medal throughput.']}


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--run-dir',type=Path,required=True);p.add_argument('--samples',type=Path,required=True);p.add_argument('--minutes',type=float,default=60);a=p.parse_args()
    print(json.dumps(summarize(a.run_dir,a.samples,a.minutes),ensure_ascii=False,indent=2))
