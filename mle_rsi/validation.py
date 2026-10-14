"""Host-owned public splits and independent scoring. Never reads private data.

Run with the installed ML Python, not the lightweight controller environment.
Candidates see a read-only manifest and split, while the host scores their CSV
against its own labels. This verifies predictions, not arbitrary training code.
"""
import argparse
import csv
import hashlib
import json
from pathlib import Path
import shutil
import zipfile


TABLES = {
    'aerial-cactus-identification': ('train.csv', 'id', ['has_cactus'], 'auc'),
    'aptos2019-blindness-detection': ('train.csv', 'id_code', ['diagnosis'], 'kappa'),
    'detecting-insults-in-social-commentary': ('train.csv', None, ['Insult'], 'auc'),
    'dog-breed-identification': ('labels.csv', 'id', ['breed'], 'multiclass'),
    'histopathologic-cancer-detection': ('train_labels.csv', 'id', ['label'], 'auc'),
    'jigsaw-toxic-comment-classification-challenge': ('train.csv', 'id', ['toxic', 'severe_toxic', 'obscene', 'threat', 'insult', 'identity_hate'], 'macro_auc'),
    'leaf-classification': ('train.csv', 'id', ['species'], 'multiclass'),
    'nomad2018-predict-transparent-conductors': ('train.csv', 'id', ['formation_energy_ev_natom', 'bandgap_energy_ev'], 'rmsle'),
    'plant-pathology-2020-fgvc7': ('train.csv', 'image_id', ['healthy', 'multiple_diseases', 'rust', 'scab'], 'macro_auc'),
    'ranzcr-clip-catheter-line-classification': ('train.csv', 'StudyInstanceUID', ['ETT - Abnormal', 'ETT - Borderline', 'ETT - Normal', 'NGT - Abnormal', 'NGT - Borderline', 'NGT - Incompletely Imaged', 'NGT - Normal', 'CVC - Abnormal', 'CVC - Borderline', 'CVC - Normal', 'Swan Ganz Catheter Present'], 'macro_auc'),
    'siim-isic-melanoma-classification': ('train.csv', 'image_name', ['target'], 'auc'),
    'spooky-author-identification': ('train.csv', 'id', ['author'], 'multiclass'),
    'tabular-playground-series-dec-2021': ('train.csv', 'Id', ['Cover_Type'], 'accuracy'),
    'tabular-playground-series-may-2022': ('train.csv', 'id', ['target'], 'auc'),
}


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def file_hash(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def canonical_id(value, kind):
    value = str(value)
    if kind == 'image_stem':
        name=Path(value).name
        return Path(name).stem if Path(name).suffix.lower() in ('.jpg','.jpeg','.png','.tif','.tiff','.bmp','.webp') else name
    if kind == 'filename':
        return Path(value).name
    return value


def fixed_split(frame, targets, group=None, stratify=False):
    import numpy as np
    from sklearn.model_selection import GroupShuffleSplit, train_test_split
    indices = np.arange(len(frame))
    if group:
        groups = frame[group].fillna(frame['id']).astype(str)
        tr, va = next(GroupShuffleSplit(n_splits=1, test_size=.2, random_state=0).split(indices, groups=groups))
    else:
        y = frame[targets[0]] if stratify else None
        # Singleton classes stay in training. They cannot support a holdout.
        rare = y.map(y.value_counts()).lt(2).to_numpy() if y is not None else np.zeros(len(frame), dtype=bool)
        eligible = indices[~rare]
        labels = y.iloc[eligible] if y is not None else None
        nvalid = int(np.ceil(.2 * len(eligible)))
        if labels is not None and (labels.nunique() > nvalid or labels.nunique() > len(eligible)-nvalid):
            labels = None
        tr, va = train_test_split(eligible, test_size=.2, random_state=0, stratify=labels)
        tr = np.concatenate([tr, indices[rare]])
    return tr, va


def prepare(task, public, destination):
    import numpy as np
    import pandas as pd
    public, destination = Path(public), Path(destination)
    if (destination/'manifest.json').exists():
        verify_prepared(destination)
        return
    destination.mkdir(parents=True, exist_ok=True)
    meta = {'version': 2, 'task': task, 'seed': 0, 'id_kind': 'literal',
            'split_file': '/validation/split.csv', 'manifest_path': '/validation/manifest.json',
            'prediction_file': 'validation_predictions.csv', 'prediction_id_column': 'id',
            'calibration': 'Fit preprocessing and calibration on training or inner training folds only; never fit on the outer validation labels.',
            'allowed_feature_columns': None, 'data_overrides': {}}
    if task in TABLES:
        filename, idcol, targets, metric = TABLES[task]
        if metric=='macro_auc' and (public/'sample_submission.csv').exists():
            # Some prepared benchmarks grade fewer labels than the raw train CSV.
            targets=[c for c in pd.read_csv(public/'sample_submission.csv',nrows=0).columns if c!=idcol]
        needed = ([idcol] if idcol else []) + targets
        group = 'PatientID' if task.startswith('ranzcr-') else 'patient_id' if task.startswith('siim-') else None
        if group: needed.append(group)
        frame = pd.read_csv(public/filename, usecols=needed)
        frame['id'] = frame[idcol].astype(str) if idcol else frame.index.astype(str)
        frame['row_index'] = np.arange(len(frame))
        tr, va = fixed_split(frame, targets, group, metric in ('multiclass','kappa','accuracy','auc') and not group)
        meta.update(source_file=filename, source_id_column=idcol, group_column=group)
        if (public/'test.csv').exists():
            test_columns=pd.read_csv(public/'test.csv',nrows=0).columns.tolist()
            train_columns=pd.read_csv(public/filename,nrows=0).columns.tolist()
            meta['allowed_feature_columns']=[c for c in train_columns if c in test_columns and c not in targets]
            meta['excluded_features']=[c for c in train_columns if c not in test_columns and c not in targets]
        if task.startswith('siim-'):
            original = pd.read_csv(public/filename)
            testcols = pd.read_csv(public/'test.csv', nrows=0).columns.tolist()
            allowed = [c for c in original if c in testcols]
            original[allowed+targets].to_csv(destination/'sanitized-train.csv', index=False)
            meta.update(allowed_feature_columns=allowed, excluded_features=[c for c in original if c not in allowed+targets],
                        data_overrides={'train.csv': 'sanitized-train.csv'})
    elif task == 'random-acts-of-pizza':
        records = json.loads((public/'train.json').read_text())
        test = json.loads((public/'test.json').read_text())
        available = set.intersection(*(set(r) for r in test))
        targets, metric = ['requester_received_pizza'], 'auc'
        safe = [{k: v for k, v in r.items() if k in available or k in targets} for r in records]
        sanitized = destination/'public';sanitized.mkdir(exist_ok=True)
        write_json(sanitized/'train.json', safe)
        for name in ['test.json', 'sampleSubmission.csv', 'description.md']:
            shutil.copyfile(public/name, sanitized/name)
        frame = pd.DataFrame({'id': [r['request_id'] for r in records], targets[0]: [int(r[targets[0]]) for r in records], 'row_index': np.arange(len(records))})
        tr, va = fixed_split(frame, targets, stratify=True)
        meta.update(source_file='train.json', source_id_column='request_id', sanitized_public='public',
                    allowed_feature_columns=sorted(available), excluded_features=sorted(set.union(*(set(r) for r in records))-available-set(targets)))
    elif task == 'dogs-vs-cats-redux-kernels-edition':
        names = sorted(p.name for p in (public/'train').rglob('*.jpg'))
        frame = pd.DataFrame({'id': [Path(n).stem for n in names], 'label': [int(n.startswith('dog.')) for n in names], 'row_index': np.arange(len(names))})
        targets, metric = ['label'], 'logloss'
        tr, va = fixed_split(frame, targets, stratify=True)
        meta.update(id_kind='image_stem', source_file='train/', source_id_column='filename stem')
    elif task.startswith('the-icml-2013-whale'):
        with zipfile.ZipFile(public/'train2.zip') as z:
            names = sorted(n for n in z.namelist() if n.endswith(('.aif','.aiff')))
        frame = pd.DataFrame({'id': [Path(n).name for n in names], 'label': [int(Path(n).stem.rsplit('_',1)[1]) for n in names], 'row_index': np.arange(len(names))})
        targets, metric = ['label'], 'auc'
        tr, va = fixed_split(frame, targets, stratify=True)
        meta.update(id_kind='filename', source_file='train2.zip', source_id_column='filename')
    elif task == 'mlsp-2013-birds':
        rows = []
        for line in (public/'essential_data/rec_labels_test_hidden.txt').read_text().splitlines()[1:]:
            parts = line.split(',')
            if '?' in parts: continue
            labels = {int(x) for x in parts[1:] if x}
            rows.append({'id': parts[0], **{str(j): int(j in labels) for j in range(19)}})
        frame = pd.DataFrame(rows);frame['row_index'] = np.arange(len(frame))
        targets, metric = list(map(str,range(19))), 'micro_auc'
        tr, va = fixed_split(frame, targets)
        meta.update(source_file='essential_data/rec_labels_test_hidden.txt', source_id_column='rec_id')
    elif task == 'denoising-dirty-documents':
        names = sorted(p.name for p in (public/'train').glob('*.png'))
        frame = pd.DataFrame({'id': [Path(n).stem for n in names], 'row_index': np.arange(len(names))})
        targets, metric = ['value'], 'rmse_pixels'
        tr, va = fixed_split(frame, targets)
        meta.update(id_kind='image_stem', source_file='train/', source_id_column='image stem',
                    pixel_id_format='imageStem_row_column, rows and columns are 1-based; predictions in [0,1]')
        from PIL import Image
        with (destination/'truth.csv').open('w') as f:
            writer = csv.writer(f);writer.writerow(['id','value'])
            for i in va:
                ident = frame.iloc[i]['id'];a = np.asarray(Image.open(public/'train_cleaned'/f'{ident}.png').convert('L'), dtype=float)/255
                for r in range(a.shape[0]):
                    writer.writerows((f'{ident}_{r+1}_{c+1}',float(a[r,c])) for c in range(a.shape[1]))
    elif task.startswith('text-normalization-challenge-'):
        lang = 'en' if 'english' in task else 'ru'
        archive = public/f'{lang}_train.csv.zip'
        targets, metric = ['after'], 'text_accuracy'
        ntrain = nvalid = 0
        with zipfile.ZipFile(archive) as z, z.open(next(n for n in z.namelist() if n.endswith('.csv'))) as f:
            for chunk in pd.read_csv(f, keep_default_na=False, chunksize=200000):
                mask = chunk['sentence_id'].astype('int64') % 5 == 0
                truth = pd.DataFrame({'id': chunk.loc[mask,'sentence_id'].astype(str)+'_'+chunk.loc[mask,'token_id'].astype(str), 'after': chunk.loc[mask,'after']})
                truth.to_csv(destination/'truth.csv', index=False, mode='a' if nvalid else 'w', header=not nvalid)
                ntrain += int((~mask).sum());nvalid += int(mask.sum())
        meta.update(source_file=archive.name, source_id_column='sentence_id_token_id', split_file=None,
                    allowed_feature_columns=['sentence_id','token_id','before'],excluded_features=['class'],
                    split_rule='sentence_id modulo 5 equals 0 for validation; all other sentences for training',
                    metric=metric, targets=targets, prediction_columns=['prediction'], n_train=ntrain, n_validation=nvalid,
                    validation_unit='token; grouped by sentence')
        finish_manifest(destination, meta)
        return
    elif task == 'new-york-city-taxi-fare-prediction':
        # Fixed public subset, independent of candidate. Never change it mid-run.
        # 1.2M training + 300k validation matches the preceding run's sizes.
        frame = pd.read_csv(public/'labels.csv', usecols=['key','fare_amount'], nrows=1500000)
        frame['id'] = frame['key'].astype(str);frame['row_index'] = np.arange(len(frame))
        targets, metric = ['fare_amount'], 'rmse'
        va = np.flatnonzero(frame['row_index'].to_numpy()%5 == 0)
        tr = np.flatnonzero(frame['row_index'].to_numpy()%5 != 0)
        meta.update(source_file='labels.csv', source_id_column='key', public_row_limit=1500000,
                    training_note='For validation training use only split=train rows. After validation, final refit may use all public training data within the unchanged budget.')
    else:
        raise ValueError('No trusted validation adapter for '+task)
    split = frame[['id','row_index']].copy();split['split'] = 'train';split.loc[va,'split'] = 'validation'
    split.to_csv(destination/'split.csv',index=False)
    if metric != 'rmse_pixels':frame.iloc[va][['id']+targets].to_csv(destination/'truth.csv',index=False)
    predictions = sorted(frame[targets[0]].astype(str).unique().tolist()) if metric=='multiclass' else targets if len(targets)>1 else ['prediction']
    meta.update(metric=metric, targets=targets, prediction_columns=predictions, n_train=len(tr), n_validation=len(va),
                validation_unit='image' if metric=='rmse_pixels' else 'row')
    if metric=='rmse_pixels':meta['prediction_columns']=['prediction']
    finish_manifest(destination, meta)


def finish_manifest(destination, meta):
    files = [p for p in destination.rglob('*') if p.is_file() and p.name!='manifest.json']
    meta['file_hashes'] = {str(p.relative_to(destination)): file_hash(p) for p in files}
    write_json(destination/'manifest.json', meta)


def verify_prepared(directory):
    meta = json.loads((directory/'manifest.json').read_text())
    for name, expected in meta['file_hashes'].items():
        if file_hash(directory/name) != expected:
            raise ValueError('Prepared validation data changed: '+name)
    return meta



def validate_submission(public, output, meta):
    """Check final artifact against public sample only; never read test labels."""
    import numpy as np
    import pandas as pd
    samples=[p for p in Path(public).iterdir() if 'sample' in p.name.lower()
             and (p.name.lower().endswith('.csv') or p.name.lower().endswith('.csv.zip'))]
    if len(samples)!=1:raise ValueError('Expected one public sample submission, found '+str(len(samples)))
    sample=pd.read_csv(samples[0],dtype=str,keep_default_na=False)
    submission=pd.read_csv(Path(output)/'submission.csv',dtype=str,keep_default_na=False)
    insult=meta['task']=='detecting-insults-in-social-commentary'
    id_column='Comment' if insult else sample.columns[0]
    predictions=['Insult'] if insult else list(sample.columns[1:])
    required=[id_column]+predictions
    missing=set(required)-set(submission.columns)
    if missing:raise ValueError('Submission missing official columns '+str(sorted(missing))+'; follow public sample '+samples[0].name)
    if len(submission)!=len(sample):raise ValueError('Submission must cover all public test IDs: expected '+str(len(sample))+' rows, got '+str(len(submission)))
    if not np.array_equal(np.sort(submission[id_column].to_numpy()),np.sort(sample[id_column].to_numpy())):
        raise ValueError('Submission IDs differ from public sample (missing, extra or duplicated IDs)')
    metric=meta['metric']
    if metric!='text_accuracy':
        values=submission[predictions].to_numpy(dtype=float)
        if not np.isfinite(values).all():raise ValueError('Non-finite submission predictions')
        if metric in ('auc','micro_auc','macro_auc','logloss','multiclass','rmse_pixels') and ((values<0).any() or (values>1).any()):
            raise ValueError('Submission probabilities/pixels must be in [0,1]')
        if metric=='multiclass' and not np.allclose(values.sum(axis=1),1,atol=1e-6):
            raise ValueError('Submission class probabilities must sum to one')
        if metric in ('accuracy','kappa') and not np.equal(values,np.floor(values)).all():
            raise ValueError('Submission class labels must be integers')
        if metric=='kappa' and ((values<0).any() or (values>4).any()):raise ValueError('Submission diagnosis must be 0..4')
        if metric=='rmsle' and (values<0).any():raise ValueError('Submission RMSLE predictions must be nonnegative')
    return {'public_schema_checked':True,'rows':len(submission),'sample_sha256':file_hash(samples[0]),
            'id_column':id_column,'prediction_columns':predictions,'private_labels_used':False}


def score(directory, output, public=None):
    import numpy as np
    import pandas as pd
    from sklearn.metrics import accuracy_score, cohen_kappa_score, log_loss, roc_auc_score
    directory, output = Path(directory), Path(output)
    meta = verify_prepared(directory)
    submission_check=validate_submission(public,output,meta) if public is not None else None
    truth = pd.read_csv(directory/'truth.csv', dtype=str, keep_default_na=False)
    pred = pd.read_csv(output/'validation_predictions.csv', dtype=str, keep_default_na=False)
    columns = meta['prediction_columns']
    if 'id' not in pred or not set(columns)<=set(pred):
        raise ValueError('Validation CSV requires id and prediction columns '+str(columns))
    # Pixel IDs already have an unambiguous compound form; never strip them.
    kind = 'literal' if meta['metric']=='rmse_pixels' else meta['id_kind']
    pred['id'] = pred['id'].map(lambda x:canonical_id(x,kind))
    if pred['id'].duplicated().any() or len(pred)!=len(truth) or set(pred['id'])!=set(truth['id']):
        raise ValueError('Validation prediction IDs differ from the host-owned fixed split')
    pred = pred.set_index('id').loc[truth['id']]
    metric = meta['metric'];y = truth[meta['targets']].to_numpy()
    if metric=='text_accuracy':
        value = float(np.mean(y[:,0].astype(str)==pred[columns[0]].astype(str).to_numpy()))
    else:
        p = pred[columns].to_numpy(dtype=float)
        if not np.isfinite(p).all():raise ValueError('Non-finite validation predictions')
        if metric in ('auc','micro_auc','macro_auc','logloss','multiclass','rmse_pixels') and ((p<0).any() or (p>1).any()):
            raise ValueError('Probability/pixel predictions must be in [0,1]')
        if metric=='multiclass':
            if not np.allclose(p.sum(axis=1),1,atol=1e-5):raise ValueError('Class probabilities must sum to one')
            value = log_loss(y[:,0].astype(str),p,labels=columns)
        elif metric=='logloss':value = log_loss(y[:,0].astype(int),p[:,0],labels=[0,1])
        elif metric=='auc':value = roc_auc_score(y[:,0].astype(float),p[:,0])
        elif metric=='micro_auc':value = roc_auc_score(y.astype(float).ravel(),p.ravel())
        elif metric=='macro_auc':
            usable = [j for j in range(y.shape[1]) if len(np.unique(y[:,j]))==2]
            if not usable:raise ValueError('No validation column has both classes')
            value = float(np.mean([roc_auc_score(y[:,j].astype(float),p[:,j]) for j in usable]))
        elif metric=='accuracy':
            if not np.equal(p,np.floor(p)).all():raise ValueError('Class labels must be integers')
            value = accuracy_score(y[:,0].astype(int),p[:,0].astype(int))
        elif metric=='kappa':
            if not np.equal(p,np.floor(p)).all() or (p<0).any() or (p>4).any():raise ValueError('Diagnosis predictions must be integer labels 0..4')
            value = cohen_kappa_score(y[:,0].astype(int),p[:,0].astype(int),weights='quadratic',labels=list(range(5)))
        elif metric in ('rmse','rmse_pixels'):value = float(np.sqrt(np.mean((y.astype(float)-p)**2)))
        elif metric=='rmsle':
            if (p<0).any():raise ValueError('RMSLE predictions must be nonnegative')
            value = float(np.sqrt(np.mean((np.log1p(y.astype(float))-np.log1p(p))**2,axis=0)).mean())
        else:raise ValueError('Unknown independent metric '+metric)
    if not np.isfinite(value):raise ValueError('Independent score is non-finite')
    return {'validation_score':float(value),'independently_scored':True,'scored_predictions':len(truth),
            'n_validation':meta['n_validation'],'n_train':meta['n_train'],
            'manifest_sha256':file_hash(directory/'manifest.json'),'metric':metric,'submission_contract':submission_check}


def main():
    parser=argparse.ArgumentParser();sub=parser.add_subparsers(dest='command',required=True)
    p=sub.add_parser('prepare');p.add_argument('task');p.add_argument('public');p.add_argument('destination')
    p=sub.add_parser('score');p.add_argument('directory');p.add_argument('output');p.add_argument('result');p.add_argument('--public')
    args=parser.parse_args()
    if args.command=='prepare':prepare(args.task,args.public,args.destination)
    else:write_json(Path(args.result),score(args.directory,args.output,args.public))


if __name__=='__main__':main()
