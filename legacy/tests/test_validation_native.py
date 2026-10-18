"""Run under the ML environment: python -m unittest discover -s tests -p test_validation_native.py."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

from mle_rsi.validation import prepare, score, finish_manifest, canonical_id


@unittest.skipUnless(importlib.util.find_spec('pandas'), 'Requires installed ML environment')
class PublicValidationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.public=self.root/'public';self.public.mkdir();self.dest=self.root/'prepared';self.out=self.root/'output';self.out.mkdir()

    def test_singleton_class_stays_in_training_and_labels_are_independent(self):
        import pandas as pd
        pd.DataFrame({'Id':range(101),'Cover_Type':[1]*50+[2]*50+[3]}).to_csv(self.public/'train.csv',index=False)
        prepare('tabular-playground-series-dec-2021',self.public,self.dest)
        split=pd.read_csv(self.dest/'split.csv');self.assertEqual(split.loc[split.id==100,'split'].iloc[0],'train')
        truth=pd.read_csv(self.dest/'truth.csv');truth.rename(columns={'Cover_Type':'prediction'}).to_csv(self.out/'validation_predictions.csv',index=False)
        self.assertEqual(score(self.dest,self.out)['validation_score'],1.)
        truth['prediction']=0;truth[['id','prediction']].to_csv(self.out/'validation_predictions.csv',index=False)
        self.assertEqual(score(self.dest,self.out)['validation_score'],0.)

    def test_pizza_sanitization_removes_outcome_features_and_archives(self):
        records=[{'request_id':str(i),'request_text_edit_aware':'need help','requester_user_flair':'shroom' if i%2 else None,
                  'post_was_edited':bool(i%2),'requester_received_pizza':bool(i%2)} for i in range(30)]
        (self.public/'train.json').write_text(json.dumps(records));(self.public/'test.json').write_text(json.dumps([{'request_id':'test','request_text_edit_aware':'hi'}]))
        (self.public/'sampleSubmission.csv').write_text('request_id,requester_received_pizza\ntest,0\n');(self.public/'description.md').write_text('public')
        prepare('random-acts-of-pizza',self.public,self.dest)
        sanitized=json.loads((self.dest/'public/train.json').read_text())
        self.assertNotIn('requester_user_flair',sanitized[0]);self.assertNotIn('post_was_edited',sanitized[0])
        self.assertIn('requester_received_pizza',sanitized[0]);self.assertFalse((self.dest/'public/train.zip').exists())

    def test_image_extensions_are_normalized_but_missing_or_duplicate_ids_fail(self):
        self.assertEqual(canonical_id('dog.7313','image_stem'),'dog.7313')
        self.assertEqual(canonical_id('dog.7313.jpg','image_stem'),'dog.7313')
        (self.public/'train').mkdir()
        for animal in ['dog','cat']:
            for i in range(10):(self.public/'train'/f'{animal}.{i}.jpg').touch()
        prepare('dogs-vs-cats-redux-kernels-edition',self.public,self.dest)
        import pandas as pd
        truth=pd.read_csv(self.dest/'truth.csv');truth['id']=truth.id+'.jpg';truth.rename(columns={'label':'prediction'},inplace=True)
        truth.to_csv(self.out/'validation_predictions.csv',index=False)
        self.assertLess(score(self.dest,self.out)['validation_score'],1e-10)
        truth['id']=truth['id'].str.removesuffix('.jpg');truth.to_csv(self.out/'validation_predictions.csv',index=False)
        self.assertLess(score(self.dest,self.out)['validation_score'],1e-10)
        truth.iloc[:-1].to_csv(self.out/'validation_predictions.csv',index=False)
        with self.assertRaisesRegex(ValueError,'IDs differ'):score(self.dest,self.out)

    def test_multiclass_rejects_forged_report_and_wrong_probability_order(self):
        import pandas as pd
        pd.DataFrame({'id':range(30),'species':['A','B','C']*10}).to_csv(self.public/'train.csv',index=False)
        prepare('leaf-classification',self.public,self.dest);truth=pd.read_csv(self.dest/'truth.csv')
        p=pd.DataFrame({'id':truth.id,**{c:(truth.species==c).astype(float) for c in ['A','B','C']}})
        p.to_csv(self.out/'validation_predictions.csv',index=False);(self.out/'candidate-result.json').write_text('{"validation_score":999}')
        self.assertLess(score(self.dest,self.out)['validation_score'],1e-10)
        p['A']=1;p['B']=1;p.to_csv(self.out/'validation_predictions.csv',index=False)
        with self.assertRaisesRegex(ValueError,'sum to one'):score(self.dest,self.out)

    def test_group_split_never_separates_patient_and_manifest_drift_fails(self):
        import pandas as pd
        pd.DataFrame({'image_name':[str(i) for i in range(30)],'patient_id':[str(i//3) for i in range(30)],
            'target':[i%2 for i in range(30)],'diagnosis':['leak']*30,'benign_malignant':['leak']*30}).to_csv(self.public/'train.csv',index=False)
        pd.DataFrame({'image_name':['test'],'patient_id':['other']}).to_csv(self.public/'test.csv',index=False)
        prepare('siim-isic-melanoma-classification',self.public,self.dest)
        split=pd.read_csv(self.dest/'split.csv');self.assertTrue((split.groupby(split.row_index//3)['split'].nunique()==1).all())
        self.assertEqual(set(pd.read_csv(self.dest/'sanitized-train.csv').columns),{'image_name','patient_id','target'})
        (self.dest/'truth.csv').write_text('corrupted')
        with self.assertRaisesRegex(ValueError,'changed'):score(self.dest,self.out)

    def test_metric_formulas(self):
        import pandas as pd
        import numpy as np
        cases=[('rmse',['y'],['prediction'],[[1],[3]],[[2],[2]],1.),
               ('rmsle',['a','b'],['a','b'],[[0,0],[0,0]],[[1,3],[1,3]],(np.log(2)+np.log(4))/2),
               ('macro_auc',['a','b'],['a','b'],[[0,1],[1,0]],[[.1,.9],[.9,.1]],1.),
               ('micro_auc',['a','b'],['a','b'],[[0,1],[1,0]],[[.1,.9],[.9,.1]],1.),
               ('kappa',['diagnosis'],['prediction'],[[0],[4]],[[0],[4]],1.),
               ('text_accuracy',['after'],['prediction'],[['001'],['NaN']],[['001'],['NaN']],1.)]
        for metric,targets,cols,y,p,expected in cases:
            with self.subTest(metric=metric):
                self.dest.mkdir(exist_ok=True)
                truth=pd.DataFrame(y,columns=targets);truth.insert(0,'id',['x','y']);truth.to_csv(self.dest/'truth.csv',index=False)
                pred=pd.DataFrame(p,columns=cols);pred.insert(0,'id',['x','y']);pred.to_csv(self.out/'validation_predictions.csv',index=False)
                finish_manifest(self.dest,{'metric':metric,'targets':targets,'prediction_columns':cols,'id_kind':'literal','n_train':8,'n_validation':2})
                self.assertAlmostEqual(score(self.dest,self.out)['validation_score'],expected)


if __name__=='__main__':unittest.main()
