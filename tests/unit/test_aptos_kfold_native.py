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
from PIL import Image
from sklearn.metrics import cohen_kappa_score
from mle_rsi import runtime as rt
from mle_rsi.validation import prepare_aptos_kfold, score, verify_prepared
from mle_rsi.tasks import contract_for


class AptosKfoldTest(unittest.TestCase):
    def test_groups_oof_metric_and_contract(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); public=root/'public'; (public/'train_images').mkdir(parents=True)
            ids=np.array([f'im-{i:03d}' for i in range(100)]); y=np.arange(100)%5
            pd.DataFrame({'id_code':ids,'diagnosis':y}).to_csv(public/'train.csv', index=False)
            for i, name in enumerate(ids):
                Image.fromarray(np.full((4,4,3), i if i != 5 else 0, dtype=np.uint8)).save(public/'train_images'/f'{name}.png')
            groups=root/'patients.csv'
            pd.DataFrame({'id':ids, 'group':[f'patient-{i//2}' for i in range(100)]}).to_csv(groups,index=False)
            prepared=root/'prepared'; again=root/'again'; out=root/'out'; out.mkdir()
            prepare_aptos_kfold(public,prepared,groups_file=groups)
            prepare_aptos_kfold(public,again,groups_file=groups)
            self.assertEqual((prepared/'split.csv').read_bytes(),(again/'split.csv').read_bytes())
            meta=verify_prepared(prepared); split=pd.read_csv(prepared/'split.csv')
            self.assertEqual(meta['grouping']['exact_rgb_duplicates'],1)
            self.assertTrue(meta['grouping']['patient_groups_supplied'])
            self.assertEqual(split.groupby('group').fold.nunique().max(),1)
            self.assertEqual(split.loc[0,'fold'],split.loc[5,'fold'])
            task={'output_dir':str(out),'validation_metric':'quadratic_weighted_kappa','higher_is_better':True}
            with patch.object(rt,'manifest',return_value=meta),patch.object(rt,'split_table',return_value=split),patch.object(rt,'task_config',return_value=task):
                folds=rt.fold_assignments(ids); records=[]
                for fold in range(3):
                    tr,va=rt.split_indices(ids,fold=fold)
                    self.assertFalse(set(split.iloc[tr].group)&set(split.iloc[va].group))
                    a,b=rt.inner_split(ids[tr],y[tr],fold=fold)
                    self.assertFalse(set(split.iloc[tr[a]].group)&set(split.iloc[tr[b]].group))
                    with self.assertRaises(ValueError):rt.inner_split(ids,fold=fold)
                    records.append({'fold':fold,'train_ids':ids[tr],'validation_ids':ids[va]})
                settings={'method':'agent-selected','seed':0}
                rt.record_kfold_training_contract([{'name':'chosen','validation':settings,'folds':[settings]*3,'final':settings}],records,ids)
                pred=np.where(folds==0,0,np.where(folds==1,y,y//2))
                rt.write_validation(ids[::-1],pred[::-1])
                result=score(prepared,out); cv=result['cross_validation']
                expected=cohen_kappa_score(y,pred,weights='quadratic',labels=list(range(5)))
                self.assertAlmostEqual(result['validation_score'],expected)
                self.assertNotAlmostEqual(result['validation_score'],cv['fold_kappa_mean'],places=5)
                self.assertEqual(cv['aggregation'],'pooled_oof_kappa')
                self.assertEqual(len(cv['fold_kappa']),3)
                with self.assertRaises(ValueError):rt.write_validation(ids[:-1],pred[:-1])
                rt.write_validation(ids,np.full(100,1.5))
                with self.assertRaisesRegex(ValueError,'integer'):score(prepared,out)
                rt.write_validation(ids,pred)
                contract=json.loads((out/'training-contract.json').read_text());contract['final_training']['refit']=False
                (out/'training-contract.json').write_text(json.dumps(contract))
                with self.assertRaisesRegex(ValueError,'refit'):score(prepared,out)
            self.assertIn('pooled out-of-fold',contract_for(meta))
            self.assertNotIn('fold ROC AUC',contract_for(meta))
            with self.assertRaises(ValueError):prepare_aptos_kfold(public,prepared,seed=1,groups_file=groups)


if __name__=='__main__':unittest.main()
