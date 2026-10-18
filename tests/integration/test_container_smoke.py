"""Opt-in tiny CPU fixture, using an existing image; no model network or private data."""
import asyncio
import csv
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import httpx
import pytest
from mle_rsi import runtime,validation
from mle_rsi.backend import assemble
from mle_rsi.config import validate
from mle_rsi.execute import readable
from mle_rsi.io import write,read
from mle_rsi.ledger import Ledger
from mle_rsi.provider import ModelService
from mle_rsi.resources import ResourcePool

SOURCE='''MLE_RESOURCE = "cpu"
import pandas as pd
import numpy as np
import mle_runtime as rt
train=pd.read_csv('/data/train.csv')
test=pd.read_csv('/data/test.csv')
tr,va=rt.split_indices(train.id.astype(str))
prior=float(train.iloc[tr].target.mean())
rt.write_validation(train.iloc[va].id.astype(str),np.full(len(va),prior))
final_prior=float(train.target.mean())
pd.DataFrame({'id':test.id,'target':final_prior}).to_csv('/output/submission.csv',index=False)
settings={'model':'mean-label','seed':0}
rt.record_training_contract([{'name':'mean','validation':settings,'final':settings}])
rt.commit_outputs()
'''


@pytest.mark.skipif(os.environ.get('MLE_PLATFORM_CONTAINER_SMOKE')!='1',reason='Explicit local container fixture only')
@pytest.mark.asyncio
async def test_generated_candidate_container_public_score_and_recovery(tmp_path,monkeypatch):
    # Use a durable path visible to the host daemon (pytest /tmp may be namespaced).
    root=Path(os.environ['MLE_PLATFORM_SMOKE_ROOT']);root.mkdir(parents=True,exist_ok=False)
    public=root/'public';public.mkdir()
    (public/'description.md').write_text('Binary mean-label fixture; no benchmark result claimed.')
    with (public/'train.csv').open('w') as f:
        writer=csv.writer(f);writer.writerow(['id','f_0','target']);writer.writerows((i,i%3,i%2) for i in range(40))
    (public/'test.csv').write_text('id,f_0\n100,1\n101,2\n')
    (public/'sample_submission.csv').write_text('id,target\n100,0\n101,0\n')
    task='tabular-playground-series-may-2022'
    mlpython='/opt/mle-rsi-fixes/env/bin/python'
    subprocess.run([mlpython,str(Path(validation.__file__)),'prepare',task,str(public),str(root/'prepared'/task)],check=True)
    (root/'runtime').mkdir();shutil.copyfile(runtime.__file__,root/'runtime/mle_runtime.py');write(root/'runtime/inventory.json',{})
    for directory in ('public','runtime','prepared'):
        readable(root/directory)
        for p in (root/directory).rglob('*'):readable(p)
    cpu=int(os.environ.get('MLE_PLATFORM_SMOKE_CPU','0'))
    spec=validate({'schema':1,'mode':'real','tasks':[{'id':task,'public_dir':str(public),'cores':1,'timeout_seconds':30}],
        'host':{'cpus':[cpu],'gpus':[],'python_environment':'/opt/mle-rsi-fixes/env',
                'python_extra_mounts':['/mnt/afs/yangsen1/tools/python'],
                'image':'sha256:859eb2ba86a41e3a360e9d726a183147a20faa990bfd5f7f3e4f4372b375f00f',
                'container_output_uid':100000,'container_output_gid':100000,
                'grading_python':mlpython,'data_dir':str(root)},
        'rounds':1,'batches':1,'batch_size':1,'search':{'M':1,'replay_scenarios':1,'scheduling_betas':[.5],'policy_decision_timeout_s':5},
        'model':{'provider':'openai-compatible','base_url':'https://fixture.invalid','model':'fixture',
                 'api_key_env':'FIXTURE_KEY','context_tokens':128000,'max_output_tokens':1024},
        'provider':{'reservation_rates':{'input_usd_per_million':1,'output_usd_per_million':1}},
        'budget':{'total':{'api_usd':1,'execution_seconds':500,'gpu_seconds':0}}})
    monkeypatch.setenv('FIXTURE_KEY','fixture-only')
    requests=[]
    def handle(request):
        requests.append(request.method)
        if request.method=='GET':return httpx.Response(200,json={'amount':{'currency':'USD','value':.001}})
        return httpx.Response(200,headers={'x-request-id':'fixture'},json={'model':'fixture','usage':{'total_tokens':100},
            'choices':[{'finish_reason':'stop','message':{'content':json.dumps({'proposal':'Fixture','source':SOURCE})}}]})
    wire=httpx.MockTransport(handle)
    ledger=Ledger(root,spec['budget']);pool=ResourcePool(spec['host'])
    service=ModelService(root,task,ledger,{**spec['provider'],'model':spec['model']},asyncio.Semaphore(1),
        client_factory=lambda **kw:httpx.AsyncClient(transport=wire,**kw))
    campaign=assemble(root,spec['tasks'][0],spec,ledger,pool,service)
    try:
        await campaign.run_round(1)
        result=campaign.best_result()
        reports=list((root/'evaluations').glob('*/report.json'))
        assert result is not None, [read(p) for p in reports]
        assert result['raw_metrics']['raw_metric']==.5
        assert result['report']['diagnostics']['resources']['container_id']
        counts=ledger.summary()
        attempt=campaign.store.list('attempt')[0][1]
        artifact=campaign.store.get('artifact',attempt.artifact_ref)
        again=await campaign.task_backend.evaluate(artifact,campaign.task_backend.sample(attempt.attempt_id))
        assert again.valid and ledger.summary()==counts and requests==['POST','GET']
        write(root/'verification.json',{'passed':True,'fixture_only':True,'candidate_calls':1,
              'raw_metric':.5,'ledger':counts,'container_id':result['report']['diagnostics']['resources']['container_id']})
    finally:campaign.close();ledger.close()
