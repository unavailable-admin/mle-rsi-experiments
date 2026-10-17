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


def validation_mask(ids):
    import numpy as np
    ids = canonical_ids(ids)
    if manifest()['metric']=='text_accuracy':
        return np.asarray([int(x.split('_')[0])%5==0 for x in ids])
    split = split_table();known=set(split['id'])
    unknown=set(ids)-known
    if unknown:raise ValueError('IDs outside fixed public split: '+str(sorted(unknown)[:5]))
    valid=set(split.loc[split['split']=='validation','id'])
    return np.asarray([x in valid for x in ids])


def split_indices(ids):
    import numpy as np
    mask=validation_mask(ids)
    return np.flatnonzero(~mask),np.flatnonzero(mask)


def inner_split(ids, labels=None, validation_fraction=.2):
    """An inner calibration/tuning split confined to the outer training rows.

    Return positions into the supplied IDs. Singletons stay in inner training.
    Never pass outer validation IDs here.
    """
    import numpy as np
    from sklearn.model_selection import train_test_split
    if validation_mask(ids).any():raise ValueError('Calibration must exclude outer validation IDs')
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
        expected=set(split.loc[split['split']=='validation','id'])
        counts=Counter(ids)
        missing=sorted(expected-set(ids));extra=sorted(set(ids)-expected)
        duplicates=sorted(k for k,v in counts.items() if v>1)
        if missing or extra or duplicates:
            raise ValueError('Validation prediction IDs differ: expected='+str(len(expected))+
                ', actual='+str(len(ids))+', missing='+str(missing[:8])+
                ', extra='+str(extra[:8])+', duplicates='+str(duplicates[:8]))
    frame=pd.DataFrame(values,columns=meta['prediction_columns']);frame.insert(0,'id',ids)
    frame.to_csv(out/'validation_predictions.csv',index=False)
    # For pixel tasks the split is image-level; the scorer verifies every pixel.
    split_ids=sorted({x.rsplit('_',2)[0] for x in ids}) if meta['metric']=='rmse_pixels' else ids
    (out/'validation-split.json').write_text(json.dumps({'validation_ids':split_ids}))
    (out/'candidate-result.json').write_text(json.dumps({'validation_metric':task['validation_metric'],
        'validation_score':reported_score,'higher_is_better':task['higher_is_better'],
        'n_train':meta['n_train'],'n_validation':meta['n_validation'],'score_source':'host-recomputed'}))
    if model:progress('validation_output',model=model)


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
        required=['submission.csv','validation_predictions.csv','candidate-result.json','validation-split.json']
        missing=[name for name in required if not (Path(config['output_dir'])/name).is_file()]
        if missing:raise ValueError('Missing required output: '+str(missing))
        progress('completed')
    except BaseException as exc:
        progress('failed',error=f'{type(exc).__name__}: {exc}')
        raise




def record_training_contract(members):
    """Record actual settings of the fitted validation and final models."""
    value = {'members':members}
    for member in members:
        if not member.get('name') or not member.get('validation') or member['validation'] != member.get('final'):
            raise ValueError('Validation/final training configuration mismatch')
    if not members:raise ValueError('Empty training ensemble')
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


def _feature_key(identity):
    import hashlib
    required={'data_sha256','split_sha256','weights_sha256','preprocessing_sha256','extractor_sha256','deterministic','frozen'}
    if set(identity)!=required or identity['deterministic'] is not True or identity['frozen'] is not True:
        raise ValueError('Only deterministic frozen features with a complete identity may be cached')
    for key in required-{'deterministic','frozen'}:
        value=identity[key]
        if not isinstance(value,str) or len(value)!=64 or any(c not in '0123456789abcdef' for c in value):
            raise ValueError('Invalid feature identity: '+key)
    return hashlib.sha256(json.dumps(identity,sort_keys=True).encode()).hexdigest()


def feature_cache_get(identity):
    import numpy as np
    path=Path('/feature-cache')/(_feature_key(identity)+'.npy')
    if not path.exists():return None
    return np.load(path,allow_pickle=False)


def feature_cache_put(identity, values):
    import numpy as np
    import os
    import tempfile
    root=Path('/feature-cache')
    root.mkdir(exist_ok=True)
    key=_feature_key(identity)
    fd,name=tempfile.mkstemp(prefix='.'+key,dir=root)
    try:
        with os.fdopen(fd,'wb') as handle:
            np.save(handle,values,allow_pickle=False);handle.flush();os.fsync(handle.fileno())
        os.replace(name,root/(key+'.npy'))
    finally:
        Path(name).unlink(missing_ok=True)


if __name__=='__main__':launch()
