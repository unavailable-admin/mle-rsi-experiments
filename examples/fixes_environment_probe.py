"""No API probe: exercise exactly the runtime and mounts used by real candidates."""
from pathlib import Path
import importlib
import json
import torch
import mle_runtime as rt

for module in ['numpy','pandas','scipy','sklearn','torchvision','timm','transformers','tokenizers',
               'lightgbm','xgboost','catboost','PIL','cv2','librosa','skimage','soundfile','sentencepiece']:
    importlib.import_module(module)
from transformers import AutoTokenizer, AutoModel
from tokenizers import Tokenizer, models
assert Tokenizer(models.WordLevel({'[UNK]':0},unk_token='[UNK]')).encode('test').ids==[0]
# Test both CPU and CUDA weight loads/forward passes inside namespaced isolation.
model,_=rt.load_resnet50(device='cpu');model.eval()
with torch.inference_mode():
    assert torch.isfinite(model(torch.zeros(1,3,224,224))).all()
if rt.task_config()['budget']['gpu_count']:
    assert torch.cuda.is_available()
    model=model.cuda()
    with torch.inference_mode():
        assert torch.isfinite(model(torch.zeros(1,3,224,224,device='cuda'))).all()
    model.train();model.zero_grad(set_to_none=True)
    loss=model(torch.randn(2,3,64,64,device='cuda')).square().mean();loss.backward()
    torch.cuda.synchronize()
    assert torch.isfinite(loss)
    print('GPU',torch.cuda.get_device_name(),flush=True)
# Every advertised model file must be readable, not merely visible to host root.
files=0
for path in Path('/models').rglob('*'):
    if path.is_file():
        with path.open('rb') as f:f.read(1)
        files+=1
assert files>0
for directory in sorted(Path('/models/pretrained').glob('*/*')):
    if (directory/'tokenizer_config.json').exists():
        tokenizer=AutoTokenizer.from_pretrained(str(directory),local_files_only=True,trust_remote_code=False)
        assert tokenizer('readiness check')['input_ids']
        print('TOKENIZER',directory,flush=True)
print('READABLE_MODEL_FILES',files,flush=True)
# Produce a genuinely scored public submission with the prewritten Leaf probe.

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
    stop_at = time.monotonic()+2
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

