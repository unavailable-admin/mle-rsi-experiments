"""The only MLE module assembling Dream-RSI and implementing its task interface."""
import json
from pathlib import Path
from dataclasses import replace
from dream_rsi.campaign import Campaign
from dream_rsi.config import CampaignConfig,ModelConfig,HistoryInputConfig
from dream_rsi.models import TaskSpec,EvaluationReport,FailClass
from dream_rsi.seed import make_artifact
from dream_rsi.policy import best_first_policy_source
from . import tasks,validation
from .io import read,write,sha,digest
from .execute import execute
from .process import run_accounted
from .recovery import execution as recover_execution
from .ledger import EvidenceConflict


class MLEBackend:
    def __init__(self,root,task,spec,ledger,pool):
        self.root,self.settings,self.spec,self.ledger,self.pool=Path(root),task,spec,ledger,pool
        self.task_id=task['id'];self.prepared=self.root/'prepared'/self.task_id
        self.mock=spec['mode']=='mock'
        manifest={'metric':'auc','task':self.task_id} if self.mock else read(self.prepared/'manifest.json')
        self.manifest=manifest;self.metric=tasks.metric(manifest)
        objective='Verification fixture' if self.mock else (Path(task['public_dir'])/'description.md').read_text()
        self.task=TaskSpec(task_id=self.task_id,objective_text=objective,
            candidate_contract='Fixture only' if self.mock else tasks.contract(manifest)+'\nVALIDATION MANIFEST:\n'+json.dumps({k:v for k,v in manifest.items() if k not in ('file_hashes','data_overrides')}),
            allowed_language='python',allowed_libraries=('installed-environment',),
            benchmark_protocol_id='mle-public-v1',evaluation_criteria={'metric':self.metric,'score_direction':'higher_is_better'})

    def fingerprint(self):
        return {'task':self.task_id,'validation':digest(self.manifest),'metric':self.metric,
                'contract':digest(self.task.candidate_contract),'description':digest(self.task.objective_text),
                'execution':self.settings,'mode':self.spec['mode']}

    def bind(self,store,protocol_hash):
        self.namespace=store.get("metadata","namespace")
        self.protocol_hash=protocol_hash
        return self,self

    def sample(self,attempt_id):
        return {'evaluation_id':f"{self.namespace}:{attempt_id}",'protocol_hash':self.protocol_hash}

    def initial_artifact(self):return replace(make_artifact('Unevaluated workspace','# implement task'),entrypoint='__main__')
    def bootstrap_report(self):return EvaluationReport(False,True,0.,FailClass.OK,self.protocol_hash)

    async def evaluate(self,artifact,context):
        context=context() if callable(context) else context
        ident='execution:'+context['evaluation_id']
        identity=digest({'source':artifact.source_hash,'context':context,'task':self.fingerprint()})
        directory=self.root/'evaluations'/digest(ident)
        saved=directory/'report.json'
        if saved.exists():
            row=read(saved)
            if row['input_hash']!=identity:raise EvidenceConflict('Evaluation input changed')
            recover_execution(directory,ident,identity,self.ledger) if not self.mock else None
            return self._report(row)
        if self.mock:
            row={'input_hash':identity,'valid':True,'score':.5,'metrics':{'raw_metric':.5,'verification_only':True},'diagnostics':{}}
            write(saved,row);return self._report(row)
        try:
            gpu=tasks.request_gpu(artifact.source,self.settings['gpu'])
            if gpu and not self.spec['host'].get('gpus'):
                raise ValueError('Candidate requests GPU but this host has no configured GPU')
        except (ValueError,SyntaxError) as exc:
            row={'input_hash':identity,'valid':False,'score':0.,'metrics':{},'diagnostics':{},'error':str(exc),'fail_class':'source_error'}
            write(saved,row);return self._report(row)
        reserve={'execution_seconds':self.settings['timeout_seconds']+30,'gpu_seconds':self.settings['timeout_seconds']+30 if gpu else 0}
        recovered=recover_execution(directory,ident,identity,self.ledger)
        if recovered is None:self.ledger.check(self.task_id,reserve,exclude=ident)
        cores=self.settings.get('gpu_cores',self.settings['cores']) if gpu else self.settings['cores']
        async with self.pool.slot(self.task_id,cores,gpu if recovered is None else False) as slot:
            directory.mkdir(parents=True,exist_ok=True)
            (directory/'agent.py').write_text(artifact.source)
            write(directory/'task.json',{'competition_id':self.task_id,'seed':0,'data_dir':'/data','output_dir':'/output',
                'validation_manifest':'/validation/manifest.json','validation_metric':self.metric['name'],
                'higher_is_better':self.metric['higher_is_better'],'runtime_inventory':'/runtime/inventory.json',
                'budget':{'wall_seconds':self.settings['timeout_seconds'],'cpu_count':len(slot['cpus']),'gpu_count':int(slot['gpu'] is not None)}})
            resources=recovered or await execute(self.root,directory,ident,identity,self.settings,self.spec['host'],slot,self.ledger)
            diagnostics={'resources':resources,'output_dir':str(directory/'output')}
            log=directory/'execution/stdout.log'
            if log.exists():
                with log.open('rb') as stream:
                    stream.seek(max(0,log.stat().st_size-12000))
                    diagnostics['stdout_tail']=stream.read().decode(errors='replace')
            row={'input_hash':identity,'valid':False,'score':0.,'metrics':{},'diagnostics':diagnostics}
            try:
                if resources['exit_code'] or resources['timed_out']:
                    row['fail_class']='timeout' if resources['timed_out'] else 'runtime_error'
                    raise ValueError('Candidate execution failed: '+diagnostics.get('stdout_tail','')[-6000:])
                output=directory/'output'
                for name in ('submission.csv','validation_predictions.csv','training-contract.json','candidate-result.json','validation-split.json'):
                    p=output/name
                    if p.is_symlink() or not p.is_file() or not p.stat().st_size:raise ValueError('Missing/unsafe artifact: '+name)
                tasks.verify_training(output)
                score_file=directory/'public-score.json'
                result=await run_accounted([str(Path(self.spec['host']['python_environment'])/'bin/python'),
                    str(Path(validation.__file__)),'score',str(self.prepared),str(output),str(score_file),
                    '--public',self.settings['public_dir']],directory/'scoring',300,slot['cpus'],
                    self.ledger,'score:'+context['evaluation_id'],self.task_id,identity)
                if result['exit_code'] or result['timed_out']:
                    with (directory/'scoring/stdout.log').open('rb') as stream:
                        stream.seek(max(0,(directory/'scoring/stdout.log').stat().st_size-6000))
                        detail=stream.read().decode(errors='replace')
                    raise ValueError('Host validation failed: '+detail)
                score=read(score_file)
                raw=score['validation_score']
                row.update(valid=True,score=tasks.search_score(raw,self.metric),
                    metrics={'raw_metric':raw,**self.metric,'validation_protocol':digest(self.manifest)},
                    fail_class='ok')
                diagnostics.update(public_score=score,submission_sha256=sha(output/'submission.csv'))
                # Fingerprints are diagnostics, never a search rejection gate.
                fingerprints=score.get('prediction_fingerprints',{})
                duplicates={}
                for earlier in (self.root/'evaluations').glob('*/report.json'):
                    other=read(earlier)
                    if other.get('metrics',{}).get('validation_protocol')!=digest(self.manifest):continue
                    for kind,value in fingerprints.items():
                        if other.get('diagnostics',{}).get('public_score',{}).get('prediction_fingerprints',{}).get(kind)==value:
                            duplicates.setdefault(kind,[]).append(str(earlier.parent))
                diagnostics['prediction_duplicate_of']=duplicates
            except (ValueError,KeyError,TypeError,FileNotFoundError) as exc:
                row['error']=str(exc);row.setdefault('fail_class','output_contract_error')
            write(saved,row)
            return self._report(row)

    def _report(self,row):
        return EvaluationReport(True,row['valid'],row['score'],FailClass(row.get('fail_class','ok')),self.protocol_hash,
                                error=row.get('error'),metrics=row['metrics'],diagnostics=row['diagnostics'])


def assemble(root,task,spec,ledger,pool,transport=None,control=None,progress=None):
    from .config import OFFLINE
    search={**OFFLINE[spec.get('offline_tier','baseline')],**spec.get('search',{})}
    model=ModelConfig(**spec.get('model',{}))
    config=CampaignConfig(output_dir=str(Path(root)/'campaigns'),campaign_id=task['id'],outer_rounds=spec['rounds'],
        W=spec['batch_size'],K1=spec['batches'],K2=spec['batches'],
        max_response_attempts=1,round_discovery_calls=spec['batches']*spec['batch_size'],
        round_discovery_budget_unit='nodes',fixed_batch_size=True,exploration_protocol='branching-workspaces-v3',
        offline_from_original_root=True,offline_latest_prefix_only=True,
        replay_node_budget=spec['batches']*spec['batch_size'],
        discovery_model=model,developer_model=model,history_input=HistoryInputConfig(mode='evidence'),**search)
    backend=MLEBackend(root,task,spec,ledger,pool)
    def discovery(messages,ident):return json.dumps({'proposal':'Mock fixture','source':"MLE_RESOURCE='cpu'\\nprint('fixture')"})
    def developer(messages,ident):return json.dumps({'source':best_first_policy_source(tunable=config.policy_tuning_trials>1),'change_note':'Fixture'})
    return Campaign(config,task_backend=backend,transport=transport,control=control,progress=progress,
        discovery_responder=discovery if backend.mock else None,developer_responder=developer if backend.mock else None)
