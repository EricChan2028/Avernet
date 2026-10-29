"""Cron lists cannot silently close a retained execution scope."""
import copy
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from bcs_e2e.fixture_cron import project_crons, MAX_ROWS
from bcs_e2e.profile import ConfigError


class FixtureCronTests(unittest.TestCase):
    def payload(self):
        return {'success': True,'error_code':200,'message':'OK','data':[], 'failed_targets':[]}

    def job(self, **changes):
        return {'id':'job-1','bot_id':'test-bot','owner_id':'test-owner','enabled':True,
                'runtime_stage':'draft','payload':{'message':'PRIVATE-PROMPT'},'name':'PRIVATE-NAME',**changes}

    def project(self,payload):return project_crons(payload,bot_id='test-bot',owner_id='test-owner')

    def test_positive_scalar_projection_preserves_stages_without_prompt(self):
        p=self.payload();p['data']=[self.job(),self.job(runtime_stage='online')]
        result=self.project(p)
        self.assertEqual(result['status'],'OBSERVED');self.assertEqual(result['enabled_count'],2)
        self.assertEqual([r['runtime_stage'] for r in result['jobs']],['draft','online'])
        self.assertNotIn('PRIVATE',json.dumps(result));self.assertFalse(result['pool_release_authorized'])

    def test_empty_success_is_not_runtime_inventory_or_input_closure(self):
        result=self.project(self.payload())
        self.assertEqual(result['status'],'OBSERVED');self.assertEqual(result['job_count'],0)
        self.assertEqual(result['runtime_inventory_completeness'],'NOT_VERIFIED')
        self.assertEqual(result['input_boundary'],'NOT_PROVEN')
        self.assertEqual(result['running_jobs'],'NOT_QUERIED')
        self.assertFalse(result['ready_to_execute'])

    def test_missing_failures_or_v1_page_cannot_masquerade_as_empty(self):
        for payload in ({'success':True,'error_code':200,'data':[]},
                        {'code':0,'data':{'items':[],'total':0},'message':'success'},
                        {**self.payload(),'failed_targets':None}):
            with self.assertRaises(ConfigError):self.project(payload)

    def test_failure_and_partial_empty_are_not_success(self):
        p=self.payload();p['failed_targets']=[{'bot_id':'test-bot','owner_id':'test-owner',
            'runtime_stage':'verify','reason':'PRIVATE-ERROR','message':'PRIVATE-DETAIL'}]
        result=self.project(p);self.assertEqual(result['status'],'INCOMPLETE')
        self.assertEqual(result['failed_target_count'],1);self.assertNotIn('PRIVATE',json.dumps(result))
        p['success']=False
        self.assertEqual(self.project(p)['status'],'BLOCKED')
        p['success']=True;p['error_code']=500
        self.assertEqual(self.project(p)['status'],'BLOCKED')

    def test_disabled_or_unknown_enabled_rows_still_do_not_prove_input_closure(self):
        for value,status in ((False,'OBSERVED'),(None,'INCOMPLETE')):
            p=self.payload();p['data']=[self.job(enabled=value)]
            result=self.project(p);self.assertEqual(result['status'],status)
            self.assertEqual(result['input_boundary'],'NOT_PROVEN')

    def test_wrong_owner_bot_stage_conflicting_id_or_enabled_is_rejected(self):
        for changes in ({'bot_id':'foreign'},{'owner_id':'foreign'},{'runtime_stage':'prod'},
                        {'runtime_stage':[]},{'enabled':1},{'enabled':'false'},{'task_id':'another'}):
            p=self.payload();p['data']=[self.job(**changes)]
            with self.subTest(changes=changes),self.assertRaises(ConfigError):self.project(p)

    def test_personal_stage_absent_is_preserved_not_invented(self):
        p=self.payload();job=self.job();del job['runtime_stage'];p['data']=[job]
        self.assertIsNone(self.project(p)['jobs'][0]['runtime_stage'])

    def test_foreign_partial_failure_is_rejected(self):
        p=self.payload();p['failed_targets']=[{'bot_id':'foreign','owner_id':'test-owner'}]
        with self.assertRaises(ConfigError):self.project(p)

    def test_duplicate_unknown_identity_and_oversize_are_not_trimmed(self):
        p=self.payload();p['data']=[self.job(),self.job()]
        r=self.project(p);self.assertEqual(r['status'],'INCOMPLETE');self.assertEqual(r['job_count'],2)
        self.assertEqual(r['duplicate_count'],1)
        p['data']=[self.job(id=None)]
        with self.assertRaises(ConfigError):self.project(p)
        p['data']=[self.job(id=f'job-{i}') for i in range(MAX_ROWS+1)]
        with self.assertRaises(ConfigError):self.project(p)

    def test_boolean_integer_confusion_and_truncation_fail_closed(self):
        for changes in ({'success':1},{'error_code':True},{'truncated':True},{'truncated':0},
                        {'data':{}},{'failed_targets':[{}]*9}):
            with self.subTest(changes=changes),self.assertRaises(ConfigError):self.project({**self.payload(),**changes})

    def test_input_is_not_modified(self):
        p=self.payload();p['data']=[self.job()];before=copy.deepcopy(p);self.project(p)
        self.assertEqual(p,before)


if __name__=='__main__':unittest.main()
