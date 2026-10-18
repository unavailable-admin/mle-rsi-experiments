import json
import re
import sys

import pytest
from mle_rsi import runtime as rt, tasks


def test_documented_holdout_training_example_publishes_valid_bundle(tmp_path, monkeypatch):
    monkeypatch.setattr(rt, 'task_config', lambda: {'output_dir': str(tmp_path)})
    prompt = tasks.contract({'task': 'leaf-classification', 'metric': 'multiclass'})
    assert 'public_data_dir' not in prompt
    assert 'data_dir=/data' in prompt
    example = re.search(r'rt.record_training_contract\(\[.*?\]\)', prompt, re.S).group()
    for name in ('submission.csv', 'validation_predictions.csv', 'validation-split.json', 'candidate-result.json'):
        (tmp_path/name).write_text('{}')
    exec(example, {'rt': rt, 'settings': {'model': 'fitted-fixture', 'seed': 0}})
    tasks.verify_training(tmp_path)
    bundle = rt.commit_outputs()
    assert (bundle/'training-contract.json').is_file()


@pytest.mark.parametrize('members', [None, [], {}, ['invalid'], [{'name': 'model'}],
    [{'name': 'model', 'validation': {'seed': 0}, 'final': {'seed': 1}}]])
def test_bad_training_records_explain_actual_api(members):
    with pytest.raises(ValueError, match='rt.record_training_contract'):
        rt.validate_training_members(members)


@pytest.mark.parametrize('record', [None, {}, {'members': []}])
def test_launch_rejects_missing_or_malformed_training_record(tmp_path, monkeypatch, record):
    agent = tmp_path/'agent.py'
    agent.write_text('pass')
    for name in ('submission.csv', 'validation_predictions.csv', 'candidate-result.json', 'validation-split.json'):
        (tmp_path/name).write_text('{}')
    if record is not None:
        (tmp_path/'training-contract.json').write_text(json.dumps(record))
    monkeypatch.setattr(rt, 'task_config', lambda: {'output_dir': str(tmp_path),
        'budget': {'wall_seconds': 10}, 'validation_metric': 'multiclass'})
    monkeypatch.setitem(sys.modules, 'mle_runtime', rt)
    monkeypatch.setattr(sys, 'argv', ['runtime', str(agent)])
    with pytest.raises(ValueError, match='training-contract.json|rt.record_training_contract'):
        rt.launch()
