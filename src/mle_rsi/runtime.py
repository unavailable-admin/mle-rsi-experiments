"""Read-only helper mounted as /runtime/mle_runtime.py inside candidates."""
import argparse
import json
from pathlib import Path
import time

_started = time.monotonic()
_events = []
_split = None


def task_config():
    return json.loads(Path('/task.json').read_text())


def manifest():
    return json.loads(Path('/validation/manifest.json').read_text())


def canonical_ids(ids):
    kind = manifest()['id_kind']
    result=[]
    for x in ids:
        name=Path(str(x)).name
        if kind=='image_stem':
            result.append(Path(name).stem if Path(name).suffix.lower() in ('.jpg','.jpeg','.png','.tif','.tiff','.bmp','.webp') else name)
        else:result.append(name if kind=='filename' else str(x))
    return result


def split_table():
    """Rows: id, row_index, split. IDs, not row order, define membership."""
    import pandas as pd
    global _split
    if _split is None:
        if manifest()['split_file'] is None:raise ValueError('Sentence task: use validation_mask(ids) instead')
        _split = pd.read_csv('/validation/split.csv', dtype={'id':str}, keep_default_na=False)
    return _split.copy()


def validation_mask(ids, fold=None):
    import numpy as np
    ids = canonical_ids(ids)
    if manifest().get('validation_protocol') == 'stratified_kfold':
        if type(fold) is not int or fold not in range(manifest()['n_splits']):
            raise ValueError('Three-fold protocol requires fold=0, 1, or 2')
        return fold_assignments(ids) == fold
    if manifest()['metric']=='text_accuracy':
        return np.asarray([int(x.split('_')[0])%5==0 for x in ids])
    split = split_table();known=set(split['id'])
    unknown=set(ids)-known
    if unknown:raise ValueError('IDs outside fixed public split: '+str(sorted(unknown)[:5]))
    valid=set(split.loc[split['split']=='validation','id'])
    return np.asarray([x in valid for x in ids])


def split_indices(ids, fold=None):
    import numpy as np
    mask=validation_mask(ids, fold=fold)
    return np.flatnonzero(~mask),np.flatnonzero(mask)


def inner_split(ids, labels=None, validation_fraction=.2, fold=None):
    """An inner calibration/tuning split confined to the outer training rows.

    Return positions into the supplied IDs. Singletons stay in inner training.
    Never pass outer validation IDs here.
    """
    import numpy as np
    from sklearn.model_selection import train_test_split
    if validation_mask(ids, fold=fold).any():raise ValueError('Calibration must exclude outer validation IDs')
    if manifest().get('group_column') == 'group':
        from sklearn.model_selection import GroupShuffleSplit
        table = split_table()
        groups = table.set_index('id').loc[canonical_ids(ids), 'group'].to_numpy()
        return next(GroupShuffleSplit(n_splits=1, test_size=validation_fraction, random_state=0).split(ids, groups=groups))
    indices=np.arange(len(ids));rare=np.zeros(len(ids),dtype=bool)
    if labels is not None:
        import pandas as pd
        y=pd.Series(labels);rare=y.map(y.value_counts()).lt(2).to_numpy()
        eligible=indices[~rare];strat=y.iloc[eligible]
        if strat.nunique()>int(np.ceil(validation_fraction*len(eligible))):strat=None
    else:eligible=indices;strat=None
    train,valid=train_test_split(eligible,test_size=validation_fraction,random_state=0,stratify=strat)
    return np.concatenate([train,indices[rare]]),valid


def write_validation(ids, predictions, reported_score=None, model=None):
    """Predictions use manifest.prediction_columns order; no labels required.

    Denoising IDs are imageStem_row_column (1-based pixels), not image IDs.
    Submission.csv is separate and must follow the competition format.
    """
    import numpy as np
    import pandas as pd
    meta=manifest();task=task_config();out=Path(task['output_dir']);out.mkdir(exist_ok=True)
    ids=list(map(str,ids)) if meta['metric']=='rmse_pixels' else canonical_ids(ids)
    values=np.asarray(predictions)
    if values.ndim==1:values=values[:,None]
    if values.shape != (len(ids),len(meta['prediction_columns'])):raise ValueError('Wrong prediction shape for fixed metric')
    if meta.get('split_file') and meta['metric'] not in ('rmse_pixels', 'text_accuracy'):
        from collections import Counter
        split=split_table()
        expected=set(split['id']) if meta.get('validation_protocol') == 'stratified_kfold' else set(split.loc[split['split']=='validation','id'])
        counts=Counter(ids)
        missing=sorted(expected-set(ids));extra=sorted(set(ids)-expected)
        duplicates=sorted(k for k,v in counts.items() if v>1)
        if missing or extra or duplicates:
            raise ValueError('Validation prediction IDs differ: expected='+str(len(expected))+
                ', actual='+str(len(ids))+', missing='+str(missing[:8])+
                ', extra='+str(extra[:8])+', duplicates='+str(duplicates[:8]))
    frame=pd.DataFrame(values,columns=meta['prediction_columns']);frame.insert(0,'id',ids)
    if meta.get('validation_protocol') == 'stratified_kfold':
        frame.insert(1, 'fold', fold_assignments(ids))
    frame.to_csv(out/'validation_predictions.csv',index=False)
    # For pixel tasks the split is image-level; the scorer verifies every pixel.
    split_ids=sorted({x.rsplit('_',2)[0] for x in ids}) if meta['metric']=='rmse_pixels' else ids
    (out/'validation-split.json').write_text(json.dumps({'validation_ids':split_ids}))
    (out/'candidate-result.json').write_text(json.dumps({'validation_metric':task['validation_metric'],
        'validation_score':reported_score,'higher_is_better':task['higher_is_better'],
        'n_train':meta['n_train'],'n_validation':meta['n_validation'],'score_source':'host-recomputed'}))
    if model:progress('validation_output',model=model)


def fold_assignments(ids):
    """Host-owned fold ID for every supplied public training sample."""
    import numpy as np
    if manifest().get('validation_protocol') != 'stratified_kfold':
        raise ValueError('This task does not use K-fold validation')
    ids = canonical_ids(ids)
    split = split_table().set_index('id')
    if not set(ids) <= set(split.index):
        raise ValueError('IDs outside fixed public folds')
    return np.asarray(split.loc[ids, 'fold'], dtype=int)


def _ids_hash(ids):
    import hashlib
    return hashlib.sha256(json.dumps(sorted(map(str, ids)), separators=(',', ':')).encode()).hexdigest()


def record_kfold_training_contract(members, fold_training, final_ids):
    """Record actual fold membership and full-data final refit; never infer fits."""
    meta = manifest()
    if meta.get('validation_protocol') != 'stratified_kfold':
        raise ValueError('This task does not use K-fold validation')
    split = split_table(); all_ids = set(split['id'])
    records = []
    if len(fold_training) != meta['n_splits']:
        raise ValueError('Missing fold training records')
    for fold, row in enumerate(fold_training):
        if row['fold'] != fold:
            raise ValueError('Fold records must be in order 0,1,2')
        train = canonical_ids(row['train_ids']); valid = canonical_ids(row['validation_ids'])
        expected = set(split.loc[split['fold'] == fold, 'id'])
        if (len(set(train)) != len(train) or len(set(valid)) != len(valid)
                or set(train) != all_ids-expected or set(valid) != expected):
            raise ValueError('Fold training/validation membership differs from host split')
        records.append({'fold': fold, 'n_train': len(train), 'n_validation': len(valid),
                        'train_ids_sha256': _ids_hash(train), 'validation_ids_sha256': _ids_hash(valid)})
    final_ids = canonical_ids(final_ids)
    if len(final_ids) != len(all_ids) or set(final_ids) != all_ids:
        raise ValueError('Final model must refit on all public training rows')
    for member in members:
        if member.get('folds') != [member.get('validation')]*meta['n_splits']:
            raise ValueError('Fold training configurations differ')
    record_training_contract(members)
    out = Path(task_config()['output_dir'])
    value = json.loads((out/'training-contract.json').read_text())
    value.update(validation_protocol='stratified_kfold', n_splits=meta['n_splits'], fold_training=records,
                 final_training={'n_train': len(final_ids), 'ids_sha256': _ids_hash(final_ids), 'refit': True})
    temporary = out/'training-contract.json.tmp'
    temporary.write_text(json.dumps(value, allow_nan=False))
    temporary.replace(out/'training-contract.json')


def remaining_seconds(reserve=45):
    return max(0.,task_config()['budget']['wall_seconds']-(time.monotonic()-_started)-reserve)


def progress(stage, **details):
    event={**details,'stage':stage,'elapsed_seconds':round(time.monotonic()-_started,3)}
    _events.append(event);print('MLE_PROGRESS '+json.dumps(event),flush=True)
    _save_audit()


def report_model(name, device='cpu', checkpoint=None, fallback_reason=None):
    progress('model',name=name,device=str(device),checkpoint=checkpoint,fallback_reason=fallback_reason)


def _save_audit():
    out=Path(task_config()['output_dir']);out.mkdir(exist_ok=True)
    (out/'runtime-audit.json').write_text(json.dumps({'events':_events},indent=2))


def load_resnet50(device=None):
    """Load the available local checkpoint explicitly; never silently fallback."""
    import torch
    from torchvision.models import resnet50
    from torchvision import transforms
    inventory=json.loads(Path('/runtime/inventory.json').read_text())
    paths=[p for p in inventory['torch_checkpoints'] if Path(p).name.startswith('resnet50-')]
    if not paths:raise FileNotFoundError('No ResNet50 in inventory; choose an explicit alternative and report_model(fallback_reason=...)')
    path=paths[0];model=resnet50(weights=None)
    state=torch.load(path,map_location='cpu',weights_only=True)
    model.load_state_dict(state,strict=True)
    device=device or ('cuda' if torch.cuda.is_available() else 'cpu')
    model=model.to(device)
    transform=transforms.Compose([transforms.Resize(256),transforms.CenterCrop(224),transforms.ToTensor(),
        transforms.Normalize([.485,.456,.406],[.229,.224,.225])])
    report_model('resnet50',device=device,checkpoint=path)
    return model,transform


def launch():
    import runpy
    import sys
    sys.modules['mle_runtime']=sys.modules[__name__]
    parser=argparse.ArgumentParser();parser.add_argument('agent');args=parser.parse_args()
    config=task_config()
    if not isinstance(config['budget'],dict) or not isinstance(config['budget']['wall_seconds'],(int,float)):
        raise ValueError('Host task budget schema invalid')
    progress('preflight',budget=config['budget'],validation_metric=config['validation_metric'])
    source=Path(args.agent).read_text();compile(source,args.agent,'exec')
    sys.argv=[args.agent,'--task','/task.json']
    try:
        runpy.run_path(args.agent,run_name='__main__')
        required=['submission.csv','validation_predictions.csv','candidate-result.json','validation-split.json','training-contract.json']
        missing=[name for name in required if not (Path(config['output_dir'])/name).is_file()]
        if missing:raise ValueError('Missing required output: '+str(missing))
        training = json.loads((Path(config['output_dir'])/'training-contract.json').read_text())
        validate_training_members(training.get('members') if isinstance(training, dict) else None)
        progress('completed')
    except BaseException as exc:
        progress('failed',error=f'{type(exc).__name__}: {exc}')
        raise




def validate_training_members(members):
    hint = "Use rt.record_training_contract([{'name': 'model', 'validation': settings, 'final': settings}]) with actual fitted settings."
    if not isinstance(members, list) or not members:
        raise ValueError('Training contract requires a nonempty members list. ' + hint)
    for member in members:
        if (not isinstance(member, dict) or not isinstance(member.get('name'), str)
                or not member['name'] or not isinstance(member.get('validation'), dict)
                or not member['validation'] or member['validation'] != member.get('final')):
            raise ValueError('Training member requires name and matching nonempty validation/final settings. ' + hint)


def record_training_contract(members):
    """Record actual settings of the fitted validation and final models."""
    validate_training_members(members)
    value = {'members': members}
    out=Path(task_config()['output_dir'])
    temporary=out/'training-contract.json.tmp'
    temporary.write_text(json.dumps(value,allow_nan=False))
    temporary.replace(out/'training-contract.json')


def commit_outputs():
    """Publish one complete, immutable output bundle with content hashes."""
    import hashlib
    import os
    import shutil
    import tempfile
    out=Path(task_config()['output_dir'])
    names=('submission.csv','validation_predictions.csv','candidate-result.json',
           'validation-split.json','training-contract.json')
    stage=Path(tempfile.mkdtemp(prefix='.bundle-',dir=out))
    try:
        hashes={}
        for name in names:
            source=out/name
            if source.is_symlink() or not source.is_file() or source.stat().st_size==0:
                raise ValueError('Incomplete bundle: '+name)
            shutil.copyfile(source,stage/name)
            hashes[name]=hashlib.sha256((stage/name).read_bytes()).hexdigest()
        (stage/'seal.json').write_text(json.dumps(hashes))
        for path in stage.iterdir():
            with path.open('rb') as handle:os.fsync(handle.fileno())
        # Successive bundles get new names; an atomic pointer selects a complete one.
        final=out/('bundle-'+stage.name.removeprefix('.bundle-'))
        stage.rename(final)
        pointer=out/'complete-bundle.json.tmp'
        pointer.write_text(json.dumps({'directory':final.name,'hashes':hashes}))
        pointer.replace(out/'complete-bundle.json')
        return final
    finally:
        if stage.exists():shutil.rmtree(stage)



def deterministic_features(name, builder, *, parameters, input_identity):
    """Cache label-independent deterministic preprocessing, never fitted models.

    builder takes (parameters, input_identity) and returns a dict of numeric
    numpy arrays. Declare every dependency in those JSON values, including IDs,
    preprocessing settings, model/weight hashes and random seeds. Function
    source, public data, split, image and runtime also enter the key. Closures
    are rejected. Uncached behavior remains available when caching is disabled.
    """
    import hashlib
    import inspect
    import os
    import tempfile
    import numpy as np

    def encoded(value):
        return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False).encode()

    config = task_config().get('feature_cache')
    if not config:
        return builder(parameters, input_identity)
    if getattr(builder, '__closure__', None) or inspect.ismethod(builder):
        raise ValueError('Cached builder must not depend on a closure or bound instance')
    identity = {'schema': 1, 'namespace': config['namespace'], 'name': name,
                'builder': hashlib.sha256(inspect.getsource(builder).encode()).hexdigest(),
                'parameters': parameters, 'inputs': input_identity}
    key = hashlib.sha256(encoded(identity)).hexdigest()
    def load(entry):
        meta = json.loads((entry / 'metadata.json').read_text())
        data = entry / 'features.npz'
        if meta['identity'] != identity or hashlib.sha256(data.read_bytes()).hexdigest() != meta['sha256']:
            raise ValueError('Corrupt feature cache')
        with np.load(data, allow_pickle=False) as arrays:
            return {k: arrays[k].copy() for k in arrays.files}
    published = Path(config['read_dir']) / key
    if published.is_dir():
        return load(published)
    pending = Path(task_config()['output_dir']) / '.feature-cache'
    pending.mkdir(exist_ok=True)
    entry = pending / key
    if entry.is_dir():
        return load(entry)
    result = builder(parameters, input_identity)
    if not isinstance(result, dict) or not result or any(not isinstance(k, str) or not k or '/' in k for k in result):
        raise ValueError('Feature builder must return named numeric arrays')
    arrays = {k: np.asarray(v) for k, v in result.items()}
    if any(v.dtype.hasobject or v.dtype.kind not in 'biufc' for v in arrays.values()):
        raise ValueError('Only numeric feature arrays may be cached')
    import shutil
    temporary = Path(tempfile.mkdtemp(prefix='.entry-', dir=pending))
    try:
        data = temporary / 'features.npz'
        np.savez(data, **arrays)
        if data.stat().st_size > config['max_entry_bytes']:
            raise ValueError('Feature cache entry exceeds bound')
        meta = {'identity': identity, 'sha256': hashlib.sha256(data.read_bytes()).hexdigest()}
        (temporary / 'metadata.json').write_bytes(encoded(meta))
        os.rename(temporary, entry)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
    return arrays


if __name__ == '__main__':
    launch()
