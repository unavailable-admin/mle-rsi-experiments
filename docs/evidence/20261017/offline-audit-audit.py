import json, pathlib, hashlib, csv
import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score,log_loss
ROOT=pathlib.Path('/opt/mle-rsi-fixes/runs/lite22-001')
OUT=pathlib.Path('/mnt/afs/yangsen1/runs/offline-validation-audit')
e=json.load(open('/mnt/afs/yangsen1/runs/offline-max-trajectory-audit/evidence.json'))
a=json.load(open('/mnt/afs/yangsen1/runs/offline-max-trajectory-audit/analysis.json'))
result={'scope':'5090 rounds 1-4, existing public predictions only; no training or private rescoring'}
t='aerial-cactus-identification';truth=pd.read_csv(ROOT/'prepared'/t/'truth.csv').set_index('id'); y=truth.iloc[:,0].to_numpy();rows=[];preds=[]
for n in e[t]['nodes']:
 if not n['valid'] or n['metrics']['validation_score']!=1:continue
 p=pathlib.Path(n['diagnostics']['output_dir']); f=p/'validation_predictions.csv'
 if not f.exists():continue
 d=pd.read_csv(f).set_index('id').loc[truth.index];v=d.iloc[:,0].to_numpy();preds.append(v)
 rows.append({'node':n['id'],'auc':roc_auc_score(y,v),'logloss':log_loss(y,v),'margin':float(v[y==1].min()-v[y==0].max()),'validation_hash':hashlib.sha256(v.tobytes()).hexdigest(),'submission_hash':n['diagnostics'].get('submission_sha256')})
pd.DataFrame(rows).to_csv(OUT/'cactus-ties.csv',index=False)
result['cactus']={'tied':len(rows),'unique_validation':len({x['validation_hash'] for x in rows}),'unique_submission':len({x['submission_hash'] for x in rows}),'first':rows[0],'min_logloss':min(rows,key=lambda x:x['logloss']),'max_logloss':max(rows,key=lambda x:x['logloss']),'positive':int(y.sum()),'negative':int(len(y)-y.sum()),'mean_prediction_range':float(np.ptp(np.array(preds),axis=0).mean())}
t='siim-isic-melanoma-classification';prep=ROOT/'prepared'/t
split=pd.read_csv(prep/'split.csv').set_index('id');meta=pd.read_csv(prep/'sanitized-train.csv').set_index('image_name');joined=split.join(meta);tr=joined[joined['split']=='train'];va=joined[joined['split']=='validation'];
truth=pd.read_csv(prep/'truth.csv').set_index('id');y=truth.iloc[:,0].to_numpy();vectors=[];sel=[]
for r in [3,4]:
 s=json.load(open(ROOT/f'rounds/{r:04}'/t/'selected.json'));p=pathlib.Path(s['diagnostics']['original_output_dir']);v=pd.read_csv(p/'validation_predictions.csv').set_index('id').loc[truth.index].iloc[:,0].to_numpy();vectors.append(v);sel.append({'round':r,'node':s['node_id'],'auc':roc_auc_score(y,v),'events':s['diagnostics']['runtime_audit']['events']})
groups=meta.loc[truth.index,'patient_id'].fillna(pd.Series(truth.index,index=truth.index)).astype(str).to_numpy();ug=np.unique(groups);ix=[np.flatnonzero(groups==g) for g in ug];rng=np.random.default_rng(20261016);diff=[]
for _ in range(400):
 i=np.concatenate([ix[j] for j in rng.integers(0,len(ix),len(ix))]); yy=y[i]
 if len(np.unique(yy))==2:diff.append(roc_auc_score(yy,vectors[1][i])-roc_auc_score(yy,vectors[0][i]))
result['melanoma']={'patient_overlap':len(set(tr.patient_id.dropna())&set(va.patient_id.dropna())),'train_rows':len(tr),'validation_rows':len(va),'train_positive':int(tr.target.sum()),'validation_positive':int(va.target.sum()),'validation_patients':len(ug),'paired_patient_bootstrap_replicates':len(diff),'delta_auc':sel[1]['auc']-sel[0]['auc'],'delta_auc_percentile95':np.quantile(diff,[.025,.975]).tolist(),'warning':'same searched validation set; bootstrap does not establish independent generalization','selected':sel}
timeouts=[];cost=[]
for t,v in a.items():
 total=sum(n['wall'] or 0 for n in v['nodes']);cnt=0
 for n in v['nodes']:
  if not n.get('timeout'):continue
  cnt+=1;ev=next(z for z in e[t]['nodes'] if z['id']==n['id']);p=pathlib.Path(ev['diagnostics']['output_dir']);events=[]
  for line in n.get('tail','').splitlines():
   if line.startswith('MLE_PROGRESS '):
    try:events.append(json.loads(line[13:]))
    except:pass
  files=[{'name':str(f.relative_to(p)),'bytes':f.stat().st_size} for f in p.rglob('*') if f.is_file()] if p.exists() else []
  timeouts.append({'task':t,'node':n['id'],'last_event':events[-1] if events else None,'output_files':files,'validation_exists':(p/'validation_predictions.csv').exists(),'submission_exists':(p/'submission.csv').exists(),'source':str(ROOT/'campaigns'/('dev-'+t)/ev['artifact']['source']['path'])})
 cost.append({'task':t,'timeouts':cnt,'historical_execution_hours':total/3600,'extra_hours_for_same_timeouts_900_to_1800':cnt*.25,'percent_extra_vs_historical':cnt*900/total*100 if total else 0,'extra_minutes_per25_at_historical_rate':cnt/100*25*15})
result['cost']=cost;result['timeouts']=timeouts
(OUT/'results.json').write_text(json.dumps(result,indent=2))
print(json.dumps({k:v for k,v in result.items() if k not in ['timeouts']},indent=2))
print('TIMEOUT OUTPUTS',[(x['task'],x['node'],x['validation_exists'],x['submission_exists'],[f['name'] for f in x['output_files']]) for x in timeouts])
