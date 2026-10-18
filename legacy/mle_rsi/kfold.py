"""Candidate instructions for a fresh host-owned three-fold Cactus search."""
CONTRACT = '''
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
