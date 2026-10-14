"""Unpaid readiness probe; never injected as a search seed."""
from pathlib import Path
import pandas as pd
import mle_runtime as rt
from sklearn.pipeline import make_pipeline
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.naive_bayes import MultinomialNB

meta=rt.manifest();task=rt.task_config();out=Path(task['output_dir'])
train=pd.read_csv('/data/train.csv');test=pd.read_csv('/data/test.csv')
a,b=rt.split_indices(train['id'])
def fit(frame):
    model=make_pipeline(TfidfVectorizer(ngram_range=(1,2),min_df=2),MultinomialNB(alpha=.1))
    model.fit(frame['text'],frame['author']);return model
model=fit(train.iloc[a]);cols=meta['prediction_columns']
order=[list(model.classes_).index(c) for c in cols]
rt.write_validation(train.iloc[b]['id'],model.predict_proba(train.iloc[b]['text'])[:,order],model='tfidf-nb-readiness')
rt.progress('full_refit',rows=len(train));model=fit(train)
order=[list(model.classes_).index(c) for c in cols]
submission=pd.DataFrame(model.predict_proba(test['text'])[:,order],columns=cols)
submission.insert(0,'id',test['id']);submission.to_csv(out/'submission.csv',index=False)
