exec(open('/mnt/afs/yangsen1/runs/offline-validation-audit/audit.py').read().split("t='aerial-cactus-identification'")[0])
r=json.load(open(OUT/'results.json'));t='jigsaw-toxic-comment-classification-challenge';n=next(n for n in e[t]['nodes'] if n['id']=='tree-0001:n9');p=pathlib.Path(n['diagnostics']['output_dir']);truth=pd.read_csv(ROOT/'prepared'/t/'truth.csv').set_index('id');pred=pd.read_csv(p/'validation_predictions.csv').set_index('id').loc[truth.index];sub=pd.read_csv(p/'submission.csv');print('jigsaw columns',pred.columns.tolist(),truth.columns.tolist())
r['jigsaw_salvage']={'node':n['id'],'validation_rows':len(pred),'validation_finite':bool(np.isfinite(pred.to_numpy()).all()),'validation_in_01':bool(((pred>=0)&(pred<=1)).all().all()),'validation_macro_auc':float(np.mean([roc_auc_score(truth.iloc[:,i],pred.iloc[:,i]) for i in range(truth.shape[1])])),'submission_rows':len(sub),'submission_unique_ids':int(sub.iloc[:,0].nunique()),'submission_finite':bool(np.isfinite(sub.iloc[:,1:].to_numpy()).all()),'note':'CSV sanity/public score only; not accepted as official candidate or private graded'}
# Confirm actual resource assignment of both encoding failures.
r['jigsaw_cpu_resources']=[]
for n in e[t]['nodes']:
 if n['id'] not in ['tree-0001:n9','tree-0003:n59']:continue
 ref=n['diagnostics']['resources'];d=json.load(open(ROOT/'campaigns'/('dev-'+t)/ref['path']));r['jigsaw_cpu_resources'].append({'node':n['id'],'gpu':d.get('gpu'),'cpu_affinity':d.get('cpu_affinity')})
(OUT/'results.json').write_text(json.dumps(r,indent=2));print(json.dumps(r['jigsaw_salvage'],indent=2));print(r['jigsaw_cpu_resources'])
