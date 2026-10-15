"""Single-task cumulative solver transfer. Search never consumes private grades."""
import argparse
import asyncio
from contextlib import ExitStack
import html
import json
import os
import shutil
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import REPO
from .common import lock, read, sha, write, load_spec
from .experiment import campaign, frozen, grade_selected, initialize
from dream_rsi.storage import CampaignStore
from dream_rsi.tree import DiscoveryTree

TASK = 'spooky-author-identification'


def validate(spec):
    if spec['development_tasks'] != [TASK] or spec.get('experiment_tasks') != [TASK]:
        raise ValueError('Single-task entrypoint requires only Spooky')
    s = spec['search']
    if s['policy_versions_per_round'] < 2 or not spec.get('mandatory_bounded_repair'):
        raise ValueError('Offline updates and host repair guard are required')
    groups = spec['execution'].get('candidate_cpu_groups')
    if groups:
        flat = [cpu for group in groups for cpu in group]
        if len(groups) != 5 or any(len(g) != 8 for g in groups) or len(flat) != len(set(flat)) or set(flat) != set(spec['execution']['cpu_ids']):
            raise ValueError('Five-way run requires five disjoint eight-CPU candidate lanes')
        if spec['model'].get('request_parallelism') != 5:
            raise ValueError('Five-way run requires API parallelism five')
    total = s['development_rounds'] * s['batches_per_round'] * s['workers']
    points = spec['single_task']['checkpoint_candidates']
    per_round = s['batches_per_round'] * s['workers']
    if sorted(set(points)) != points or points[-1] != total or any(n <= 0 or n % per_round for n in points):
        raise ValueError('Checkpoints must be complete rounds, ending at full budget')
    return total


def store_path(root):
    return Path(root)/'campaigns'/('dev-'+TASK)


def export_checkpoints(root, store, spec):
    per_round = spec['search']['batches_per_round'] * spec['search']['workers']
    for count in spec['single_task']['checkpoint_candidates']:
        best = store.get('round_best', str(count // per_round))
        if best is None:
            continue
        node = best['best_node']
        out = Path(root)/'checkpoints'/f'{count:04d}'
        if (out/'selected.json').exists():
            continue
        out.mkdir(parents=True, exist_ok=True)
        choice = None
        if node.parent_id is not None and node.evaluation.valid:
            (out/'solver.py').write_text(node.artifact.source)
            diagnostics = dict(node.evaluation.diagnostics)
            if diagnostics.get('output_dir'):
                source = Path(diagnostics['output_dir'])/'submission.csv'
                if sha(source) != diagnostics['submission_sha256']:
                    raise RuntimeError('Selected submission changed')
                shutil.copyfile(source, out/'submission.csv')
                diagnostics['original_output_dir'] = diagnostics['output_dir']
                diagnostics['output_dir'] = str(out.resolve())
            choice = {'node_id':node.node_id, 'score':node.score,
                      'source_hash':node.artifact.source_hash, 'solver_sha256':sha(out/'solver.py'),
                      'metrics':node.evaluation.metrics, 'diagnostics':diagnostics,
                      'selection_basis':'host-recomputed public validation only',
                      'candidate_budget':count, 'selected_at':time.time()}
        write(out/'selected.json', choice)


def completed_search(root, spec):
    path = Path(root)/'search-complete.json'
    if not path.exists():
        raise RuntimeError('Private grading is disabled until all online/offline rounds complete')
    seal = read(path)
    if seal['candidate_nodes'] != validate(spec):
        raise RuntimeError('Incomplete candidate budget')
    for name, expected in seal['artifacts'].items():
        if sha(Path(root)/name) != expected:
            raise RuntimeError('Frozen checkpoint changed: '+name)
    return seal


async def search(root):
    record = frozen(root); spec = record['spec']; total = validate(spec)
    if (root/'search-complete.json').exists():
        return completed_search(root, spec)
    if list((root/'checkpoints').glob('*/official-result.json')):
        raise RuntimeError('Cannot search after private evaluation')
    if (root/'budget.json').exists():
        ledger=read(root/'budget.json')
        if any(r['state']=='running' for r in ledger.get('execution',{}).values()):
            raise RuntimeError('Unfinished execution reservation; reconcile before resume')
    os.sched_setaffinity(0, spec['execution']['cpu_ids'])
    timing = root/'single-timing.json'
    if not timing.exists():
        write(timing, {'started_at':time.time(), 'deadline':time.time()+spec['runtime']['overall_seconds']})
    c = campaign(root, TASK, 'development')
    try:
        with c.store.writer():
            async with asyncio.timeout(max(0, read(timing)['deadline']-time.time())):
                checkpoint = await c.bootstrap()
                export_checkpoints(root, c.store, spec)
                while checkpoint['completed_rounds'] < spec['search']['development_rounds']:
                    number = checkpoint['completed_rounds']+1
                    write(root/'single-status.json', {'state':'searching','round':number,'updated_at':time.time()})
                    print(f'ROUND {number}/{spec["search"]["development_rounds"]}: online + offline', flush=True)
                    await c.run_round(number)
                    checkpoint = c.checkpoints.load(c.config.campaign_id)
                    export_checkpoints(root, c.store, spec)
                    print(f'ROUND {number} completed', flush=True)
                nodes = c.store.list('node')
                if len(nodes) != total:
                    raise RuntimeError(f'Expected {total} candidates, got {len(nodes)}; refusing final grading')
                artifacts = {str(p.relative_to(root)):sha(p) for p in (root/'checkpoints').glob('*/*') if p.is_file()}
                write(root/'search-complete.json', {'completed_rounds':checkpoint['completed_rounds'],
                      'candidate_nodes':len(nodes),'completed_at':time.time(),'artifacts':artifacts,
                      'private_scores_used':False})
    finally:
        c.store.close()


def grade(root):
    record = frozen(root); completed_search(root, record['spec'])
    for count in record['spec']['single_task']['checkpoint_candidates']:
        out = root/'checkpoints'/f'{count:04d}'
        print('GRADE frozen checkpoint',count,flush=True)
        grade_selected(root,TASK,{'selected':read(out/'selected.json')},output_dir=out)
    write(root/'single-status.json', {'state':'completed','updated_at':time.time()})


def report(root):
    record = read(root/'frozen-config.json'); spec = record['spec']
    data = {'mode':record['mode'],'target_candidates':validate(spec),'target_rounds':spec['search']['development_rounds'],
            'state':read(root/'single-status.json') if (root/'single-status.json').exists() else {'state':'ready'},
            'parallelism':{'api':spec['model'].get('request_parallelism',1),'training':len(spec['execution'].get('candidate_cpu_groups',[])) or 1},
            'api_cap_usd':spec['budget']['api_usd_total'],'completed_rounds':0,'nodes':[],'checkpoints':[],'updated_at':time.time()}
    if (store_path(root)/'campaign.sqlite').exists():
        store = CampaignStore(store_path(root),read_only=True)
        try:
            cp = store.get('checkpoint','active') or {}
            data['completed_rounds'] = cp.get('completed_rounds',0)
            data['phase'] = cp.get('phase')
            # Node records are unique across the cumulative tree; no duplicated historical prefixes.
            nodes = sorted((n for _,n in store.list('node')),key=lambda n:n.created_order)
            best = None
            for n in nodes:
                metric = n.evaluation.metrics.get('validation_score') if n.evaluation.valid else None
                if metric is not None:
                    best = metric if best is None else min(best,metric)
                data['nodes'].append({'index':n.created_order,'node_id':n.node_id,'parent_id':n.parent_id,
                    'valid':n.evaluation.valid,'metric':metric,'best':best,'depth':n.depth,
                    'stage':n.attempt.stage.value,'source_hash':n.artifact.source_hash,
                    'submission_hash':n.evaluation.diagnostics.get('submission_sha256'),
                    'failure':n.evaluation.fail_class.value})
            if spec.get('diversity_trial'):
                from .diversity import audit
                data['diversity'] = audit(store)
            data['policies'] = [dict(round=s['round'],selected_id=s['selected_id'],evaluated_versions=s['evaluated_versions'])
                                for _,s in store.list('round_summary')]
        finally:
            store.close()
    for count in spec['single_task']['checkpoint_candidates']:
        path = root/'checkpoints'/f'{count:04d}'
        if not (path/'selected.json').exists():continue
        choice = read(path/'selected.json')
        row = {'candidates':count,'node_id':choice['node_id'] if choice else None,
               'public':choice['metrics'].get('validation_score') if choice else None}
        if (path/'official-result.json').exists():
            official = read(path/'official-result.json')
            row['grading_state'] = official['state']
            details = official.get('official_report',{}).get('competition_reports',[])
            if details:row.update(private=details[0]['score'],medal=details[0]['any_medal'])
        data['checkpoints'].append(row)
    nodes=data['nodes']
    data['valid_candidates']=sum(n['valid'] for n in nodes)
    data['unique_sources']=len({n['source_hash'] for n in nodes})
    data['unique_submissions']=len({n['submission_hash'] for n in nodes if n['submission_hash']})
    data['successful_debug']=sum(n['valid'] and n['stage']=='debug' for n in nodes)
    if (root/'budget.json').exists():
        from .__main__ import budget_summary
        data['budget']=budget_summary(read(root/'budget.json'))
    return data


def render(data):
    public=[(n['index'],n['best']) for n in data['nodes'] if n['best'] is not None]
    private=[(c['candidates'],c['private']) for c in data['checkpoints'] if c.get('private') is not None]
    vals=[y for _,y in public+private]
    lo=min(vals+[.16506])*.9; hi=max(vals+[.418785])*1.08
    def x(n):return 60+840*n/data['target_candidates']
    def y(v):return 300-240*(v-lo)/(hi-lo)
    svg=['<svg viewBox="0 0 960 350" role="img" aria-label="Public validation and retrospective private log loss">']
    for label,v in [('Gold',.16506),('Silver',.26996),('Bronze',.29381)]:
        svg.append(f'<path d="M60 {y(v)} H900" stroke="#b0b8c5" stroke-dasharray="5 5"/><text x="65" y="{y(v)-5}" font-size="12">Private {label} {v}</text>')
    for points,color in [(public,'#147d92'),(private,'#b45918')]:
        svg.append('<polyline fill="none" stroke="'+color+'" stroke-width="3" points="'+' '.join(f'{x(a)},{y(b)}' for a,b in points)+'"/>')
        for a,b in points if color=='#b45918' else []:
            svg.append(f'<circle cx="{x(a)}" cy="{y(b)}" r="5" fill="{color}"/>')
    svg.append('<path d="M60 40 V300 H900" fill="none" stroke="#555"/>')
    for n in [0,100,200,300,400,500]:
        if n<=data['target_candidates']:svg.append(f'<text x="{x(n)}" y="325" font-size="12">{n}</text>')
    svg.append('<text x="60" y="22">Log loss ↓</text><text x="700" y="346">Candidate slots</text></svg>')
    rows=''.join('<tr>'+''.join('<td>'+html.escape(str(c.get(k,'—')))+'</td>' for k in ['candidates','public','private','medal','node_id'])+'</tr>' for c in data['checkpoints'])
    b=data.get('budget',{})
    stats=f"API {data.get('parallelism',{}).get('api',1)}-way / training {data.get('parallelism',{}).get('training',1)}-way · {len(data['nodes'])}/{data['target_candidates']} candidates · {data['completed_rounds']}/{data['target_rounds']} rounds · {data['valid_candidates']} valid · {data['unique_sources']} distinct code · {data['unique_submissions']} distinct submissions · {data['successful_debug']} successful debug"
    return '''<!doctype html><html lang="zh"><meta charset="utf-8"><meta http-equiv="refresh" content="30"><title>Spooky solver search</title><style>body{font:16px system-ui;background:#f4f6fa;color:#192638;max-width:1100px;margin:40px auto;padding:20px}article{background:white;padding:24px;border-radius:12px;margin:20px 0}svg{width:100%}td,th{text-align:left;padding:12px;border-bottom:1px solid #ddd}table{width:100%}code{word-break:break-all}</style><h1>Spooky · 单题 solver 搜索</h1>'''+f'<p>{html.escape(str(data["state"]))} · mode={data["mode"]} · phase={data.get("phase","—")}</p><article>{stats}<p>API charged ${b.get("api_usd",0):.3f}; accounted incl. reservations ${b.get("api_accounted_usd",0):.3f} / ${data.get("api_cap_usd",20):g} · candidate wall {b.get("candidate_wall_hours",0):.2f}h / 40h</p></article><article>'+''.join(svg)+'''<p style="color:#147d92">蓝绿：固定 public validation 历史最优；橙：搜索全部结束后测的 private checkpoint。</p><p>两种 split 的分数不可直接等同。奖牌线只适用于 private。最终 solver 始终按 public 选，不能按这张 private 曲线挑选。页面每 30 秒刷新。</p></article><article><table><tr><th>候选数</th><th>Public loss</th><th>Private loss</th><th>Medal</th><th>Solver node</th></tr>'''+rows+'</table></article></html>'


def save_report(root):
    data=report(root)
    write(root/'single-report.json',data)
    (root/'single-report.html').write_text(render(data))
    return data


def serve(root, port):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path not in ('/','/report.json'):
                self.send_error(404);return
            try:
                data=report(root)
                content=(json.dumps(data) if self.path=='/report.json' else render(data)).encode()
                self.send_response(200)
                self.send_header('Content-Type','application/json' if self.path=='/report.json' else 'text/html; charset=utf-8')
                self.send_header('Content-Length',str(len(content)));self.end_headers();self.wfile.write(content)
            except Exception as exc:
                self.send_error(503,str(exc))
    print(f'Dashboard: http://127.0.0.1:{port}',flush=True)
    ThreadingHTTPServer(('127.0.0.1',port),Handler).serve_forever()


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('command',choices=['init','run','grade','status','web'])
    p.add_argument('--run-dir',type=Path,required=True)
    p.add_argument('--config',type=Path,default=REPO/'configs/spooky-solver-500.json')
    p.add_argument('--mode',choices=['real','mock'],default='real')
    p.add_argument('--port',type=int,default=18768)
    a=p.parse_args(); root=a.run_dir.resolve()
    if a.command=='init':
        spec=load_spec(a.config);validate(spec);initialize(root,spec,a.mode);save_report(root);return
    if a.command=='web':serve(root,a.port);return
    if a.command=='status':
        data=save_report(root);data.pop('nodes');print(json.dumps(data,indent=2));return
    with ExitStack() as stack:
        stack.enter_context(lock(root/'controller',blocking=False))
        record=frozen(root)
        if record['mode']=='real':
            siblings=set()
            for cpu in record['spec']['execution']['cpu_ids']:
                for group in Path(f'/sys/devices/system/cpu/cpu{cpu}/topology/thread_siblings_list').read_text().strip().split(','):
                    ends=group.split('-');siblings.update(range(int(ends[0]),int(ends[-1])+1))
            write(root/'single-status.json',{'state':'waiting_for_resources','updated_at':time.time()})
            print('Waiting for CPU leases (guarded run owns these until it finishes)...',flush=True)
            for cpu in sorted(siblings):
                stack.enter_context(lock(Path('/tmp/mle-rsi-resource-leases')/f'cpu-{cpu}'))
            print('CPU leases acquired; starting single-task work',flush=True)
        try:
            if a.command=='run':asyncio.run(search(root))
            grade(root)
        except BaseException as exc:
            write(root/'single-status.json',{'state':'stopped','reason':f'{type(exc).__name__}: {exc}','updated_at':time.time()})
            raise
        finally:save_report(root)


if __name__=='__main__':main()
