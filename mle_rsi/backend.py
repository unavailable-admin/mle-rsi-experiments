"""Public-only candidate generation/evaluation; private grading is a separate command."""
import asyncio
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


CONTRACT = '''
Write a complete Python script executed as python /agent.py --task /task.json.
Read the JSON task: public_data_dir=/data (read-only), output_dir=/output (writable),
seed, competition_id, validation_metric, higher_is_better, and budget.
No network or private labels. Inspect public files at runtime; do not invent columns.
Installed environment includes numpy, pandas, scipy, sklearn, torch, torchvision,
timm, transformers, lightgbm, xgboost, catboost, Pillow, OpenCV and librosa.
Torch weights, when present, are at /models/torch/hub/checkpoints; other local model
directories are at /models/pretrained. Never download. LightGBM is CPU-only.

Use a reproducible 80/20 PUBLIC training/validation split, seed=0, stratification
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


class MLEBackend:
    def __init__(self, task, root, spec, phase, *, mock=False):
        if task not in task_ids(): raise ValueError('Unknown Lite task')
        self.task_id, self.root, self.spec, self.phase, self.mock = task, Path(root), spec, phase, mock
        self.metric = metric_spec(task)
        self.public = WORKSPACE / 'data/mlebench' / task / 'prepared/public'
        self.task = TaskSpec(task_id=task, objective_text=(self.public/'description.md').read_text() + '\n' + preview(self.public),
                            candidate_contract=CONTRACT, benchmark_protocol_id='mle-public-v1',
                            allowed_language='python', allowed_libraries=('installed-ML-environment',),
                            evaluation_criteria={'metric': self.metric, 'score': {'direction': 'higher_is_better',
                                'definition': self.metric['definition']},
                                'validation': 'candidate-reported public holdout; fixed IDs; not independently scored'})
        self.budget = Budget(root, spec['budget'])
        self.gate = asyncio.Lock()

    def fingerprint(self):
        return {'benchmark':'mle', 'task':self.task_id, 'metric':self.metric, 'phase':self.phase,
                'description_sha256':sha(self.public/'description.md'), 'contract':digest(CONTRACT),
                'execution':self.spec['execution'], 'mock':self.mock}

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
        async with self.gate:
            return await self._evaluate(artifact,batch)

    async def _evaluate(self, artifact, batch):
        batch = batch() if callable(batch) else batch
        attempt = batch['attempt_id']
        directory = self.store.root / 'executions' / attempt.replace(':','-')
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
        task = {'competition_id':self.task_id,'task_id':self.task_id,'seed':0,
                'public_data_dir':'/data','output_dir':'/output',
                'validation_metric':self.metric['name'],'higher_is_better':self.metric['higher_is_better'],
                'budget':{'wall_seconds':self.spec['execution']['candidate_timeout_seconds'],
                          'cpu_cores':8,'memory_gib':32}}
        write(directory/'task.json', task)
        write(directory/'request.json', dict(task=self.task_id, root=str(self.root), phase=self.phase,
                                           directory=str(directory), spec=self.spec))
        # Worker owns its container; process completion includes forced container cleanup.
        process = await asyncio.create_subprocess_exec(sys.executable, '-m', 'mle_rsi.execute',
                    str(directory/'request.json'), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        try:
            output, _ = await process.communicate()
        except asyncio.CancelledError:
            process.terminate()
            await asyncio.shield(process.wait())
            raise
        (directory/'worker.log').write_bytes(output)
        if process.returncode:
            raise LimitReached('Execution worker stopped; inspect '+str(directory/'worker.log'))
        resources = read(directory/'execution/resources.json')
        diagnostics = {'resources':resources, 'output_dir':str(directory/'output'),
                       'stdout_tail':(directory/'execution/stdout.log').read_text(errors='replace')[-12000:]}
        record = dict(artifact_id=artifact.artifact_id, valid=False, score=0., fail_class='runtime_error',
                      metric={}, diagnostics=diagnostics)
        try:
            if resources['exit_code'] or resources['timed_out']:
                record['fail_class'] = 'timeout' if resources['timed_out'] else 'runtime_error'
                raise ValueError('Candidate timed out or exited unsuccessfully')
            out = directory/'output'
            for name in ['submission.csv','candidate-result.json','validation-split.json','validation_predictions.csv']:
                p = out/name
                if p.is_symlink() or not p.is_file() or p.stat().st_size == 0:
                    raise ValueError('Missing/unsafe candidate artifact: '+name)
            metric = read(out/'candidate-result.json')
            if metric['validation_metric'] != self.metric['name'] or metric['higher_is_better'] is not self.metric['higher_is_better']:
                raise ValueError('Metric name/direction does not match task')
            if type(metric.get('n_validation')) is not int or metric['n_validation'] < 2:
                raise ValueError('Missing positive validation sample count')
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
        except (ValueError, KeyError, TypeError) as exc:
            record['error'] = str(exc)
            if not resources['exit_code'] and not resources['timed_out']: record['fail_class']='output_contract_error'
        write(saved, record)
        return self._report(record)

    def _report(self, record):
        return EvaluationReport(True,record['valid'],record['score'],FailClass(record['fail_class']),self.protocol_hash,
                                error=record.get('error'), metrics=record['metric'],
                                diagnostics={'benchmark':'mle','score_definition':self.metric['definition'],
                                             **record['diagnostics']})
