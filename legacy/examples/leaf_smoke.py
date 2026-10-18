"""Trusted small CPU candidate for paid-API-free execution smoke tests."""
import argparse
import json
from pathlib import Path
import numpy as np
import pandas as pd
from sklearn.model_selection import train_test_split
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import log_loss

p=argparse.ArgumentParser();p.add_argument('--task',type=Path,required=True);a=p.parse_args()
t=json.loads(a.task.read_text());d=Path(t['public_data_dir']);o=Path(t['output_dir'])
tr=pd.read_csv(d/'train.csv');te=pd.read_csv(d/'test.csv');sample=pd.read_csv(d/'sample_submission.csv')
fit,val=train_test_split(np.arange(len(tr)),test_size=.2,stratify=tr.species,random_state=0)
x=tr.drop(columns=['id','species']);xt=te.drop(columns=['id'])
m=make_pipeline(StandardScaler(),LogisticRegression(max_iter=500,random_state=0))
m.fit(x.iloc[fit],tr.species.iloc[fit]);pred=m.predict_proba(x.iloc[val]);score=log_loss(tr.species.iloc[val],pred,labels=m.classes_)
pd.DataFrame(pred,columns=m.classes_).assign(id=tr.id.iloc[val].to_numpy(),label=tr.species.iloc[val].to_numpy()).to_csv(o/'validation_predictions.csv',index=False)
(o/'validation-split.json').write_text(json.dumps({'validation_ids':tr.id.iloc[val].tolist()}))
m.fit(x,tr.species);pred=m.predict_proba(xt)
frame=pd.DataFrame(pred,columns=m.classes_,index=te.id)
for c in sample.columns[1:]:sample[c]=frame.loc[sample.id,c].to_numpy()
sample.to_csv(o/'submission.csv',index=False)
(o/'candidate-result.json').write_text(json.dumps({'validation_metric':t['validation_metric'],'validation_score':score,
                  'higher_is_better':False,'n_train':len(fit),'n_validation':len(val)}))
print('Public validation log loss',score,flush=True)
