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


if __name__=='__main__':launch()
