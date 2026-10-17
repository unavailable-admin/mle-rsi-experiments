"""Read-only search inspection plus authenticated local pause/resume controls."""
import html
import hmac
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import time
import threading
from urllib.parse import urlparse
from .common import read, lock
from collections import deque
_THROUGHPUT = {}
from .__main__ import budget_summary
from dream_rsi.storage import CampaignStore


def report(root, stores=None):
    record = read(root/'frozen-config.json')
    spec = record['spec']
    tasks = spec['experiment_tasks']
    retired = read(root/'retired-tasks.json') if (root/'retired-tasks.json').exists() else {}
    rows = []
    ledger = read(root/'budget.json') if (root/'budget.json').exists() else {'api': {}, 'execution': {}}
    for task in tasks:
        state = root/'task-status'/f'{task}.json'
        row = {'task': task, 'state': 'ready', 'completed_rounds': 0, 'candidates': 0, 'valid': 0,
               'timeouts': 0, 'failures': {}, 'public_curve': [], 'checkpoints': []}
        if state.exists():
            row.update(read(state))
        path = root/'campaigns'/('dev-'+task)
        if (path/'campaign.sqlite').exists():
            if stores is None:
                store = CampaignStore(path, read_only=True)
            else:
                if str(path) not in stores:
                    stores[str(path)] = CampaignStore(path, read_only=True)
                store = stores[str(path)]
            try:
                cp = store.get('checkpoint','active') or {}
                row['completed_rounds'] = cp.get('completed_rounds', 0)
                row['phase'] = cp.get('phase')
                nodes = sorted((n for _,n in store.list('node')), key=lambda n:n.created_order)
                row['candidates'] = len(nodes)
                row['valid'] = sum(n.evaluation.valid for n in nodes)
                row['unique_submissions'] = len({n.evaluation.diagnostics.get('submission_sha256') for n in nodes
                                                 if n.evaluation.diagnostics.get('submission_sha256')})
                best_quality = None
                best_metric = None
                last_improved = 0
                for i,n in enumerate(nodes, 1):
                    if n.evaluation.valid and (best_quality is None or n.score > best_quality + 1e-12):
                        best_quality = n.score
                        best_metric = n.evaluation.metrics.get('validation_score')
                        last_improved = i
                    if best_metric is not None:
                        row['public_curve'].append([i,best_metric])
                    if not n.evaluation.valid:
                        name = n.evaluation.fail_class.value
                        row['failures'][name] = row['failures'].get(name, 0)+1
                row['timeouts'] = row['failures'].get('timeout',0)
                row['public_best'] = best_metric
                row['slots_since_improvement'] = len(nodes)-last_improved if last_improved else None
                row['first_batch_done'] = len(nodes) >= 5
                if spec.get('diversity_trial'):
                    from .diversity import audit
                    class AuditView:
                        def list(self, kind):
                            return list(enumerate(nodes)) if kind == 'node' else store.list(kind)
                    row['diversity'] = audit(AuditView())
            finally:
                if stores is None:
                    store.close()
        row['api_usd'] = sum(v.get('charged_usd',0) for v in ledger['api'].values() if v['task']==task)
        row['execution_hours'] = sum(v['wall_seconds'] for v in ledger['execution'].values() if v['task']==task)/3600
        for out in sorted((root/'rounds').glob('*/'+task)):
            if not (out/'seal.json').exists():
                continue
            choice = read(out/'selected.json')
            cp = {'round': int(out.parent.name), 'public': choice['metrics'].get('validation_score') if choice else None}
            if (out/'official-result.json').exists():
                result = read(out/'official-result.json')
                cp.update(state=result['state'], medal=result['any_medal'], valid_submission=result['valid_submission'])
                details = result.get('official_report',{}).get('competition_reports',[])
                if details:
                    cp['private'] = details[0]['score']
            row['checkpoints'].append(cp)
        row['retirement'] = retired.get(task)
        if task in retired: row['state'] = 'retired_medal'
        rows.append(row)
    rounds = []
    for number in sorted({c['round'] for r in rows for c in r['checkpoints']}):
        cps = [c for r in rows for c in r['checkpoints'] if c['round']==number]
        carried = sum(e.get('activation_round',e['round']) < number for e in retired.values())
        evaluated = sum(c.get('state') in ('graded','no_submission') for c in cps)
        medals = sum(c.get('medal',False) for c in cps)
        rounds.append({'round':number, 'search_completed':len(cps), 'evaluated':evaluated,
                       'medals':medals, 'total':len(tasks), 'medal_rate':medals/len(tasks),
                       'target_total':len(tasks)-carried, 'carried_medals':carried, 'cumulative_medals':medals+carried, 'complete':evaluated+carried==len(tasks), 'verification_only':record['mode']!='real'})
    samples = _THROUGHPUT.setdefault(str(root), deque())
    now = time.time()
    valid_total = sum(r['valid'] for r in rows)
    samples.append((now,valid_total))
    while len(samples)>1 and samples[1][0]<now-3600: samples.popleft()
    elapsed = now-samples[0][0]
    throughput = (valid_total-samples[0][1])*3600/elapsed if elapsed>=60 else None
    with_alive = False
    try:
        with lock(root/'controller', blocking=False):
            pass
    except BlockingIOError:
        with_alive = True
    return {'updated_at':time.time(), 'mode':record['mode'], 'run_dir':str(root),
            'state':read(root/'round-status.json') if (root/'round-status.json').exists() else {'state':'ready'},
            'control':read(root/'control.json') if (root/'control.json').exists() else {'action':'resume'},
            'controller_alive':with_alive, 'rows':rows, 'rounds':rounds,
            'valid_candidates_per_hour':throughput, 'throughput_window_seconds':elapsed,
            'retired_count':len(retired), 'remaining_tasks':[t for t in tasks if t not in retired],
            'historical_medals':sum(any(c.get('medal') for c in r['checkpoints']) for r in rows),
            'budget':budget_summary(ledger), 'api_cap':spec['budget']['api_usd_total'],
            'stage_increment':read(root/'stage2-lineage.json')['increment'] if (root/'stage2-lineage.json').exists() else None,
            'resources':{'cpu_ids':spec['execution']['cpu_ids'], 'gpu_pool':spec['execution']['gpu_pool'],
                         'slots':spec['execution'].get('mixed_resources',{}).get('max_slots',len(spec['execution']['cpu_groups']))},
            'scheduler':read(root/'scheduler.json') if (root/'scheduler.json').exists() else {'active':[],'waiting':[]},
            'note':('探索性冲牌：private 获牌反馈用于退出和预算分配；沿用历史搜索，资源调度已改变，不是独立盲测。' if spec.get('round_robin',{}).get('retire_on_medal') else '探索性逐轮评测；private 不进入自动搜索，人工看过成绩后的调整须记录。')}


PAGE = '''<!doctype html><html lang="zh"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>5090-2 · Lite22 独立实验</title><style>
:root{font-family:system-ui,sans-serif;color:#e1e9f4;background:#0b1220}body{max-width:1500px;margin:auto;padding:28px}h1{margin-bottom:8px}p{color:#a9bad0;line-height:1.6}button,select{background:#263b56;color:white;border:1px solid #526886;border-radius:6px;padding:10px 16px;cursor:pointer;margin-right:8px}button:disabled{opacity:.5;cursor:wait}a{color:#7ed6d5}article{background:#142136;border:1px solid #263b56;border-radius:12px;padding:20px;margin:18px 0}.cards{display:grid;grid-template-columns:repeat(4,1fr);gap:14px}.cards article{margin:0}.big{font-size:28px;font-weight:700}table{width:100%;border-collapse:collapse;font-size:14px}td,th{padding:11px 8px;text-align:left;border-bottom:1px solid #2b3b50}th{color:#96abc5}td:first-child{max-width:280px;overflow-wrap:anywhere}.scroll{overflow:auto}.bad{color:#ffb0a2}.good{color:#73dbc5}svg{width:100%;height:210px}code{overflow-wrap:anywhere}.small{font-size:13px;color:#a9bad0}#message{color:#ffd08a}details{max-width:430px}summary{cursor:pointer}@media(max-width:800px){body{padding:12px}.cards{grid-template-columns:repeat(2,1fr)}}
</style><h1>5090-2 · Lite22 独立实验</h1><p>目标：Lite22 SOTA → 超过 HMA → MLE-bench SOTA。每题每轮 25 候选，跨题共享配置的资源池。</p>
<button id="pause" onclick="control('pause')">暂停推进</button><button id="resume" onclick="control('resume')">继续推进</button><a id="tree" target="_blank" rel="noopener">搜索树 / 候选代码 / 策略</a><p id="message" role="status"></p>
<p class="small">暂停不再接收新批次，已接收批次及进行中的策略更新、评分完成后显示已暂停。继续仅对仍在线的控制器生效。每 15 秒刷新。</p>
<div class="cards"><article><div>控制器</div><div id="state" class="big">读取中</div></article><article><div>累计保留得牌 / 全部题目</div><div id="medals" class="big">—</div><div id="coverage" class="small"></div></article><article><div>候选 / 有效候选</div><div id="candidates" class="big">—</div></article><article><div>API 已结算 / 上限</div><div id="budget" class="big">—</div><div id="reserved" class="small"></div></article></div>
<article><h2>获牌退出与剩余目标</h2><p id="retirement"></p></article><article><h2>资源与排队</h2><p id="resources"></p><div id="lanes"></div></article>
<article><h2>逐轮覆盖与得牌率</h2><p class="small">获牌率分母始终包含全部任务。搜索和新评测分母仅包含本轮仍在攻的任务，冻结获牌题单独沿用。后续轮次计入此前冻结获牌提交；保留成绩不代表本轮重新评测。mock 不显示真实得牌率。</p><div class="scroll"><table><thead><tr><th>轮次</th><th>完成搜索</th><th>已评测</th><th>得牌</th><th>得牌率</th><th>覆盖状态</th></tr></thead><tbody id="rounds"></tbody></table></div></article>
<article><h2>每题健康与进度</h2><div class="scroll"><table><thead><tr><th>题目</th><th>状态 / 轮次</th><th>候选 / 有效</th><th>Public 最优</th><th>Private 最新轮</th><th>超时 / 无改善候选</th><th>API / 执行小时</th><th>诊断</th></tr></thead><tbody id="tasks"></tbody></table></div></article>
<article><h2>学习曲线</h2><select id="pick" onchange="draw()"></select><div id="plot"></div><p class="small">蓝绿：public 历史最优；橙：每轮固定 checkpoint 的 private 成绩。横轴为候选数，各题指标不同，public/private 不应等同。solver 始终按 public 选择。</p></article>
<p><a id="utilization" target="_blank" rel="noopener">CPU / GPU 利用率与队列监控</a></p><p id="note"></p><p id="stamp" class="small"></p>
<script>
const TOKEN=__TOKEN__; let data=null, refreshing=false; const $=id=>document.getElementById(id); const esc=x=>String(x??'—').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const num=x=>x==null?'—':Number(x).toFixed(5); const labels={retired_medal:'已获牌冻结',all_medaled:'全部获牌完成',preparing:'准备任务',ready:'待启动',running:'运行中',paused:'已暂停',stopped:'已停止',blocked:'需检查',budget_exhausted:'额度耗尽',budget_stop:'预算停止',target_reached:'目标轮完成',waiting_for_resources:'资源未就绪',searching:'搜索中',grading:'评分中',round_complete:'本轮完成'};
$('utilization').href=location.protocol+'//'+location.hostname+':'+(Number(location.port||18770)+2)+'/';
$('tree').href=location.protocol+'//'+location.hostname+':'+(Number(location.port||18770)+1)+'/';
async function control(action){if(!confirm(action==='pause'?'请求暂停？已接收批次完成后暂停，不强杀训练或 API 请求。':'继续按原冻结配置和剩余预算推进？'))return;try{const r=await fetch('/control',{method:'POST',headers:{'Content-Type':'application/json','X-Control-Token':TOKEN},body:JSON.stringify({action})});const result=await r.json();if(!r.ok)throw Error(result.error||r.status);$('message').textContent=action==='pause'?'已请求暂停，等待已接收批次收尾。':'已请求继续。';await refresh();}catch(e){$('message').textContent='控制失败：'+e.message;}}
async function refresh(){if(refreshing)return;refreshing=true;try{const r=await fetch('/report.json',{cache:'no-store'});if(!r.ok)throw Error(r.status);data=await r.json();render();}catch(e){$('message').textContent='读取失败，将自动重试：'+e.message;}finally{refreshing=false;}}
function render(){const d=data, rs=d.rounds, last=rs[rs.length-1], total=d.rows.length;let state=labels[d.state.state]||d.state.state;if(d.control.action==='pause'&&d.state.state!=='paused'&&d.controller_alive)state='暂停收尾中';if(!d.controller_alive)state+=' · 离线';$('state').textContent=state;$('pause').disabled=$('resume').disabled=!d.controller_alive;
$('medals').textContent=d.mode!=='real'?'MOCK':last?Math.max(last.cumulative_medals??last.medals,d.retired_count??0)+' / '+total:'0 / '+total;$('coverage').textContent=last?'第 '+last.round+' 轮 · 新评测 '+last.evaluated+'/'+(last.target_total??total)+' · 沿用获牌 '+(last.carried_medals??0):'尚无轮次评测';$('candidates').textContent=d.rows.reduce((s,r)=>s+r.candidates,0)+' / '+d.rows.reduce((s,r)=>s+r.valid,0);$('budget').textContent='$'+d.budget.api_usd.toFixed(2)+' / $'+d.api_cap;$('reserved').textContent='含预留 $'+d.budget.api_accounted_usd.toFixed(2)+'；未结算 '+d.budget.unsettled_requests+(d.stage_increment?'；本阶段新增上限 $'+d.stage_increment.api_usd_total:'');
$('retirement').textContent='历史获牌 '+d.historical_medals+' / '+total+' · 已冻结 '+d.retired_count+' · 剩余 '+d.remaining_tasks.length+' 题 · 最近观测有效候选/小时 '+(d.valid_candidates_per_hour==null?'采样中':d.valid_candidates_per_hour.toFixed(1));
$('resources').textContent=d.resources.cpu_ids.length+' 个 CPU · GPU '+d.resources.gpu_pool.join(', ')+' · '+d.resources.slots+' 个执行槽 · 活跃 '+d.scheduler.active.length+' · 排队 '+d.scheduler.waiting.length;$('lanes').innerHTML=d.scheduler.active.map(s=>'<p>'+esc(s.task)+' · '+esc(s.kind)+' · CPU '+esc(s.cpus.join(','))+' · GPU '+esc(s.gpu)+' · '+Math.round((Date.now()/1000-s.started_at)/60)+' 分钟</p>').join('')||'<p>没有活跃执行槽</p>';
$('rounds').innerHTML=rs.map(r=>'<tr><td>'+r.round+'</td><td>'+r.search_completed+'/'+(r.target_total??total)+'</td><td>'+r.evaluated+'/'+(r.target_total??total)+'</td><td>'+(r.cumulative_medals??r.medals)+'/'+total+'</td><td>'+(r.verification_only?'MOCK':(100*(r.cumulative_medals??r.medals)/total).toFixed(1)+'%')+'</td><td>'+(r.complete?'完整':'部分覆盖')+'</td></tr>').join('');
$('tasks').innerHTML=d.rows.map(r=>{const c=r.checkpoints[r.checkpoints.length-1];return '<tr><td>'+esc(r.task)+'</td><td class="'+(r.state==='blocked'?'bad':'')+'">'+esc(labels[r.state]||r.state)+' / '+r.completed_rounds+'</td><td>'+r.candidates+' / '+r.valid+'</td><td>'+num(r.public_best)+'</td><td>'+(c?'R'+c.round+' '+num(c.private)+(c.medal?' 🏅':''):'—')+'</td><td>'+r.timeouts+' / '+esc(r.slots_since_improvement)+'</td><td>$'+r.api_usd.toFixed(2)+' / '+r.execution_hours.toFixed(2)+'</td><td><details><summary>'+ (r.first_batch_done?'首批已完成':'首批待完成')+'</summary><p>'+esc(r.reason||'')+'</p><p>'+esc(JSON.stringify(r.failures))+'</p><p>不同提交 '+esc(r.unique_submissions)+' · 去重拒绝 '+esc(r.diversity?.duplicate_responses)+'</p></details></td></tr>';}).join('');
const selected=$('pick').value;$('pick').innerHTML=d.rows.map(r=>'<option value="'+esc(r.task)+'">'+esc(r.task)+'</option>').join('');if(selected)$('pick').value=selected;draw();$('note').textContent=d.note;$('stamp').textContent='更新 '+new Date(d.updated_at*1000).toLocaleString()+' · '+d.run_dir;}
function draw(){if(!data)return;const r=data.rows.find(r=>r.task===$('pick').value);if(!r)return;const pub=r.public_curve,priv=r.checkpoints.filter(c=>c.private!=null).map(c=>[c.round*25,c.private]),all=[...pub,...priv];if(!all.length){$('plot').textContent='暂无有效分数';return;}const low=Math.min(...all.map(p=>p[1])),high=Math.max(...all.map(p=>p[1])),span=high-low||Math.abs(low)*.1||1,min=low-span*.1,max=high+span*.1,n=Math.max(25,r.candidates),x=i=>65+820*i/n,y=v=>165-140*(v-min)/(max-min);let s='<svg viewBox="0 0 940 210" role="img" aria-label="'+esc(r.task)+' 学习曲线"><path d="M65 20 V165 H885" fill="none" stroke="#627891"/>';
for(const [points,color] of [[pub,'#73dbc5'],[priv,'#ffba76']]){s+='<polyline fill="none" stroke="'+color+'" stroke-width="2" points="'+points.map(p=>x(p[0])+','+y(p[1])).join(' ')+'"/>';if(points===priv)s+=points.map(p=>'<circle cx="'+x(p[0])+'" cy="'+y(p[1])+'" r="4" fill="'+color+'"/>').join('');}for(const v of [low,high])s+='<text x="0" y="'+y(v)+'" fill="#b8c9dd" font-size="12">'+v.toFixed(4)+'</text>';s+='<text x="65" y="195" fill="#b8c9dd">0</text><text x="840" y="195" fill="#b8c9dd">'+n+' 候选</text></svg>';$('plot').innerHTML=s;}
refresh();setInterval(refresh,15000);
</script></html>'''


def serve(root, port):
    token = read(root/'web-token.json')['token']
    stores = {}
    report_lock = threading.Lock()
    cached = {'at': 0, 'value': None}
    def live_report():
        # Reuse immutable artifact path indexes and coalesce concurrent refreshes
        # on shared storage. Controls use a separate handler and stay responsive.
        with report_lock:
            if time.monotonic() - cached['at'] > 5:
                cached['value'] = report(root, stores)
                cached['at'] = time.monotonic()
            return cached['value']
    class Handler(BaseHTTPRequestHandler):
        def send(self, value, code=200, kind='application/json'):
            content = (json.dumps(value,ensure_ascii=False) if kind=='application/json' else value).encode()
            self.send_response(code)
            self.send_header('Content-Type',kind+'; charset=utf-8')
            self.send_header('Cache-Control','no-store')
            self.send_header('X-Content-Type-Options','nosniff')
            self.send_header('Content-Length',str(len(content)))
            self.end_headers()
            self.wfile.write(content)
        def do_GET(self):
            if self.path=='/':
                self.send(PAGE.replace('__TOKEN__',json.dumps(token)),kind='text/html')
            elif self.path=='/report.json':
                try:
                    self.send(live_report())
                except Exception as exc:
                    self.send({'error':str(exc)},503)
            else:
                self.send_error(404)
        def do_POST(self):
            from .rounds import control
            origin = self.headers.get('Origin')
            host = self.headers.get('Host','')
            valid_host = urlparse('http://'+host).hostname in ('127.0.0.1','localhost','::1')
            if (self.path!='/control' or not valid_host or
                (origin and urlparse(origin).netloc!=host) or
                not hmac.compare_digest(self.headers.get('X-Control-Token',''),token)):
                self.send({'error':'Forbidden'},403)
                return
            try:
                length = int(self.headers.get('Content-Length','0'))
                if not 0 < length < 1024:
                    raise ValueError('Invalid body length')
                action = json.loads(self.rfile.read(length))['action']
                if action not in ('pause','resume'):
                    raise ValueError('Only pause/resume are available on the dashboard')
                self.send(control(root,action))
            except (ValueError,KeyError) as exc:
                self.send({'error':str(exc)},400)
    print(f'Lite22 dashboard: http://127.0.0.1:{port}',flush=True)
    ThreadingHTTPServer(('127.0.0.1',port),Handler).serve_forever()
