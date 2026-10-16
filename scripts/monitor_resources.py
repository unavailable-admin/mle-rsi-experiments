"""Read-only host and experiment sampler; no credentials or process command lines."""
import argparse
from collections import Counter, deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import threading
import time


def read(path, default):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default


def cpu_ticks():
    rows = {}
    for line in Path('/proc/stat').read_text().splitlines():
        parts = line.split()
        if parts and parts[0].startswith('cpu'):
            values = list(map(int, parts[1:9]))
            rows[parts[0]] = (sum(values), values[3] + values[4])
    return rows


def cpu_percent(before, after):
    result = {}
    for name, (total, idle) in after.items():
        old_total, old_idle = before.get(name, (total, idle))
        delta = total - old_total
        result[name] = round(100 * (1 - (idle-old_idle)/delta), 2) if delta else 0
    return result


def command(args):
    p = subprocess.run(args, capture_output=True, text=True, timeout=8)
    if p.returncode:
        raise RuntimeError(p.stderr.strip()[:250])
    return p.stdout


def collect(root, cpus, before):
    now = time.time()
    after = cpu_ticks()
    usage = cpu_percent(before, after)
    mem = {p[0].rstrip(':'): int(p[1])*1024 for p in
           (line.split() for line in Path('/proc/meminfo').read_text().splitlines()) if len(p)>2}
    scheduler = read(root/'scheduler.json', {'active': [], 'waiting': []})
    ledger = read(root/'budget.json', {'api': {}, 'execution': {}})
    phases = Counter(read(p, {}).get('state', 'unknown') for p in (root/'task-status').glob('*.json'))
    gpu = []
    errors = []
    try:
        raw = command(['nvidia-smi', '--query-gpu=index,uuid,utilization.gpu,utilization.memory,memory.used,memory.total,power.draw,temperature.gpu', '--format=csv,noheader,nounits'])
        for line in raw.splitlines():
            p = [s.strip() for s in line.split(',')]
            row = {'index': int(p[0]), 'uuid': p[1]}
            for key, value in zip(['util', 'memory_util', 'memory_mib', 'total_mib', 'power_w', 'temperature_c'], p[2:]):
                try: row[key] = float(value)
                except ValueError: row[key] = None
            gpu.append(row)
    except Exception as e:
        errors.append('GPU: '+str(e))
    done = [v for v in ledger['execution'].values() if v['state']=='completed']
    pending_api = [v for v in ledger['api'].values() if v['state'] in ('pending','running','uncertain')]
    waiting = scheduler['waiting']
    sample = {'at': now, 'host': os.uname().nodename, 'cpu_host_percent': usage.get('cpu',0),
              'cpu_allocated_percent': sum(usage.get('cpu'+str(c),0) for c in cpus)/len(cpus),
              'cpu_per_core': [usage.get('cpu'+str(c),0) for c in cpus], 'cpu_ids': cpus,
              'memory_used_gib': (mem['MemTotal']-mem['MemAvailable'])/2**30,
              'memory_total_gib': mem['MemTotal']/2**30, 'load': os.getloadavg(), 'gpu':gpu,
              'active': scheduler['active'], 'waiting': waiting, 'slot_limit':scheduler.get('slots',16), 'cpu_waiting':sum(not w['gpu'] for w in waiting), 'gpu_waiting':sum(w['gpu'] for w in waiting),
              'oldest_queue_seconds': max([now-w.get('queued_at',now) for w in waiting] or [0]),
              'phases': dict(phases), 'api_unsettled': len(pending_api),
              'api_states': dict(Counter(v['state'] for v in ledger['api'].values())),
              'execution_completed': len(done), 'execution_wall_hours': sum(v['wall_seconds'] for v in done)/3600,
              'state':read(root/'round-status.json',{'state':'ready'}), 'errors': errors}
    return sample, after


PAGE = '''<!doctype html><html lang="zh"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>5090-2 · 资源监控</title>
<style>body{font:15px system-ui;background:#0b1220;color:#e1e9f4;max-width:1400px;margin:auto;padding:24px}article{background:#142136;padding:18px;border-radius:10px;margin:16px 0}a{color:#73dbc5}.grid{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}.big{font-size:25px}p{line-height:1.6;color:#b3c1d5}canvas{width:100%;height:220px}table{width:100%;text-align:left;border-collapse:collapse}td,th{padding:8px;border-bottom:1px solid #30425b}.cores{display:grid;grid-template-columns:repeat(16,1fr);gap:3px}.cores span{padding:6px 0;text-align:center;font-size:11px}.bad{color:#ffac8d}@media(max-width:800px){.grid{grid-template-columns:repeat(2,1fr)}.cores{grid-template-columns:repeat(8,1fr)}} </style>
<h1>5090-2 · CPU / GPU 与排队</h1><p>每 5 秒采样；利用率属于整台主机，可能包含其他人的任务。执行槽与队列只属于本实验。GPU 显存占用不等同于计算利用率。</p><a id="overview">实验总览 / 暂停</a><span id="stamp"></span>
<div class="grid"><article>配置 CPU 平均忙碌<div class="big" id="cpu">—</div><small id="hostcpu"></small></article><article>GPU 平均计算利用率<div class="big" id="gpu">—</div></article><article>执行槽 / 排队<div class="big" id="slots">—</div><small id="queue"></small></article><article>已完成执行 / API 未结算<div class="big" id="done">—</div><small id="phase"></small></article></div>
<article><h2>最近一小时利用率</h2><p>蓝绿：配置 CPU；橙：GPU 平均。纵轴 0–100%。</p><canvas id="chart" width="1280" height="240"></canvas></article>
<article><h2>每张 GPU</h2><table><thead><tr><th>GPU</th><th>计算利用率</th><th>显存 MiB</th><th>功率 W</th><th>温度 °C</th></tr></thead><tbody id="gpus"></tbody></table></article>
<article><h2>配置 CPU 核心忙碌程度</h2><div class="cores" id="cores"></div><p id="memory"></p></article>
<article><h2>活跃执行</h2><div id="active"></div><h2>任务阶段</h2><p id="states"></p><p id="hint"></p></article><p class="bad" id="errors"></p>
<script>const $=x=>document.getElementById(x),esc=x=>String(x??'—').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));$('overview').href=location.protocol+'//'+location.hostname+':'+(Number(location.port||18772)-2)+'/';
function avg(g){let a=g.map(x=>x.util).filter(x=>x!=null);return a.length?a.reduce((s,x)=>s+x,0)/a.length:0;}
function draw(h){let c=$('chart'),x=c.getContext('2d');x.clearRect(0,0,c.width,c.height);x.strokeStyle='#40516a';x.strokeRect(35,15,1220,200);x.fillStyle='#b3c1d5';x.fillText('100%',0,20);x.fillText('0%',5,215);let start=h[0].at,end=h[h.length-1].at;for(let [key,color] of [['cpu_allocated_percent','#73dbc5'],['gpu_mean','#ffba76']]){x.beginPath();x.strokeStyle=color;h.forEach((r,i)=>{let px=35+1220*(r.at-start)/Math.max(1,end-start),py=215-2*r[key];i?x.lineTo(px,py):x.moveTo(px,py)});x.stroke()}}
async function refresh(){try{let d=await(await fetch('/metrics.json',{cache:'no-store'})).json(),s=d.latest;if(!s)return;$('stamp').textContent=' · '+new Date(s.at*1000).toLocaleString();$('cpu').textContent=s.cpu_allocated_percent.toFixed(1)+'%';$('hostcpu').textContent='全机逻辑 CPU 平均 '+s.cpu_host_percent.toFixed(1)+'%';$('gpu').textContent=avg(s.gpu).toFixed(1)+'%';$('slots').textContent=s.active.length+'/'+s.slot_limit+' · 排队 '+s.waiting.length;$('queue').textContent='CPU队列 '+s.cpu_waiting+' / GPU队列 '+s.gpu_waiting+' · 最长排队 '+Math.round(s.oldest_queue_seconds)+' 秒';$('done').textContent=s.execution_completed+' / '+s.api_unsettled;$('phase').textContent=s.state.state;$('gpus').innerHTML=s.gpu.map(g=>'<tr><td>'+g.index+'</td><td>'+g.util+'%</td><td>'+g.memory_mib+' / '+g.total_mib+'</td><td>'+g.power_w+'</td><td>'+g.temperature_c+'</td></tr>').join('');$('cores').innerHTML=s.cpu_per_core.map((v,i)=>'<span style="background:rgba(70,200,165,'+(.08+.8*v/100)+')" title="'+v+'%">'+s.cpu_ids[i]+'<br>'+Math.round(v)+'%</span>').join('');$('memory').textContent='主机已用内存 '+s.memory_used_gib.toFixed(1)+' / '+s.memory_total_gib.toFixed(1)+' GiB';$('active').innerHTML=s.active.map(a=>'<p>'+esc(a.task)+' · '+esc(a.kind)+' · GPU '+esc(a.gpu)+' · CPU '+esc(a.cpus.join(','))+' · 入槽前等待 '+Number(a.queue_wait_seconds||0).toFixed(1)+' 秒</p>').join('')||'<p>当前没有执行任务</p>';$('states').textContent=JSON.stringify(s.phases);$('hint').textContent=s.active.length===0&&s.api_unsettled>0?'当前没有训练，存在未结算 API 请求；需结合请求状态判断是否等待生成。':s.waiting.length>0?'存在执行排队；结合槽占用与 GPU 忙碌程度分析资源瓶颈。':'当前无执行队列积压。';$('errors').textContent=s.errors.join('; ');if(Date.now()/1000-s.at>20)$('errors').textContent+=' 采样已过期';if(d.history.length)draw(d.history);}catch(e){$('errors').textContent='监控读取失败：'+e.message;}}
refresh();setInterval(refresh,5000);</script></html>'''


def main():
    p=argparse.ArgumentParser();p.add_argument('--run-dir',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--port',type=int,default=18772);p.add_argument('--interval',type=float,default=5);a=p.parse_args()
    cpus=read(a.run_dir/'frozen-config.json',{})['spec']['execution']['cpu_ids']
    a.output.mkdir(parents=True,exist_ok=True)
    lock=threading.Lock();data={'latest':None,'history':deque(maxlen=720)}
    def sample():
        before=cpu_ticks()
        while True:
            started=time.monotonic()
            try:
                row,before=collect(a.run_dir,cpus,before)
                with (a.output/'samples.jsonl').open('a') as f:f.write(json.dumps(row)+'\n')
                with lock:
                    data['latest']=row
                    values=[g['util'] for g in row['gpu'] if g['util'] is not None]
                    data['history'].append({'at':row['at'],'cpu_allocated_percent':row['cpu_allocated_percent'],'gpu_mean':sum(values)/len(values) if values else 0,'active':len(row['active']),'waiting':len(row['waiting']),'execution_completed':row['execution_completed']})
            except Exception as e:
                print(type(e).__name__+': '+str(e),flush=True)
            time.sleep(max(.1,a.interval-(time.monotonic()-started)))
    threading.Thread(target=sample,daemon=True).start()
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path=='/':payload=PAGE.encode();kind='text/html; charset=utf-8'
            elif self.path=='/metrics.json':
                with lock:payload=json.dumps({'latest':data['latest'],'history':list(data['history']),'run_dir':str(a.run_dir)}).encode()
                kind='application/json'
            else:self.send_error(404);return
            self.send_response(200);self.send_header('Content-Type',kind);self.send_header('Cache-Control','no-store');self.send_header('Content-Length',str(len(payload)));self.end_headers();self.wfile.write(payload)
        def log_message(self,*args):pass
    ThreadingHTTPServer(('127.0.0.1',a.port),Handler).serve_forever()


if __name__=='__main__':main()
