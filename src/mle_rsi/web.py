"""Authenticated local dashboard and explicit controller actions.

The server reads application records and Dream-RSI's public inspection API. It
never edits agent tables. Expensive actions run through the same installed CLI.
"""
import hmac
import json
import mimetypes
import os
from pathlib import Path
import secrets
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit, parse_qs
from .io import read, write, digest
from . import reporting, controller


class Dashboard:
    def __init__(self, root, token=None):
        self.root = Path(root).resolve()
        self.spec = read(self.root / 'config.json')
        self.tasks = {t['id'] for t in self.spec['tasks']}
        self.token = token or secrets.token_urlsafe(32)
        self.jobs = {}
        self.job_lock = threading.Lock()

    def task(self, name):
        if name not in self.tasks:
            raise ValueError('Unknown task')
        return self.root / 'campaigns' / name

    def launch(self, command, values):
        allowed = {'run': (), 'refresh': ('id', 'task'), 'export': ('task', 'output', 'through_round'),
                   'import': ('task', 'bundle')}
        if command not in allowed or set(values) - set(allowed[command]):
            raise ValueError('Unknown job command/arguments')
        if 'task' in values and values['task'] is not None:
            self.task(values['task'])
        args = [sys.executable, '-m', 'mle_rsi', command, '--run-dir', str(self.root)]
        for key, value in values.items():
            if value is not None:
                args.extend(['--' + key.replace('_', '-'), str(value)])
        with self.job_lock:
            if any(p.poll() is None for p in self.jobs.values()):
                raise ValueError('A dashboard job is already active')
            ident = secrets.token_hex(8)
            folder = self.root / 'web-jobs' / ident
            folder.mkdir(parents=True)
            with (folder / 'stdout.log').open('wb') as log:
                process = subprocess.Popen(args, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
            self.jobs[ident] = process
            write(folder / 'job.json', {'id': ident, 'command': command, 'pid': process.pid, 'started_at': time.time()})
        return {'job_id': ident, 'pid': process.pid}

    def job_status(self):
        results = []
        with self.job_lock:
            for path in sorted((self.root / 'web-jobs').glob('*/job.json')):
                value = read(path)
                process = self.jobs.get(value['id'])
                code = process.poll() if process else value.get('exit_code')
                value['state'] = 'running' if process and code is None else 'completed' if code == 0 else 'failed' if code is not None else 'unknown_after_server_restart'
                if code is not None:
                    value['exit_code'] = code
                    write(path, value)
                log = path.parent / 'stdout.log'
                if log.exists():
                    with log.open('rb') as handle:
                        handle.seek(max(0, log.stat().st_size - 8000))
                        value['log_tail'] = handle.read().decode(errors='replace')
                results.append(value)
        return results[-30:]

    def get(self, path, query):
        if path == '/api/status':
            value = reporting.status(self.root)
            value['host'] = {k: self.spec['host'].get(k, []) for k in ('cpus', 'gpus')}
            value['jobs'] = self.job_status()
            return value
        if path == '/api/resources':
            p = self.root / 'resources.json'
            value = read(p) if p.exists() else {'active': [], 'waiting': [], 'configured_cpus': self.spec['host']['cpus'], 'configured_gpus': self.spec['host'].get('gpus', [])}
            import psutil
            value['controller_alive'] = psutil.pid_exists(value.get('controller_pid', -1))
            return value
        if path == '/api/budget':
            import sqlite3
            with sqlite3.connect((self.root / 'ledger.sqlite').as_uri() + '?mode=ro', uri=True) as db:
                return {'entries': [dict(id=r[0], task=r[1], state=r[2], amounts=json.loads(r[3])) for r in db.execute('SELECT id,task,state,amounts FROM entries ORDER BY id')],
                        'allowances': [dict(id=r[0], task=r[1], dimension=r[2], amount=r[3], reason=r[4]) for r in db.execute('SELECT id,task,dimension,amount,reason FROM allowances')]}
        if path == '/api/history':
            from dream_rsi.inspection import history
            root = self.task(query['task'][0])
            if not (root / 'campaign.sqlite').exists():
                return {'trees': [], 'nodes': [], 'total_nodes': 0}
            return history(root, query.get('tree', [None])[0], int(query.get('offset', ['0'])[0]), int(query.get('limit', ['500'])[0]))
        if path == '/api/candidate':
            from dream_rsi.inspection import candidate
            return candidate(self.task(query['task'][0]), query['tree'][0], query['node'][0])
        if path == '/api/artifact':
            from dream_rsi.inspection import candidate
            node = candidate(self.task(query['task'][0]), query['tree'][0], query['node'][0])
            name = query['name'][0]
            if name not in ('submission.csv', 'validation_predictions.csv', 'training-contract.json', 'candidate-result.json', 'validation-split.json'):
                raise ValueError('Unsupported artifact')
            directory = Path(node['evaluation']['diagnostics']['output_dir']).resolve()
            target = directory / name
            if self.root not in directory.parents or target.is_symlink() or not target.is_file():
                raise ValueError('Artifact is outside this run or unavailable')
            return target
        raise ValueError('Unknown API route')

    def post(self, path, value):
        if path == '/api/control':
            action = value['action']
            if action in ('pause', 'stop'):
                controller.control(self.root, action)
                return {'action': action}
            if action == 'run':
                return self.launch('run', {})
            raise ValueError('Unknown control action')
        if path == '/api/refresh':
            return self.launch('refresh', {'id': value['id'], 'task': value.get('task')})
        if path == '/api/export':
            return self.launch('export', value)
        if path == '/api/import':
            return self.launch('import', value)
        if path == '/api/budget-add':
            from .ledger import Ledger
            task = value.get('task', '')
            if task:
                self.task(task)
            ledger = Ledger(self.root, self.spec.get('budget', {}))
            try:
                ledger.add_allowance(value['id'], value['dimension'], float(value['amount']), value['reason'], task)
            finally:
                ledger.close()
            return {'state': 'added'}
        raise ValueError('Unknown action route')


def make_server(root, bind='127.0.0.1', port=8080, token=None):
    app = Dashboard(root, token)
    assets = Path(__file__).with_name('web_assets')
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass  # Tokens and query values never enter HTTP access logs.

        def send(self, code, body, mime='application/json'):
            self.send_response(code)
            self.send_header('Content-Type', mime)
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def authorized(self):
            return hmac.compare_digest(self.headers.get('Authorization', ''), 'Bearer ' + app.token)

        def do_GET(self):
            parts = urlsplit(self.path)
            files = {'/': 'index.html', '/app.js': 'app.js', '/style.css': 'style.css'}
            if parts.path in files:
                target = assets / files[parts.path]
                self.send(200, target.read_bytes(), mimetypes.guess_type(str(target))[0] or 'text/plain')
                return
            if not self.authorized():
                self.send(401, b'{"error":"Authentication required"}')
                return
            try:
                result = app.get(parts.path, parse_qs(parts.query))
                if isinstance(result, Path):
                    size = result.stat().st_size
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/octet-stream')
                    self.send_header('Content-Disposition', 'attachment; filename="' + result.name + '"')
                    self.send_header('Content-Length', str(size))
                    self.send_header('X-Content-Type-Options', 'nosniff')
                    self.end_headers()
                    import shutil
                    with result.open('rb') as handle:
                        shutil.copyfileobj(handle, self.wfile)
                else:
                    self.send(200, json.dumps(result, ensure_ascii=False, allow_nan=False).encode())
            except (ValueError, KeyError, OSError) as exc:
                self.send(400, json.dumps({'error': str(exc)}).encode())

        def do_POST(self):
            if not self.authorized():
                self.send(401, b'{"error":"Authentication required"}')
                return
            try:
                size = int(self.headers.get('Content-Length', '0'))
                if not 0 < size <= 65536:
                    raise ValueError('Invalid request size')
                value = json.loads(self.rfile.read(size))
                if not isinstance(value, dict):
                    raise ValueError('JSON object required')
                result = app.post(urlsplit(self.path).path, value)
                self.send(200, json.dumps(result).encode())
            except (ValueError, KeyError, OSError, RuntimeError) as exc:
                self.send(400, json.dumps({'error': str(exc)}).encode())
    server = ThreadingHTTPServer((bind, port), Handler)
    server.app = app
    return server


def serve(root, bind='127.0.0.1', port=8080):
    server = make_server(root, bind, port)
    print('Dashboard: http://' + bind + ':' + str(server.server_port), flush=True)
    print('Access token: ' + server.app.token, flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()
