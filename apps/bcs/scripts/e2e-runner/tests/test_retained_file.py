"""Separate retained File suite: local/mock only, no delete or remote admission."""
import copy
import contextlib
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from bcs_e2e.cli import main
from bcs_e2e.inventory import current_manifest
from bcs_e2e.ledger import Ledger
from bcs_e2e.persistent_mock import PersistentLocalMockAdapter
from bcs_e2e.pool import pool_path,pool_scope
from bcs_e2e.profile import ConfigError,fingerprint,load
from bcs_e2e.reporting import build_report,render_junit,render_markdown,write_reports
from bcs_e2e import retained as retained_module
from bcs_e2e.retained import retained_run
from bcs_e2e.run_state import read,write
from bcs_e2e.write_transport import WriteTransportError


class RetainedFileTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name)
        self.profile=load(ROOT/'profiles/local.mock.json')
        registry=patch('bcs_e2e.pool.pool_root',return_value=self.root/'registry')
        registry.start();self.addCleanup(registry.stop)

    def run_case(self,name='file-one',previous=None,**kwargs):
        return retained_run(self.profile,state_dir=self.root/name,run_id=name,case_id='RETAIN-FILE-01',
                            allow_write=True,confirm_run_id=name,serial_preflight=True,
                            previous_state_dir=self.root/previous if previous else None,**kwargs)

    def snapshot(self,name):
        return {str(p.relative_to(self.root/name)):p.read_bytes()
                for p in (self.root/name).rglob('*') if p.is_file()}

    def assert_no_deletes(self,name):
        state=read(self.root/name/'local-mock.json')
        self.assertEqual(state['deleted'],[])
        self.assertEqual(state['deleted_sessions'],[])
        self.assertEqual(state['deleted_files'],[])

    def test_two_serial_rounds_pass_and_preserve_first_run(self):
        first=self.run_case('file-one')
        self.assertEqual((first['status'],first['canonical_summary_status']),('PASS','RETAINED_CASE_PASS'))
        before=self.snapshot('file-one')
        second=self.run_case('file-two','file-one')
        self.assertEqual((second['status'],second['canonical_summary_status']),('PASS','RETAINED_CASE_PASS'))
        self.assertEqual(second['serial_admission']['previous_run_id'],'file-one')
        self.assertEqual(self.snapshot('file-one'),before)
        for name in ('file-one','file-two'):
            self.assert_no_deletes(name)
            resources=read(self.root/name/'retention.json')['resources']
            self.assertEqual([r['kind'] for r in resources].count('group'),1)
            self.assertEqual([r['kind'] for r in resources].count('session'),2)
            self.assertEqual([r['kind'] for r in resources].count('file'),1)
            self.assertFalse(any(r['kind']=='share' for r in resources))
            self.assertNotIn('retention_quota',read(self.root/name/'run.json'))

    def test_cli_serial_preflight_runs_two_rounds(self):
        profile=self.root/'profile.json';profile.write_text(json.dumps(self.profile))
        def invoke(name,previous=None):
            argv=['retained-run','--profile',str(profile),'--state-dir',str(self.root/name),
                  '--run-id',name,'--case','RETAIN-FILE-01','--allow-write',
                  '--confirm-run-id',name,'--serial-preflight']
            if previous is not None:argv.extend(['--previous-state-dir',str(self.root/previous)])
            with contextlib.redirect_stdout(io.StringIO()) as output:
                rc=main(argv)
            return rc,json.loads(output.getvalue())
        first_rc,first=invoke('cli-one')
        second_rc,second=invoke('cli-two','cli-one')
        self.assertEqual((first_rc,second_rc),(0,0))
        self.assertEqual((first['canonical_summary_status'],second['canonical_summary_status']),
                         ('RETAINED_CASE_PASS','RETAINED_CASE_PASS'))
        self.assertEqual(second['serial_admission']['previous_run_id'],'cli-one')

    def test_canonical_json_junit_markdown_and_saved_reports_omit_share_tokens(self):
        result=self.run_case()
        summary=build_report(self.profile,self.root/'file-one')
        self.assertEqual(summary['status'],'RETAINED_CASE_PASS')
        self.assertEqual(summary['scope']['suite'],'preprod-retained-file-v1')
        self.assertEqual(summary['scope']['legacy_inventory_count'], len(current_manifest('preprod-retained-file-v1')['legacy_inventory']))
        self.assertEqual([c['id'] for c in summary['cases']],['RETAIN-FILE-01'])
        self.assertEqual(summary['retention']['quota']['status'],'NOT_RECORDED')
        junit=render_junit(summary);markdown=render_markdown(summary)
        node=ET.fromstring(junit).find("testcase[@name='RETAIN-FILE-01']")
        self.assertIsNotNone(node);self.assertIsNone(node.find('skipped'));self.assertIsNone(node.find('failure'))
        self.assertIn('RETAIN-FILE-01',markdown);self.assertIn('RETAINED_UNRESOLVED',markdown)
        output=self.root/'rendered';write_reports(summary,output,{'all'})
        public='\n'.join([json.dumps(result),json.dumps(summary),junit,markdown,
                          *(p.read_text() for p in output.iterdir())])
        self.assertNotIn('local-mock-share-',public)
        self.assertNotIn('share_token',public)

    def test_success_failure_and_cancellation_never_delete(self):
        success=self.run_case('success');self.assertEqual(success['functional'],'PASS');self.assert_no_deletes('success')
        with patch.object(PersistentLocalMockAdapter,'file_download',return_value=(b'wrong',{})):
            failure=self.run_case('failure','success')
        self.assertEqual(failure['functional'],'FAIL');self.assert_no_deletes('failure')
        with patch.object(PersistentLocalMockAdapter,'file_download',side_effect=KeyboardInterrupt()):
            cancelled=self.run_case('cancelled','failure')
        self.assertEqual((cancelled['functional'],cancelled['status']),('CANCELLED','CANCELLED'))
        self.assert_no_deletes('cancelled')

    def test_bad_prepare_retains_only_the_file_intent_and_does_not_retry(self):
        calls=[]
        def bad(adapter,session_id,body):
            calls.append((session_id,body))
            return {},{'operation':'file.prepare','method':'POST','path':'/mock/file','http_status':201,
                       'submitted_request_id':'bad-prepare','request_id_echoed':True,'elapsed_ms':0.0}
        with patch.object(PersistentLocalMockAdapter,'file_prepare',bad):
            result=self.run_case()
        self.assertEqual(result['functional'],'FAIL');self.assertEqual(len(calls),1)
        resources=read(self.root/'file-one'/'retention.json')['resources']
        file=next(r for r in resources if r['kind']=='file')
        self.assertIsNone(file['resource_id']);self.assertEqual(file['origin'],'unknown')
        self.assert_no_deletes('file-one')

    def test_lost_prepare_response_keeps_unknown_intent_without_retry(self):
        original=PersistentLocalMockAdapter.file_prepare
        calls=[]
        def lost(adapter,*args):
            calls.append(args)
            original(adapter,*args)
            raise WriteTransportError('lost prepare response')
        with patch.object(PersistentLocalMockAdapter,'file_prepare',lost):
            result=self.run_case()
        self.assertEqual(result['functional'],'FAIL');self.assertEqual(len(calls),1)
        retention=read(self.root/'file-one'/'retention.json')
        file=next(r for r in retention['resources'] if r['kind']=='file')
        self.assertIsNone(file['resource_id']);self.assertEqual(retention['inventory_status'],'UNKNOWN_RESOURCES')
        self.assert_no_deletes('file-one')

    def test_file_parent_must_be_explicit_session_not_initial_session(self):
        self.run_case();state=self.root/'file-one'
        with Ledger(state/'run.sqlite','file-one',fingerprint(self.profile)) as ledger:
            rows=ledger.resource_snapshot()
            explicit=next(r for r in rows if r['kind']=='session' and r['request_id']=='e2e-pre03-session-file-one')
            initial=next(r for r in rows if r['kind']=='session' and r['request_id'].startswith('e2e-initial-'))
            file=next(r for r in rows if r['kind']=='file')
            self.assertEqual(file['parent'],explicit['handle'])
            ledger.db.execute('UPDATE resource SET parent=? WHERE handle=?',(initial['handle'],file['handle']))
            ledger.db.commit();rows=ledger.resource_snapshot()
        retention=read(state/'retention.json')
        fields=('handle','case_id','attempt','kind','parent','resource_id','origin','state','request_id')
        retention['resources']=[{key:row[key] for key in fields} for row in rows]
        write(state/'retention.json',retention)
        summary=build_report(self.profile,state)
        self.assertEqual(summary['retention']['status'],'BLOCKED')
        self.assertIn('retention inventory, disposition, quota or source binding is invalid',summary['integrity']['issues'])

    def test_raw_executor_rejects_per_kind_budget_even_when_total_is_four(self):
        progress_type,steps=retained_module.CASES['RETAIN-FILE-01']
        def corrupt(*args,**kwargs):
            steps(*args,**kwargs)
            ledger=kwargs['ledger']
            ledger.db.execute("DELETE FROM resource WHERE kind='session' AND request_id LIKE 'e2e-initial-%'")
            ledger.db.commit()
            ledger.intent('RETAIN-FILE-01',1,'group','extra-retained-group')
        with patch.dict(retained_module.CASES,{'RETAIN-FILE-01':(progress_type,corrupt)}), \
             self.assertRaisesRegex(ConfigError,'per-kind'):
            self.run_case()
        self.assertFalse((self.root/'file-one'/'report-RETAIN-FILE-01.json').exists())
        self.assert_no_deletes('file-one')

    def test_raw_executor_rejects_file_parented_to_initial_session_before_pass_report(self):
        progress_type,steps=retained_module.CASES['RETAIN-FILE-01']
        def corrupt(*args,**kwargs):
            steps(*args,**kwargs)
            ledger=kwargs['ledger']
            initial=ledger.db.execute(
                "SELECT handle FROM resource WHERE kind='session' AND request_id LIKE 'e2e-initial-%'"
            ).fetchone()[0]
            ledger.db.execute("UPDATE resource SET parent=? WHERE kind='file'",(initial,))
            ledger.db.commit()
        with patch.dict(retained_module.CASES,{'RETAIN-FILE-01':(progress_type,corrupt)}), \
             self.assertRaisesRegex(ConfigError,'parent topology'):
            self.run_case()
        self.assertFalse((self.root/'file-one'/'report-RETAIN-FILE-01.json').exists())
        self.assert_no_deletes('file-one')

    def test_original_remote_bootstrap_and_control_v1_review_still_cannot_admit_file(self):
        profile=copy.deepcopy(self.profile)
        profile['entrypoint']['kind']='gateway';profile['environment']['tier']='preprod'
        state=self.root/'remote'
        with patch('bcs_e2e.retained_file_remote.FileRetainedReview') as file_review, \
             patch('bcs_e2e.retained.remote_file_preflight') as preflight, \
             patch('bcs_e2e.retained.PoolLease._guard') as guard, \
             patch('bcs_e2e.retained.reserve_retention_quota') as quota, \
             self.assertRaises(ConfigError):
            retained_run(profile,state_dir=state,run_id='remote-file',case_id='RETAIN-FILE-01',
                         allow_write=True,confirm_run_id='remote-file',retained_review=self.root/'review.json',
                         observer_binding=self.root/'binding.json',executable=self.root/'mcporter',
                         allow_http_read=True,allow_observer_read=True,serial_preflight=False)
        file_review.assert_not_called();preflight.assert_not_called();guard.assert_not_called();quota.assert_not_called()
        self.assertFalse(state.exists())

    def test_old_retained_manifest_is_byte_stable_and_file_suite_has_same_denominator(self):
        old_path=ROOT/'retained-manifest.json'
        self.assertEqual(hashlib.sha256(old_path.read_bytes()).hexdigest(),
                         '99ccd2756cdc84c98d537dd6572129be1d8dbe364dab3743965b262a6cffae13')
        old=current_manifest('preprod-retained-v1');file=current_manifest('preprod-retained-file-v1')
        self.assertEqual([c['id'] for c in old['cases']],['RETAIN-GROUP-01','RETAIN-SESSION-01'])
        self.assertEqual([c['id'] for c in file['cases']],['RETAIN-FILE-01'])
        self.assertEqual(file['legacy_inventory'],old['legacy_inventory'])
        self.assertEqual(len(file['legacy_inventory']), len(current_manifest('preprod-retained-file-v1')['legacy_inventory']))


if __name__=='__main__':unittest.main()
