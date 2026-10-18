import importlib.util
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch


@unittest.skipUnless(importlib.util.find_spec('pandas'),'Requires ML environment')
class FixesNativeTests(unittest.TestCase):
    def test_exact_prediction_normalization(self):
        import pandas as pd
        from mle_rsi.validation import prediction_fingerprint as fp
        a=pd.DataFrame({'id':['2','1'],'a':[.2,.1],'b':[.8,.9]})
        b=a.iloc[::-1][['b','id','a']]
        self.assertEqual(fp(a,'id',['a','b']),fp(b,'id',['b','a']))
        b=b.copy();b.loc[b.id=='1','a']=.11
        self.assertNotEqual(fp(a,'id',['a','b']),fp(b,'id',['a','b']))

    def test_birds_empty_known_labels_and_runtime_coverage(self):
        import pandas as pd
        from mle_rsi import candidate_runtime as rt
        from mle_rsi.validation import prepare
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp);public=base/'public';public.mkdir()
            # Include many known empty rows and unknown rows; inspect prepared IDs.
            (public/'essential_data').mkdir()
            labels=public/'essential_data/rec_labels_test_hidden.txt'
            labels.write_text('rec_id,labels\n'+''.join(f'{i},'+('?' if i>=30 else '0' if i%2 else '')+'\n' for i in range(40)))
            prepare('mlsp-2013-birds',public,base/'prepared')
            actual=pd.read_csv(base/'prepared/split.csv',dtype={'id':str})
            self.assertEqual(set(actual.id),set(map(str,range(30))))
            # Test the shared validation writer independently of task preparation.
            meta={'metric':'micro_auc','id_kind':'literal','split_file':'split.csv',
                  'prediction_columns':['s0'],'n_train':1,'n_validation':2}
            split=pd.DataFrame({'id':['positive','empty','train'],'split':['validation','validation','train']})
            task={'output_dir':str(base),'validation_metric':'auc-roc','higher_is_better':True}
            with patch.object(rt,'manifest',return_value=meta),patch.object(rt,'task_config',return_value=task),patch.object(rt,'split_table',return_value=split):
                with self.assertRaisesRegex(ValueError,"missing=.*empty"):
                    rt.write_validation(['positive'],[.7])
                rt.write_validation(['positive','empty'],[.7,.05])
                with self.assertRaisesRegex(ValueError,'duplicates'):
                    rt.write_validation(['positive','empty','empty'],[.7,.05,.05])

    def test_probability_diagnostics_do_not_change_score(self):
        import pandas as pd
        from mle_rsi.validation import prepare,score
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp);public=base/'public';public.mkdir();out=base/'out';out.mkdir()
            pd.DataFrame({'id':range(60),'species':['A','B']*30}).to_csv(public/'train.csv',index=False)
            dest=base/'prepared';prepare('leaf-classification',public,dest)
            truth=pd.read_csv(dest/'truth.csv',dtype=str)
            pred=pd.DataFrame({'id':truth.id,'A':(truth.species=='A')*.2+.4,'B':(truth.species=='B')*.2+.4})
            pred.to_csv(out/'validation_predictions.csv',index=False)
            d=score(dest,out)
            self.assertAlmostEqual(d['probability_diagnostics']['public_accuracy'],1)
            self.assertAlmostEqual(d['probability_diagnostics']['public_mean_confidence'],.6)
            self.assertAlmostEqual(d['probability_diagnostics']['public_log_loss'],d['validation_score'])
            self.assertEqual(len(d['prediction_fingerprints']['validation']),64)
