from pathlib import Path
import json
import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score,log_loss,accuracy_score
base=Path(__file__).parent
truth=pd.read_csv(base/'truth.csv').set_index('id');y=truth.has_cactus.to_numpy();preds={};rows=[]
for name in ['round-0001','tied-comparator']:
 run=base/'retrain-seed0'/name
 p=pd.read_csv(run/'output/validation_predictions.csv').set_index('id').loc[truth.index,'prediction'].to_numpy()
 preds[name]=p
 pos=p[y==1];neg=p[y==0]
 # Pairwise ranking errors avoid rounding away very small AUC differences.
 negsort=np.sort(neg);wrong=sum(len(neg)-np.searchsorted(negsort,z,side='right') for z in pos);ties=sum(np.searchsorted(negsort,z,side='right')-np.searchsorted(negsort,z,side='left') for z in pos)
 rows.append({'candidate':name,'auc':roc_auc_score(y,p),'logloss':log_loss(y,p),'accuracy_at_0_5':accuracy_score(y,p>=.5),'wrong_pairs':int(wrong),'tied_pairs':int(ties),'total_pairs':len(pos)*len(neg),'resources':json.loads((run/'resources.json').read_text())})
rng=np.random.default_rng(20261017);pos=np.flatnonzero(y==1);neg=np.flatnonzero(y==0);diff=[]
for _ in range(1000):
 ix=np.r_[rng.choice(pos,len(pos),replace=True),rng.choice(neg,len(neg),replace=True)]
 diff.append(roc_auc_score(y[ix],preds['tied-comparator'][ix])-roc_auc_score(y[ix],preds['round-0001'][ix]))
old=pd.read_csv(base.parent.parent/'run/prepared/aerial-cactus-identification/split.csv');new=pd.read_csv(base/'split.csv');ov=set(old.loc[old.split=='validation','id']);nv=set(new.loc[new.split=='validation','id'])
result={'split_seed':20261017,'training_seed':0,'n_validation':len(y),'n_positive':int(y.sum()),'old_validation_overlap':len(ov&nv),'new_validation_count':len(nv),'rows':rows,'auc_delta_comparator_minus_original':rows[1]['auc']-rows[0]['auc'],'paired_stratified_bootstrap_delta_ci95':np.quantile(diff,[.025,.975]).tolist(),'bootstrap_replicates':1000,'prediction_mean_absolute_difference':float(np.mean(np.abs(preds['round-0001']-preds['tied-comparator']))),'limitation':'One new split and one training seed per frozen solver; public data already explored; no private grading, no untouched generalization claim.'}
(base/'retrain-seed0/analysis.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))
