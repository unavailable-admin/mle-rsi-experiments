import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from mle_rsi import candidate_runtime as rt


@unittest.skipUnless(importlib.util.find_spec('pandas'),'Requires installed ML environment')
class RuntimeAuditTests(unittest.TestCase):
    def test_validation_write_does_not_invent_cpu_training_or_replace_clock(self):
        with tempfile.TemporaryDirectory() as directory:
            task={'output_dir':directory,'validation_metric':'auc-roc','higher_is_better':True}
            meta={'metric':'auc','id_kind':'literal','prediction_columns':['prediction'],'n_train':8,'n_validation':2}
            with patch.object(rt,'task_config',return_value=task),patch.object(rt,'manifest',return_value=meta),patch.object(rt,'_events',[]):
                rt.report_model('resnet50',device='cuda',checkpoint='local')
                rt.write_validation(['a','b'],[.1,.9],model='resnet50')
                rt.progress('complete',elapsed_seconds=1e12)
                events=json.loads((Path(directory)/'runtime-audit.json').read_text())['events']
            self.assertEqual([e['device'] for e in events if e['stage']=='model'],['cuda'])
            self.assertEqual(events[1]['stage'],'validation_output')
            self.assertLess(events[-1]['elapsed_seconds'],1e6)


if __name__=='__main__':unittest.main()
