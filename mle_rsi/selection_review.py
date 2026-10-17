"""Freeze a public-only Cactus recheck plan with a fresh, separately scored split."""
from pathlib import Path
import json
import hashlib
from .common import read,write,sha


def prepare_cactus(source,destination,seed=20261017):
    import pandas as pd
    from sklearn.model_selection import train_test_split
    from . import WORKSPACE
    from .validation import finish_manifest
    source,destination=Path(source),Path(destination)
    if destination.exists():raise ValueError('Recheck destination must be new')
    task='aerial-cactus-identification'
    parent=source/'prepared'/task
    meta=read(parent/'manifest.json')
    public=WORKSPACE/'data/mlebench'/task/'prepared/public'
    frame=pd.read_csv(public/'train.csv')
    frame['row_index']=range(len(frame))
    train,valid=train_test_split(frame.index,test_size=.2,random_state=seed,stratify=frame.has_cactus)
    split=frame[['id','row_index']].copy();split['split']='train';split.loc[valid,'split']='validation'
    destination.mkdir(parents=True)
    split.to_csv(destination/'split.csv',index=False)
    frame.loc[valid,['id','has_cactus']].to_csv(destination/'truth.csv',index=False)
    meta.update(seed=seed,n_train=len(train),n_validation=len(valid),
        selection_review=True,parent_manifest_sha256=sha(parent/'manifest.json'),
        comparison_rule='Retrain each frozen source on this split; never rank against old-split scores. Public AUC remains primary; logloss is diagnostic only.')
    finish_manifest(destination,meta)
    if sha(destination/'split.csv')==sha(parent/'split.csv'):raise ValueError('Recheck split unexpectedly unchanged')
    # Select finalists using public scores only, deterministic tie break. Private
    # grades, including medal flags, are deliberately never read here.
    choices=[]
    for out in (source/'rounds').glob('*/'+task):
        if not (out/'selected.json').exists() or not (out/'seal.json').exists():continue
        selected=read(out/'selected.json')
        if selected and (out/'solver.py').exists():
            choices.append((selected['score'],int(out.parent.name),out))
    chosen=[];seen=set()
    for score,number,out in sorted(choices,key=lambda x:(-x[0],x[1])):
        digest=sha(out/'solver.py')
        if digest in seen:continue
        seen.add(digest)
        name=f'round-{number:04d}.py'
        (destination/name).write_bytes((out/'solver.py').read_bytes())
        chosen.append({'source':name,'sha256':digest,'original_round':number,'original_public_score':score})
        if len(chosen)==2:break
    # Round winners can all be the same saturated-AUC solution. Freeze one
    # distinct tied candidate as a robustness comparator, never select a final
    # winner by auxiliary loss or private feedback.
    if len(chosen)==1 and choices:
        from dream_rsi.storage import CampaignStore
        import re
        store=CampaignStore(source/'campaigns'/('dev-'+task),read_only=True)
        try:
            boundary=max(number for _,number,_ in choices)
            nodes=[n for key,n in store.list('node') if int(re.search(r'tree-(\d+)',key)[1])<=boundary
                   and n.evaluation.valid and n.score>=chosen[0]['original_public_score']-1e-12]
            for node in sorted(nodes,key=lambda n:n.created_order):
                digest=hashlib.sha256(node.artifact.source.encode()).hexdigest()
                if digest in seen:continue
                name='tied-comparator.py'
                (destination/name).write_text(node.artifact.source)
                chosen.append({'source':name,'sha256':digest,'node_id':node.node_id,
                               'original_public_score':node.score,'purpose':'independent retraining comparator; not a selected winner'})
                break
        finally:store.close()
    write(destination/'recheck-plan.json',{'state':'prepared_not_trained','seed':seed,
        'candidates':chosen,'manifest_sha256':sha(destination/'manifest.json'),
        'requires_fresh_training':True,'same_source_and_resources_for_each_candidate':True,
        'selection_basis':'public scores only','historical_scores_not_comparable':True,
        'limitation':'A fresh split of already explored public data is a robustness diagnostic, not an untouched test set.'})
    return destination
