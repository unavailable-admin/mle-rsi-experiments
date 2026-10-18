import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import numpy as np
try:
    import pandas as pd
except ModuleNotFoundError:
    raise unittest.SkipTest("Run this fixture with the configured ML Python")
from mle_rsi import runtime as rt
from mle_rsi.validation import prepare_cactus_kfold, score


class KfoldTest(unittest.TestCase):
    def test_protocol_and_rejections(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); public=root/'public'; public.mkdir(); out=root/'out'; out.mkdir()
            ids=np.asarray([f'image-{i}' for i in range(60)])
            y=np.arange(60)%2
            pd.DataFrame({'id':ids,'has_cactus':y}).to_csv(public/'train.csv',index=False)
            prepared=root/'prepared'; prepare_cactus_kfold(public,prepared)
            again=root/'again'; prepare_cactus_kfold(public,again)
            self.assertEqual((prepared/'split.csv').read_bytes(),(again/'split.csv').read_bytes())
            meta=json.loads((prepared/'manifest.json').read_text())
            split=pd.read_csv(prepared/'split.csv')
            task={'output_dir':str(out),'validation_metric':'auc','higher_is_better':True}
            with patch.object(rt,'manifest',return_value=meta), patch.object(rt,'split_table',return_value=split), patch.object(rt,'task_config',return_value=task):
                with self.assertRaises(ValueError):rt.split_indices(ids)
                folds=rt.fold_assignments(ids)
                records=[]
                for f in range(3):
                    tr,va=rt.split_indices(ids,fold=f)
                    self.assertEqual(len(va),20)
                    self.assertEqual(set(y[va]),{0,1})
                    with self.assertRaises(ValueError):rt.inner_split(ids,fold=f)
                    records.append({'fold':f,'train_ids':ids[tr],'validation_ids':ids[va]})
                settings={'model':'test','epochs':1,'seed':0}
                members=[{'name':'test','validation':settings,'folds':[settings]*3,'final':settings}]
                rt.record_kfold_training_contract(members,records,ids)
                # Perfect within folds but shifted fold calibration makes pooled AUC lower.
                preds=folds*.3+y*.1+.05
                rt.write_validation(ids[::-1],preds[::-1])
                result=score(prepared,out)
                self.assertEqual(result['validation_score'],1.)
                self.assertLess(result['cross_validation']['pooled_oof_auc'],1.)
                with self.assertRaises(ValueError):rt.write_validation(ids[:-1],preds[:-1])
                with self.assertRaises(ValueError):rt.record_kfold_training_contract(members,records,ids[:-1])
                original=(out/'validation_predictions.csv').read_text()
                frame=pd.read_csv(out/'validation_predictions.csv');frame.loc[0,'fold']=9
                frame.to_csv(out/'validation_predictions.csv',index=False)
                with self.assertRaisesRegex(ValueError,'fold IDs'):score(prepared,out)
                (out/'validation_predictions.csv').write_text(original)
                contract=json.loads((out/'training-contract.json').read_text())
                contract['members'][0]['final']={'epochs':2}
                (out/'training-contract.json').write_text(json.dumps(contract))
                with self.assertRaisesRegex(ValueError,'configurations'):score(prepared,out)

if __name__=='__main__':unittest.main()
