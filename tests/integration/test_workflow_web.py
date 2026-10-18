import asyncio
import json
import threading
import urllib.request
import urllib.error
import urllib.parse
import pytest
from mle_rsi.io import write, read
from mle_rsi.workflow import execute
from mle_rsi.controller import initialize, run
from mle_rsi.web import make_server


@pytest.mark.asyncio
async def test_guarded_stages_queue_and_idempotent_resume(tmp_path, spec):
    path = tmp_path / 'workflow.json'
    write(path, {'schema': 1, 'jobs': [{'id': 'development', 'config': spec},
        {'id': 'final', 'config': {**spec, 'rounds': 2}, 'after': ['development'],
         'inherit_from': 'development', 'gate': {'min_valid_candidates': 4}}]})
    root = tmp_path / 'workflow'
    result = await execute(path, root)
    assert all(v['state'] == 'completed' for v in result['jobs'].values())
    before = read(root / 'jobs/final/status/fixture.json')
    assert before['summary']['completed_rounds'] == 2
    assert await execute(path, root) == result
    assert before == read(root / 'jobs/final/status/fixture.json')


@pytest.mark.asyncio
async def test_guard_blocks_unqualified_final(tmp_path, spec):
    path = tmp_path / 'workflow.json'
    write(path, {'schema': 1, 'jobs': [{'id': 'dev', 'config': spec},
        {'id': 'final', 'config': spec, 'after': ['dev'], 'gate': {'min_valid_candidates': 999}}]})
    with pytest.raises(ValueError, match='gate blocked'):
        await execute(path, tmp_path / 'jobs')
    assert not (tmp_path / 'jobs/jobs/final').exists()


@pytest.mark.asyncio
async def test_web_auth_history_source_budget_and_stop(tmp_path, spec):
    root = initialize(tmp_path / 'run', spec)
    await run(root)
    server = make_server(root, port=0, token='fixture-token')
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = 'http://127.0.0.1:' + str(server.server_port)
    def request(path, data=None, token='fixture-token'):
        req = urllib.request.Request(base + path, headers={'Authorization': 'Bearer ' + token},
            data=None if data is None else json.dumps(data).encode())
        return urllib.request.urlopen(req, timeout=5).read()
    try:
        assert b'\xe5\xae\x9e\xe9\xaa\x8c' in request('/')
        with pytest.raises(urllib.error.HTTPError) as failure:
            request('/api/status', token='wrong')
        assert failure.value.code == 401
        assert json.loads(request('/api/status'))['task_count'] == 1
        tree = json.loads(request('/api/history?task=fixture'))
        assert tree['total_nodes'] == 5
        node = tree['nodes'][1]['node_id']
        query = urllib.parse.urlencode({'task': 'fixture', 'tree': tree['tree_id'], 'node': node})
        assert json.loads(request('/api/candidate?' + query))['artifact']['source']
        request('/api/budget-add', {'id': 'extra', 'dimension': 'api_usd', 'amount': 1, 'reason': 'test'})
        assert len(json.loads(request('/api/budget'))['allowances']) == 1
        request('/api/control', {'action': 'stop'})
        assert read(root / 'control.json')['action'] == 'stop'
        with pytest.raises(urllib.error.HTTPError):
            request('/api/history?task=../../escape')
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
