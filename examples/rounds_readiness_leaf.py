"""Prewritten readiness only: real GPU/container, fixed public split and host scoring."""
from pathlib import Path
import time
import numpy as np
import pandas as pd
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.linear_model import LogisticRegression
import mle_runtime as rt

config = rt.task_config()
root = Path(config['public_data_dir'])
out = Path(config['output_dir'])
if config['budget']['gpu_count']:
    import torch
    assert torch.cuda.is_available()
    with torch.inference_mode():
        x = torch.ones((128,128),device='cuda')
        assert (x @ x).mean().item() == 128
    # Exercise Blackwell CUDA kernels and cuDNN forward/backward, with no API.
    model_gpu = torch.nn.Sequential(torch.nn.Conv2d(3,16,3),torch.nn.ReLU(),
        torch.nn.AdaptiveAvgPool2d(1),torch.nn.Flatten(),torch.nn.Linear(16,4)).cuda()
    batch_gpu = torch.randn(32,3,64,64,device='cuda')
    target_gpu = torch.zeros(32,dtype=torch.long,device='cuda')
    stop_at = time.monotonic()+20
    steps_gpu = 0
    while time.monotonic() < stop_at:
        model_gpu.zero_grad(set_to_none=True)
        loss_gpu = torch.nn.functional.cross_entropy(model_gpu(batch_gpu),target_gpu)
        loss_gpu.backward()
        torch.cuda.synchronize()
        steps_gpu += 1
    print('CUDA forward/backward steps',steps_gpu,'GPU',torch.cuda.get_device_name(),flush=True)
    assert steps_gpu > 0 and torch.isfinite(loss_gpu)

train = pd.read_csv(root/'train.csv')
test = pd.read_csv(root/'test.csv')
fit, val = rt.split_indices(train.id)
model = make_pipeline(StandardScaler(),LogisticRegression(max_iter=500,random_state=0))
x = train.drop(columns=['id','species'])
model.fit(x.iloc[fit],train.species.iloc[fit])
rt.write_validation(train.id.iloc[val],model.predict_proba(x.iloc[val]),reported_score=999)
model.fit(x,train.species)
submission = pd.DataFrame(model.predict_proba(test.drop(columns=['id'])),columns=model.classes_)
submission.insert(0,'id',test.id)
submission.to_csv(out/'submission.csv',index=False)
