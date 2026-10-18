"""Real container/weights/scoring check; no model API and no official grade."""
import json
from pathlib import Path
import numpy as np
import pandas as pd
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.linear_model import LogisticRegression
import mle_runtime as rt

task=rt.task_config();root=Path(task['public_data_dir']);out=Path(task['output_dir'])
train=pd.read_csv(root/'train.csv');test=pd.read_csv(root/'test.csv')
model,transform=rt.load_resnet50(device='cpu')
del model,transform
fit,val=rt.split_indices(train.id)
inner_fit,inner_val=rt.inner_split(train.id.iloc[fit].tolist(),train.species.iloc[fit].tolist())
assert len(inner_fit)+len(inner_val)==len(fit)
model=make_pipeline(StandardScaler(),LogisticRegression(max_iter=500,random_state=0))
x=train.drop(columns=['id','species']);xt=test.drop(columns=['id'])
rt.progress('fit',train_rows=len(fit),validation_rows=len(val))
model.fit(x.iloc[fit],train.species.iloc[fit])
pred=model.predict_proba(x.iloc[val])
assert list(model.classes_)==rt.manifest()['prediction_columns']
rt.write_validation(train.id.iloc[val],pred,reported_score=999,model='standardized-logistic-regression')
model.fit(x,train.species)
submission=pd.DataFrame(model.predict_proba(xt),columns=model.classes_)
submission.insert(0,'id',test.id)
submission.to_csv(out/'submission.csv',index=False)
print('Deliberately reported 999; host must replace it with real log-loss.',flush=True)
