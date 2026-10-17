"""Public-only candidate generation/evaluation; private grading is a separate command."""
import asyncio
import copy
import csv
import json
import math
import os
import subprocess
import sys
import time
from dataclasses import replace
from pathlib import Path

from . import REPO, WORKSPACE
from .common import Budget, LimitReached, read, sha, write
from dream_rsi.codec import digest
from dream_rsi.models import EvaluationReport, FailClass, TaskSpec
from dream_rsi.seed import make_artifact

DEV_TASKS = ['leaf-classification', 'spooky-author-identification',
             'denoising-dirty-documents', 'new-york-city-taxi-fare-prediction']


def task_ids():
    return (WORKSPACE / 'projects/mle-bench/experiments/splits/low.txt').read_text().split()


def metric_spec(task):
    path = WORKSPACE / 'projects/mle-bench/mlebench/competitions' / task / 'config.yaml'
    name = next(line.split('name:', 1)[1].strip() for line in path.read_text().split('grader:')[1].splitlines() if 'name:' in line)
    lower = any(s in name.lower() for s in ('loss', 'squared', 'rmsle'))
    scale = 10.0 if task == 'new-york-city-taxi-fare-prediction' else 1.0
    return {'name': name, 'higher_is_better': not lower, 'scale': scale,
            'definition': '1/(1+metric/scale)' if lower else ('(metric+1)/2' if 'kappa' in name else 'metric')}


def quality(value, metric):
    if type(value) not in (float, int) or not math.isfinite(value):
        raise ValueError('Validation metric must be a finite number')
    if not metric['higher_is_better']:
        if value < 0: raise ValueError('Loss cannot be negative')
        return 1 / (1 + value / metric['scale'])
    if 'kappa' in metric['name']:
        if not -1 <= value <= 1: raise ValueError('Invalid kappa')
        return (value + 1) / 2
    if not 0 <= value <= 1: raise ValueError('Expected metric in [0,1]')
    return float(value)


def preview(public):
    rows = []
    for p in sorted(public.iterdir())[:30]:
        if p.is_dir():
            with os.scandir(p) as it:
                names = []
                for i, item in enumerate(it):
                    if i == 5: break
                    names.append(item.name)
            rows.append(f'{p.name}/ examples: {names}')
        else:
            rows.append(f'{p.name}: {p.stat().st_size} bytes')
            if p.suffix == '.csv':
                with p.open(errors='replace') as f:
                    for _ in range(2): rows.append(f.readline(3000).strip())
    return '\n'.join(rows)


COMMON_CONTRACT = '''
Write a complete Python script executed as python /agent.py --task /task.json.
Read the JSON task: public_data_dir=/data (read-only), output_dir=/output (writable),
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
Each attempt starts in a fresh output directory; no checkpoints are inherited.
Do not start background processes. Finish all required output before the timeout.
'''


class MLEBackend:
    def __init__(self, task, root, spec, phase, *, mock=False):
        if task not in task_ids(): raise ValueError('Unknown Lite task')
        self.task_id, self.root, self.spec, self.phase, self.mock = task, Path(root), spec, phase, mock
        self.metric = metric_spec(task)
        self.public = WORKSPACE / 'data/mlebench' / task / 'prepared/public'
        self.fixed = bool(spec.get('first_batch_fixes')) and not mock
        self.prepared = Path(root)/'prepared'/task
        self.validation = read(self.prepared/'manifest.json') if self.fixed else None
        if self.fixed and self.validation.get('sanitized_public'):
            self.public=self.prepared/self.validation['sanitized_public']
        extra = ''
        if self.fixed:
            shown={k:v for k,v in self.validation.items() if k not in ('file_hashes','data_overrides')}
            extra='\nFIXED VALIDATION MANIFEST:\n'+json.dumps(shown)+'\nACTUAL RUNTIME INVENTORY:\n'+json.dumps(read(Path(root)/'runtime/inventory.json'))
        self.kfold = bool(self.validation and self.validation.get('validation_protocol') == 'stratified_kfold')
        self.contract = V2_CONTRACT if self.fixed else CONTRACT
        if self.kfold:
            from .kfold import CONTRACT as KFOLD_CONTRACT
            self.contract = COMMON_CONTRACT + KFOLD_CONTRACT
        self.task = TaskSpec(task_id=task, objective_text=(self.public/'description.md').read_text() + '\n' + preview(self.public)+extra,
                            candidate_contract=self.contract, benchmark_protocol_id=('mle-public-kfold-v1' if self.kfold else 'mle-public-v2') if self.fixed else 'mle-public-v1',
                            allowed_language='python', allowed_libraries=('installed-ML-environment',),
                            evaluation_criteria={'metric': self.metric, 'score': {'direction': 'higher_is_better',
                                'definition': self.metric['definition']},
                                'validation': 'host-owned fixed split; independently recomputed public metric' if self.fixed else 'candidate-reported public holdout; fixed IDs; not independently scored'})
        self.budget = Budget(root, spec['budget'])
        self.gate = asyncio.Lock()
        groups = spec['execution'].get('candidate_cpu_groups')
        self.cpu_slots = asyncio.Queue() if groups else None
        if groups:
            flat = [cpu for group in groups for cpu in group]
            if not all(groups) or len(flat) != len(set(flat)) or set(flat) != set(spec['execution']['cpu_ids']):
                raise ValueError('Candidate CPU groups must partition task CPUs without overlap')
            for group in groups:
                self.cpu_slots.put_nowait(tuple(group))

    def fingerprint(self):
        return {'benchmark':'mle', 'task':self.task_id, 'metric':self.metric, 'phase':self.phase,
                'description_sha256':sha(self.public/'description.md'), 'contract':digest(self.contract),
                'validation_manifest':sha(self.prepared/'manifest.json') if self.fixed else None,
                'execution':self.spec.get('continuation_execution_fingerprint', self.spec['execution']), 'mock':self.mock}

    def bind(self, store, protocol_hash):
        self.store, self.protocol_hash = store, protocol_hash
        store.put('metadata', 'task_backend', self.fingerprint())
        return self, self

    def sample(self, attempt_id):
        return {'attempt_id':attempt_id, 'protocol_hash':self.protocol_hash}

    def initial_artifact(self):
        return replace(make_artifact('Empty MLE workspace, not an evaluated solution',
                                     '# Implement the full MLE task contract.\n'), entrypoint='__main__')

    def bootstrap_report(self):
        # Neutral root sentinel, never eligible as a final submission.
        return EvaluationReport(False, True, 1e-12, FailClass.OK, self.protocol_hash,
                                diagnostics={'benchmark':'mle', 'bootstrap_placeholder':True,
                                             'score_definition':self.metric['definition']})

    async def evaluate(self, artifact, batch):
        if getattr(self, 'resource_pool', None) is not None:
            from .candidate_contract import resource_request
            try:
                gpu = self.task_id in self.spec['execution']['gpu_tasks'] if self.mock else resource_request(artifact.source, self.task_id, self.spec)
            except (ValueError, TypeError) as exc:
                return EvaluationReport(True,False,0.,FailClass.SOURCE,self.protocol_hash,error=str(exc),
                    diagnostics={'repairable':True,'preflight_failed':True})
            async with self.resource_pool.slot(self.task_id, gpu) as slot:
                worker = copy.copy(self)
                worker.spec = copy.deepcopy(self.spec)
                worker.spec['execution']['cpu_ids'] = list(slot['cpus'])
                worker.spec['execution']['global_pool'] = True
                worker.spec['execution']['leased_gpu'] = slot['gpu']
                worker.spec['execution']['gpu_lease_fd'] = slot['fd']
                worker.spec['execution']['candidate_gpu'] = gpu
                overrides = worker.spec['execution'].get('task_timeout_seconds', {})
                worker.spec['execution']['candidate_timeout_seconds'] = overrides.get(self.task_id, worker.spec['execution']['candidate_timeout_seconds'])
                return await worker._evaluate(artifact, batch)
        if self.cpu_slots is None:
            async with self.gate:
                return await self._evaluate(artifact,batch)
        cpus = await self.cpu_slots.get()
        try:
            # Per-attempt configuration; never mutate shared task state under concurrency.
            worker = copy.copy(self)
            worker.spec = copy.deepcopy(self.spec)
            worker.spec['execution']['cpu_ids'] = list(cpus)
            return await worker._evaluate(artifact,batch)
        finally:
            self.cpu_slots.put_nowait(cpus)

    async def _evaluate(self, artifact, batch):
        batch = batch() if callable(batch) else batch
        attempt = batch['attempt_id']
        from .common import request_namespace
        namespace = request_namespace(self.spec, self.task_id)
        directory = self.store.root / 'executions' / ((namespace+'-' if namespace else '') + attempt.replace(':','-'))
        saved = directory / 'assessment.json'
        if saved.exists():
            record = read(saved)
            if record['artifact_id'] != artifact.artifact_id: raise RuntimeError('Changed resumed candidate')
            return self._report(record)
        if self.mock:
            q = .35 + (int(artifact.source_hash[:5],16) % 500) / 1000
            record = dict(artifact_id=artifact.artifact_id, valid=True, score=q, fail_class='ok',
                          metric={'validation_metric':self.metric['name'], 'validation_score':q,
                                  'higher_is_better':self.metric['higher_is_better']}, diagnostics={'mock':True})
            write(saved, record)
            return self._report(record)
        if directory.exists(): raise LimitReached('Interrupted candidate; explicit reconciliation required')
        directory.mkdir(parents=True)
        (directory/'agent.py').write_text(artifact.source)
        if self.fixed:
            try:compile(artifact.source,'/agent.py','exec')
            except SyntaxError as exc:
                record=dict(artifact_id=artifact.artifact_id,valid=False,score=0.,fail_class='source_error',metric={},
                    error=f'SyntaxError at line {exc.lineno}: {exc.msg}; {exc.text or ""}',
                    diagnostics={'repairable':True,'preflight_failed':True,'output_dir':str(directory/'output')})
                # FailClass uses SOURCE's canonical wire value.
                record['fail_class']=FailClass.SOURCE.value
                write(saved,record);return self._report(record)
        task = {'competition_id':self.task_id,'task_id':self.task_id,'seed':0,
                'public_data_dir':'/data','output_dir':'/output',
                'validation_metric':self.metric['name'],'higher_is_better':self.metric['higher_is_better'],
                'budget':{'wall_seconds':self.spec['execution']['candidate_timeout_seconds'],
                          'cpu_cores':len(self.spec['execution']['cpu_ids']),'memory_gib':32}}
        if self.fixed:task.update(validation_manifest='/validation/manifest.json',runtime_inventory='/runtime/inventory.json',protocol_version=3 if self.kfold else 2)
        write(directory/'task.json', task)
        write(directory/'request.json', dict(task=self.task_id, root=str(self.root), phase=self.phase,
                                           directory=str(directory), spec=self.spec))
        # Worker owns its container; process completion includes forced container cleanup.
        process = await asyncio.create_subprocess_exec(sys.executable, '-m', 'mle_rsi.execute',
                    str(directory/'request.json'), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
                    pass_fds=(() if self.spec['execution'].get('gpu_lease_fd') is None else
                              (self.spec['execution']['gpu_lease_fd'],)))
        try:
            output, _ = await process.communicate()
        except asyncio.CancelledError:
            process.terminate()
            await asyncio.shield(process.wait())
            raise
        (directory/'worker.log').write_bytes(output)
        if process.returncode:
            if (directory/'worker-error.json').exists() and read(directory/'worker-error.json')['state']=='budget_exhausted':
                from .common import BudgetExhausted
                raise BudgetExhausted(read(directory/'worker-error.json')['reason'])
            raise LimitReached('Execution worker stopped; inspect '+str(directory/'worker.log'))
        resources = read(directory/'execution/resources.json')
        diagnostics = {'resources':resources, 'output_dir':str(directory/'output'),
                       'stdout_tail':(directory/'execution/stdout.log').read_text(errors='replace')[-12000:]}
        record = dict(artifact_id=artifact.artifact_id, valid=False, score=0., fail_class='runtime_error',
                      metric={}, diagnostics=diagnostics)
        try:
            out = directory/'output'
            bundle = out/'complete-bundle.json'
            recover = bool(resources['timed_out'] and self.spec.get('stage2',{}).get('timeout_bundles') and bundle.exists())
            if (resources['exit_code'] or resources['timed_out']) and not recover:
                record['fail_class'] = 'timeout' if resources['timed_out'] else 'runtime_error'
                detail=diagnostics['stdout_tail'][-5000:]
                message=f"Timeout after {resources['wall_seconds']:.1f}s; reduce work and reserve inference/output time" if resources['timed_out'] else f"Candidate exited with code {resources['exit_code']}"
                raise ValueError(message+'\n'+detail if self.fixed else 'Candidate timed out or exited unsuccessfully')
            if recover:
                pointer=read(bundle)
                name=pointer['directory']
                if Path(name).name!=name or not name.startswith('bundle-'):
                    raise ValueError('Unsafe completed bundle path')
                out=out/name
                if out.is_symlink():raise ValueError('Unsafe completed bundle')
                required={'submission.csv','validation_predictions.csv','candidate-result.json','validation-split.json','training-contract.json'}
                if set(pointer['hashes'])!=required or read(out/'seal.json')!=pointer['hashes']:
                    raise ValueError('Incomplete bundle seal')
                for name,expected in pointer['hashes'].items():
                    if (out/name).is_symlink() or sha(out/name)!=expected:raise ValueError('Changed completed bundle')
                diagnostics.update(output_dir=str(out), recovered_complete_bundle=True)
            if self.spec.get('stage2',{}).get('training_consistency'):
                from .candidate_contract import validate_training_contract
                diagnostics['training_contract']=validate_training_contract(read(out/'training-contract.json'))
            for name in ['submission.csv','candidate-result.json','validation-split.json','validation_predictions.csv']:
                p = out/name
                if p.is_symlink() or not p.is_file() or p.stat().st_size == 0:
                    raise ValueError('Missing/unsafe candidate artifact: '+name)
            metric = read(out/'candidate-result.json')
            if not self.fixed and (metric['validation_metric'] != self.metric['name'] or metric['higher_is_better'] is not self.metric['higher_is_better']):
                raise ValueError('Metric name/direction does not match task')
            if not self.fixed and (type(metric.get('n_validation')) is not int or metric['n_validation'] < 2):
                raise ValueError('Missing positive validation sample count')
            if self.fixed:
                result=directory/'independent-score.json'
                command=['taskset','-c',','.join(map(str,self.spec['execution']['cpu_ids'])),
                    str(Path(self.spec['execution']['python_environment'])/'bin/python'),str(REPO/'mle_rsi/validation.py'),
                    'score',str(self.prepared),str(out),str(result),'--public',str(self.public)]
                scorer=await asyncio.create_subprocess_exec(*command,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.STDOUT,
                    env={**os.environ,**{k:str(len(self.spec['execution']['cpu_ids'])) for k in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS')}})
                try:
                    scorer_output,_=await asyncio.wait_for(scorer.communicate(),timeout=300)
                except BaseException:
                    if scorer.returncode is None:scorer.kill();await scorer.wait()
                    raise
                (directory/'independent-score.log').write_bytes(scorer_output)
                if scorer.returncode:raise ValueError('Independent public validation failed:\n'+scorer_output.decode(errors='replace')[-5000:])
                independent=read(result);diagnostics['independent_validation']=independent
                diagnostics['candidate_reported_score']=metric.get('validation_score')
                diagnostics['candidate_reported_metric']=metric
                metric={'validation_metric':self.metric['name'],'higher_is_better':self.metric['higher_is_better'],
                    'validation_score':independent['validation_score'],'n_train':independent['n_train'],
                    'n_validation':independent['n_validation'],'score_source':'host-recomputed'}
                metric.update(independent.get('probability_diagnostics',{}))
                if independent.get('cross_validation'):
                    metric['validation_protocol'] = 'stratified_kfold_mean_auc'
                    metric['fold_auc'] = independent['cross_validation']['fold_auc']
                    metric['fold_auc_std'] = independent['cross_validation']['fold_auc_std']
                from .diversity import record_prediction_fingerprints
                matches=record_prediction_fingerprints(self.store,attempt,independent.get('prediction_fingerprints',{}))
                diagnostics['prediction_duplicate_of']=matches
                for kind, previous in matches.items():
                    metric[kind+'_predictions_duplicate_of']=previous
                score=quality(metric['validation_score'],self.metric)
                diagnostics.update(split_hash=independent['manifest_sha256'],submission_sha256=sha(out/'submission.csv'))
                audit=out/'runtime-audit.json'
                if audit.exists():diagnostics['runtime_audit']=read(audit)
                record.update(valid=True,score=score,fail_class='ok',metric=metric)
                write(saved,record);return self._report(record)
            ids = read(out/'validation-split.json')['validation_ids']
            if not isinstance(ids,list) or len(ids)!=metric['n_validation'] or len(set(map(str,ids)))!=len(ids):
                raise ValueError('Invalid validation identifiers')
            split = digest(sorted(map(str,ids)))
            previous = self.store.get('metadata','public_split_hash')
            if previous is not None and previous != split:
                raise ValueError('Validation split changed across candidates')
            score = quality(metric['validation_score'], self.metric)
            self.store.put('metadata','public_split_hash',split)
            record.update(valid=True, score=score, fail_class='ok', metric=metric)
            diagnostics['split_hash'] = split
            diagnostics['submission_sha256'] = sha(out/'submission.csv')
        except (ValueError, KeyError, TypeError, FileNotFoundError) as exc:
            record['error'] = str(exc)
            if not resources['exit_code'] and not resources['timed_out']: record['fail_class']='output_contract_error'
            if self.fixed:diagnostics['repairable']=True
        write(saved, record)
        return self._report(record)

    def _report(self, record):
        return EvaluationReport(True,record['valid'],record['score'],FailClass(record['fail_class']),self.protocol_hash,
                                error=record.get('error'), metrics=record['metric'],
                                diagnostics={'benchmark':'mle','score_definition':self.metric['definition'],
                                             **record['diagnostics']})
