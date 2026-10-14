import importlib.util
import tempfile
import unittest
from pathlib import Path
from mle_rsi.validation import validate_submission

@unittest.skipUnless(importlib.util.find_spec('pandas'), 'Requires ML environment')
class SubmissionContractTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.public=self.root/'public';self.public.mkdir()
        self.out=self.root/'out';self.out.mkdir()
    def test_birds_official_flat_schema_and_ids(self):
        (self.public/'sample_submission.csv').write_text('Id,Probability\n100,0\n101,0\n')
        meta={'task':'mlsp-2013-birds','metric':'micro_auc'}
        p=self.out/'submission.csv';p.write_text('rec_id,species,probability\n1,0,0.5\n1,1,0.4\n')
        with self.assertRaisesRegex(ValueError,'official columns'):validate_submission(self.public,self.out,meta)
        p.write_text('Id,Probability\n101,0.4\n100,0.5\n')
        self.assertEqual(validate_submission(self.public,self.out,meta)['rows'],2)
        p.write_text('Id,Probability\n100,0.5\n100,0.4\n')
        with self.assertRaisesRegex(ValueError,'IDs differ'):validate_submission(self.public,self.out,meta)
    def test_insults_comment_ids_and_probability_domain(self):
        (self.public/'sample.csv').write_text('Insult,Date,Comment\n0,date,abc\n0,date,def\n')
        meta={'task':'detecting-insults-in-social-commentary','metric':'auc'}
        p=self.out/'submission.csv';p.write_text('Comment,Insult\ndef,0.7\nabc,0.2\n')
        self.assertTrue(validate_submission(self.public,self.out,meta)['public_schema_checked'])
        p.write_text('Comment,Insult\ndef,7\nabc,0.2\n')
        with self.assertRaisesRegex(ValueError,'in \\[0,1\\]'):validate_submission(self.public,self.out,meta)
    def test_text_preserves_leading_zero_ids_and_literal_strings(self):
        (self.public/'sample.csv').write_text('id,after\n01,NA\n02,0\n')
        meta={'task':'text-normalization-challenge-english-language','metric':'text_accuracy'}
        p=self.out/'submission.csv';p.write_text('id,after\n02,NA\n01,000\n')
        self.assertEqual(validate_submission(self.public,self.out,meta)['rows'],2)
        p.write_text('id,after\n2,NA\n1,000\n')
        with self.assertRaisesRegex(ValueError,'IDs differ'):validate_submission(self.public,self.out,meta)
