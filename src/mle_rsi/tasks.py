"""Task contracts, immutable public protocols and metric direction."""
import json
from pathlib import Path
from .io import sha,write,read,digest
from . import validation

COMMON_CONTRACT = '''
Write a complete Python script executed as python /agent.py --task /task.json.
Read the JSON task: data_dir=/data (read-only), output_dir=/output (writable),
seed, competition_id, validation_metric, higher_is_better, and budget.
No network or private labels. Inspect public files at runtime; do not invent columns.
Installed environment includes numpy, pandas, scipy, sklearn, torch, torchvision,
timm, transformers, lightgbm, xgboost, catboost, Pillow, OpenCV and librosa.
Torch weights, when present, are at /models/torch/hub/checkpoints; other local model
directories are at /models/pretrained. Never download. LightGBM is CPU-only.

'''

CONTRACT = COMMON_CONTRACT + '''Use a reproducible 80/20 PUBLIC training/validation split, seed=0, stratification
where feasible, grouping by patient/image/sentence when observations share an entity.
Preserve the split across improvements, fit preprocessing only on training data.
Save validation-split.json with validation_ids (a list of stable row or image IDs),
and validation_predictions.csv containing held-out labels and predictions.
Never split pixels of the same image across training and validation.
You may deterministically subsample TRAINING data for the execution budget; record it.
Compute the specified metric on the public validation split. Emit candidate-result.json:
{"validation_metric": "EXACT task metric name", "validation_score": 0.1,
 "higher_is_better": false, "n_train": 100, "n_validation": 25}.
The score is agent-reported; be honest, never substitute training or test scores.
Write submission.csv for ALL test inputs in the required official format, containing
real predictions. Keep it and every required artifact as ordinary files in /output.
Refit on all public training data only after measuring held-out validation quality.
Record the actual fitted validation and final model settings using this API:
rt.record_training_contract([
    {'name': 'model', 'validation': settings, 'final': settings}
])
For an ensemble, record each fitted member. Both settings dictionaries must be
nonempty and describe the same training procedure; do not fabricate fit records.
The helper writes training-contract.json with a nonempty members list. There is
no rt.record_training() API. After saving submission.csv and validation outputs,
call rt.commit_outputs(). Required files are submission.csv,
validation_predictions.csv, validation-split.json, candidate-result.json and
training-contract.json.
Each attempt starts in a fresh output directory; no checkpoints are inherited.
Do not start background processes. Finish all required output before the timeout.
'''

FIXED_CONTRACT = '''
This run uses host-owned validation protocol v2, not a candidate-defined split.
The task JSON budget is an OBJECT: {"wall_seconds": 900, "cpu_cores": 8,
"memory_gib": 32, "gpu_count": 0 or 1}. Read task['budget']['wall_seconds'];
never float(task['budget']). The actual timeout may be lower than 900 seconds.

import mle_runtime as rt
meta = rt.manifest()   # /validation/manifest.json, read-only
split = rt.split_table()  # id,row_index,split; text tasks use rt.validation_mask(ids)
train_idx, val_idx = rt.split_indices(ids)  # ids refer to rows in YOUR loaded order
Use only the fixed training IDs to fit preprocessing, models and inner tuning.
For the held-out model, validation IDs must never enter training or calibration.
Use rt.inner_split()
on OUTER TRAINING IDs for threshold/temperature tuning, and report the untouched
outer validation metric. Training subsampling is permitted; validation subsampling
or choosing a different seed is not. For Taxi read meta['public_row_limit'] rows
for outer validation training; final refit may use additional public rows.
Text normalization is grouped by sentence_id: sentence_id % 5 == 0 is validation.
Build IDs as sentence_id.astype(str) + "_" + token_id.astype(str); training data
has no pre-existing id column. Preserve before/after text exactly when reading CSV.
Denoising uses IMAGE-level split IDs; emit every pixel of the validation images
as imageStem_row_column (1-based row/column), intensity divided by 255.
Never guess the validation format: meta['prediction_columns'] defines exact columns
and their order; multiclass probabilities must align with those class names.
rt.write_validation(ids, predictions, reported_score=optional_score, model='...')
writes all validation artifacts. The HOST independently recomputes the score from
its own public labels; candidate-supplied labels and scores cannot select a winner.
Write a separate submission.csv matching the PUBLIC SAMPLE submission schema and
all test IDs. The host checks its columns, IDs, row count and prediction domain
before accepting a candidate; public validation alone is insufficient. For birds,
the official sample has Id,Probability (one row per recording/species pair), not
rec_id,species,probability. Inspect the sample to construct its IDs correctly.
For Birds, an empty known label list means a valid all-zero species row. Keep it.
Only '?' denotes unknown labels. Never discard recordings because their label
list is empty. Parse variable-length label CSV rows with csv.reader, not a
fixed-width pandas parser. Preserve every fixed validation ID and column order.
For probability metrics, inspect public accuracy, log loss and confidence
together. Fit any calibration only on an inner split of outer training data.

Use only features available at prediction time. Removed post-outcome fields are
not accessible in sanitized public inputs. Do not recover them from IDs or targets.
Do not add training/test labels as features or fit on outer validation labels.
The runtime inventory below lists actual installed versions and local weights.
Use `model, transform = rt.load_resnet50()` for the available local ResNet50.
It returns exactly TWO values; get the device with `next(model.parameters()).device`.
It raises on missing/incompatible weights. Replace its fc for feature extraction
or task-specific fine-tuning. No silent fallback: rt.report_model(name, device=...,
checkpoint=..., fallback_reason=...) must record the actual approach and fallback.
Use rt.progress(stage, **details), for example rt.progress("fit", rows=len(train)),
around expensive work. Only stage is positional; all details are keyword arguments.
Use rt.remaining_seconds(reserve=45) to reserve time for full test prediction and writing outputs. Validate shapes on a
small batch before full training. Syntax, imports, labels and budget errors should
be corrected before costly fitting. Use AMP APIs supported by the recorded PyTorch version.
The helper source is at /runtime/mle_runtime.py. Do not modify the helper or split.
'''


V2_CONTRACT = COMMON_CONTRACT + FIXED_CONTRACT + '''
After saving held-out validation predictions, you may refit on all public training
rows for final test predictions only. Do not overwrite held-out predictions with
predictions from this refitted model. Keep all required outputs as ordinary files.
Record the actual fitted validation and final model settings using this API:
rt.record_training_contract([
    {'name': 'model', 'validation': settings, 'final': settings}
])
For an ensemble, record each fitted member. Both settings dictionaries must be
nonempty and describe the same training procedure; do not fabricate fit records.
The helper writes training-contract.json with a nonempty members list. There is
no rt.record_training() API. After saving submission.csv and validation outputs,
call rt.commit_outputs(). Required files are submission.csv,
validation_predictions.csv, validation-split.json, candidate-result.json and
training-contract.json.
Each attempt starts in a fresh output directory; no checkpoints are inherited.
Do not start background processes. Finish all required output before the timeout.
'''



"""Candidate instructions for a fresh host-owned three-fold Cactus search."""
KFOLD_CONTRACT = '''
This task uses host-owned fixed 3-fold stratified cross-validation, not an 80/20
holdout. All candidates use the same read-only /validation/split.csv containing
id,row_index,fold. Fold IDs are 0,1,2. Never change folds, drop validation rows,
or select a fold using its score. The primary score is the arithmetic MEAN of
three independently recomputed fold ROC AUCs; pooled OOF AUC is diagnostic only.

import mle_runtime as rt
meta = rt.manifest()
fold_ids = rt.fold_assignments(ids)  # aligned to YOUR loaded public training IDs
for fold in range(3):
    train_idx, val_idx = rt.split_indices(ids, fold=fold)
    # Initialize a fresh model. Fit on train_idx only, predict val_idx only.
    # Fit preprocessing, learned features, calibration and tuning on these
    # training rows only. rt.inner_split(train_ids, labels, fold=fold) is safe.
    # Store predictions in an OOF array in the original loaded ID order.
# rt.write_validation(ids, oof_predictions) requires ALL public training IDs once;
# it records each sample's host-owned fold automatically. Never overwrite OOF
# predictions with fitted-model predictions or average predictions from models
# that trained on that sample. Fixed pretrained feature extraction may be cached
# only when entirely independent of labels, fitted statistics and fine-tuning.

Use the same model family, preprocessing, hyperparameters, seeds, ensemble
members and training schedule for all three folds and the final model. After
saving OOF predictions, fit a NEW model on ALL public training rows and generate
submission.csv for every public test ID, following the public sample schema.
Do not use a fold-model ensemble as a substitute for the final full-data refit.
Reserve time for this fourth fit, test inference and output before training.
The original per-candidate time and cumulative budgets still apply. Choose a
smaller consistent configuration if needed, rather than silently shrinking the
final model. All four fits must finish for the candidate to be valid.

Record actual fitted settings and memberships:
rt.record_kfold_training_contract(
    members=[{'name':'model', 'validation':settings, 'folds':[settings,settings,settings], 'final':settings}],
    fold_training=[{'fold':f, 'train_ids':ids[fold_ids != f], 'validation_ids':ids[fold_ids == f]} for f in range(3)],
    final_ids=ids)
These records support audit; do not claim to have trained a model you did not fit.
rt.commit_outputs() can atomically publish complete outputs after final training.
rt.progress(stage, **details), rt.remaining_seconds(reserve=45),
rt.load_resnet50() and rt.report_model(...) are available as documented in
/runtime/mle_runtime.py. rt.load_resnet50 returns (model, transform), two values.

The host reads its own public labels, verifies all fold IDs and coverage,
recomputes each fold score, and checks recorded training configuration consistency.
Candidate-reported scores/labels cannot select a winner. Use no private labels,
network downloads, or background processes. Every attempt is a fresh workspace.
'''


def contract_for(manifest):
    if manifest.get('aggregation') != 'pooled_oof_kappa':
        return KFOLD_CONTRACT
    text = KFOLD_CONTRACT.replace(
        'The primary score is the arithmetic MEAN of\nthree independently recomputed fold ROC AUCs; pooled OOF AUC is diagnostic only.',
        'The primary score is quadratic weighted Kappa computed ONCE over ALL\npooled out-of-fold predictions. Fold Kappas, their mean and standard deviation\nare diagnostics only; never optimize a different aggregation.')
    text = text.replace('id,row_index,fold.', 'id,row_index,group,fold. Exact duplicate images and any supplied patient\ngroups are confined to one fold; do not repartition groups.')
    text = text.replace('The original per-candidate time and cumulative budgets still apply.',
        'The host task budget.wall_seconds is the TOTAL allowance for the three\nfold fits, full-data refit and output. It is sized separately from the previous\nsingle-holdout candidate limit; cumulative compute/API caps are not increased.')
    text = text.replace('rt.progress(stage, **details), rt.remaining_seconds(reserve=45),\nrt.load_resnet50() and rt.report_model(...) are available as documented in\n/runtime/mle_runtime.py. rt.load_resnet50 returns (model, transform), two values.',
        'rt.progress(stage, **details), rt.remaining_seconds(reserve=45) and\nrt.report_model(...) are documented in /runtime/mle_runtime.py.')
    text += '''
Return integer diagnosis predictions 0..4 for each OOF and test image. If your
method learns rounding thresholds or calibration, fit them only within each
outer training fold (using inner group-isolated splits). Do not fit thresholds
on pooled OOF labels, then score on those same labels. The full-data refit uses
the same predefined training/calibration procedure on all public training rows.
Model family, features, losses and prediction construction are yours to discover.
Cross-validation does not prescribe a model family or require an ensemble;
any searched ensemble must obey the same OOF isolation and full-data refit rules.
Do not compare old holdout scores with this protocol or import their ranking.
'''
    return text


def prepare(root, task):
    directory=Path(root)/'prepared'/task['id']
    public=Path(task['public_dir'])
    if task.get('validation')=='kfold':
        fn=validation.prepare_aptos_kfold if task['id']=='aptos2019-blindness-detection' else validation.prepare_cactus_kfold
        fn(public,directory)
    else:
        validation.prepare(task['id'],public,directory)
    validation.verify_prepared(directory)
    return directory


def metric(manifest):
    name=manifest['metric']
    lower=name in ('multiclass','logloss','rmsle','rmse','rmse_pixels')
    return {'name':name,'higher_is_better':not lower,
            'scale':10. if manifest['task']=='new-york-city-taxi-fare-prediction' else 1.,
            'definition':'1/(1+raw/scale)' if lower else '(raw+1)/2' if name=='kappa' else 'raw'}


def search_score(raw, spec):
    import math
    if not math.isfinite(raw):raise ValueError('Nonfinite metric')
    if not spec['higher_is_better']:
        if raw<0:raise ValueError('Negative loss')
        return 1/(1+raw/spec['scale'])
    if spec['name']=='kappa':
        if not -1<=raw<=1:raise ValueError('Invalid kappa')
        return (raw+1)/2
    if not 0<=raw<=1:raise ValueError('Invalid quality score')
    return raw


def contract(manifest):
    body=COMMON_CONTRACT+contract_for(manifest) if manifest.get('validation_protocol')=='stratified_kfold' else V2_CONTRACT
    return body+"\nDeclare MLE_RESOURCE = 'cpu' or 'gpu'. Record validation/final training settings using the runtime training-contract API; all required fits must be complete."


def request_gpu(source, default):
    import ast
    tree=ast.parse(source)
    values=[n.value for n in tree.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='MLE_RESOURCE' for t in n.targets)]
    if len(values)!=1:raise ValueError('One literal MLE_RESOURCE declaration required')
    value=ast.literal_eval(values[0])
    if value not in ('cpu','gpu'):raise ValueError('Invalid resource declaration')
    return value=='gpu'


def verify_training(output):
    value=read(Path(output)/'training-contract.json')
    if not value.get('members'):raise ValueError('Missing training members')
    for member in value['members']:
        if not member.get('validation') or member['validation']!=member.get('final'):
            raise ValueError('Validation/final training configurations differ')
    return value
