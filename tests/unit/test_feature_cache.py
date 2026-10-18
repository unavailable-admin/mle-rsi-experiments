import json
import numpy as np
import pytest
from mle_rsi import runtime, feature_cache
from mle_rsi.io import write


def builder(parameters, identity):
    return {'x': np.arange(identity['rows']) * parameters['scale']}


def test_cache_publish_reuse_invalidation_and_corruption(tmp_path, monkeypatch):
    root = tmp_path / 'run'
    write(root / 'release.json', {'data': {'fixture': {'x': 'abc'}}, 'assets': {},
                                 'sources': {'mle/runtime.py': 'v2'}, 'image_id': 'image'})
    task = {'id': 'fixture'}
    output = root / 'output'
    output.mkdir()
    config = {'output_dir': str(output), 'feature_cache': {'namespace': feature_cache.namespace(root, task),
              'read_dir': str(feature_cache.directory(root, task)), 'max_entry_bytes': 10000}}
    monkeypatch.setattr(runtime, 'task_config', lambda: config)
    first = runtime.deterministic_features('scale', builder, parameters={'scale': 2}, input_identity={'rows': 3})
    assert first['x'].tolist() == [0, 2, 4]
    result = feature_cache.promote(root, task, output, {})
    assert result[0]['state'] == 'published'
    import shutil
    shutil.rmtree(output / '.feature-cache')
    assert runtime.deterministic_features('scale', builder, parameters={'scale': 2}, input_identity={'rows': 3})['x'].tolist() == [0, 2, 4]
    assert not (output / '.feature-cache').exists()
    runtime.deterministic_features('scale', builder, parameters={'scale': 3}, input_identity={'rows': 3})
    assert len(list((output / '.feature-cache').iterdir())) == 1
    entry = feature_cache.directory(root, task) / result[0]['key'] / 'features.npz'
    entry.chmod(0o644)
    entry.write_bytes(b'corrupt')
    with pytest.raises(ValueError, match='Corrupt'):
        runtime.deterministic_features('scale', builder, parameters={'scale': 2}, input_identity={'rows': 3})


def test_cache_rejects_symlink_proposal(tmp_path):
    output = tmp_path / 'output'
    output.mkdir()
    (output / '.feature-cache').symlink_to(tmp_path)
    with pytest.raises(ValueError, match='Unsafe'):
        feature_cache.promote(tmp_path, {'id': 'fixture'}, output, {})
