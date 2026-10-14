import json
import pytest
from mle_rsi.common import load_spec,read
from mle_rsi import REPO
from mle_rsi.backend import MLEBackend
from mle_rsi.gpt_provider import GPTTransport
from mle_rsi.provider import load_key


def test_config_expands_new_paths_and_rejects_missing_variables(tmp_path,monkeypatch):
    p=tmp_path/'config.json';p.write_text(json.dumps({'path':'${TEST_MLE_ROOT}/data','groups':[['${TEST_MLE_ROOT}']]}))
    monkeypatch.setenv('TEST_MLE_ROOT','/tmp/workspace')
    assert load_spec(p)=={'path':'/tmp/workspace/data','groups':[['/tmp/workspace']]}
    assert read(p)['path']=='${TEST_MLE_ROOT}/data'
    monkeypatch.delenv('TEST_MLE_ROOT')
    with pytest.raises(ValueError,match='Unresolved'):load_spec(p)


def test_env_only_auth_has_clear_missing_key_error(monkeypatch):
    monkeypatch.delenv('TEST_MLE_AUTH',raising=False)
    with pytest.raises(RuntimeError,match='Set TEST_MLE_AUTH'):
        load_key({'model':{'api_key_env':'TEST_MLE_AUTH'}})


def test_guarded_configuration_keeps_serial_defaults(tmp_path):
    s=load_spec(REPO/'configs/gpt-firstbatch-guarded-dev4-lite22.json')
    backend=MLEBackend('spooky-author-identification',tmp_path,s,'development',mock=True)
    transport=GPTTransport(tmp_path,s,'spooky-author-identification','development','test')
    assert backend.cpu_slots is None
    assert transport.gate._value==1
    assert s['search']['development_rounds']*s['search']['batches_per_round']*s['search']['workers']==32
    assert s['search']['final_batches']*s['search']['workers']==4
