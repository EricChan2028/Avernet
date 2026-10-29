"""Synthetic bound-report integrity tests; actual CLI execution is tested separately."""
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from bcs_e2e.cli import main
from bcs_e2e.inventory import current_manifest
from bcs_e2e.profile import fingerprint
from bcs_e2e.reporting import build_report, render_junit, render_markdown
from bcs_e2e.run_state import read, write, RunStateError
from bcs_e2e.trust_context import CONTEXT_CONTRACT
from bcs_e2e.trust_registration import CASE_ID, SUITE
from bcs_e2e.trust_reporting import REPORT_NAME, _sha, publish_trust_report
from test_trust_context import example


class TrustReportingTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.root=Path(temp.name).resolve();self.profile=example()
        self.run_id=self.profile['environment']['id'];self.manifest=current_manifest(SUITE)
        self.binding={'suite':SUITE,'case_id':CASE_ID,'case_version':1,
                      'manifest_digest':fingerprint(self.manifest),'executor_digest':'a'*64,'migration_contract_digest':'b'*64}
        registration={'schema_version':1,'run_id':self.run_id,'runtime_dir':str(self.root),
                      'bcs_binary_sha256':'c'*64,**self.binding}
        fixture={'schema_version':1,'run_id':self.run_id}
        write(self.root/'profile.json',self.profile);write(self.root/'manifest.json',self.manifest)
        write(self.root/'trust-registration.json',registration);write(self.root/'local-fixtures.json',fixture)
        (self.root/'bcs-config.json').write_text('{}')
        context={'schema_version':1,'contract':CONTEXT_CONTRACT,'run_id':self.run_id,'runtime_dir':str(self.root),
                 'profile_digest':fingerprint(self.profile),'registration_digest':fingerprint(registration),
                 'fixture_digest':fingerprint(fixture),'runtime_config_digest':_sha(self.root/'bcs-config.json'),**self.binding}
        self.run={'schema_version':1,'status':'CONTEXT_BOUND','canonical_reporting':'COMPLETION_REQUIRED',
                  'ready_to_execute':False,'run_id':self.run_id,'runtime_dir':str(self.root),
                  'profile_digest':fingerprint(self.profile),'fixture_digest':fingerprint(fixture),
                  'run_context_digest':fingerprint(context),**self.binding}
        write(self.root/'trust-run-context.json',context);write(self.root/'run.json',self.run)
        self.shutdown={'schema_version':1,'run_id':self.run_id,'status':'STOPPED','returncode':0,
                       'origin':self.profile['entrypoint']['origin'],'binary_sha256':'c'*64}
        write(self.root/'owned-bcs.json',self.shutdown)
        self.journal={**self.run,'status':'LOCAL_TRUST_API_CLI_PASS','functional':'PASS','visibility_restoration':'PASS',
                      'assertions':[{'id':value,'status':'PASS'} for value in self.manifest['cases'][0]['assertions']],
                      'operations':[{'operation':'friend.request','status':'ACKNOWLEDGED'} for _ in range(7)],
                      'cli_operations':[{'operation':value,'status':'ACKNOWLEDGED','credential_cleanup':'CLEARED','cli_exit_code':0}
                                        for value in ('request','requests','accept','list')]}
        write(self.root/'local-trust-api.json',self.journal)
        self.runtime=SimpleNamespace(_record=lambda:self.shutdown)
        self.registration=SimpleNamespace(context=SimpleNamespace(profile=self.profile,root=self.root),validate=lambda runtime:None)

    def publish(self, failure=None):
        return publish_trust_report(self.registration,self.runtime,result={'trust_api':self.journal} if failure is None else None,failure=failure)

    def test_all_formats_and_offline_cli_bind_one_local_subset_without_release_credit(self):
        result=self.publish();self.assertEqual(result['status'],'LOCAL_SUITE_PASS')
        self.assertEqual(set(result['outputs']),{'json','junit','markdown'})
        summary=build_report(self.profile,self.root)
        self.assertEqual(summary['scope']['legacy_inventory_count'], len(current_manifest('owned-local-trusted-team-v1')['legacy_inventory']))
        self.assertEqual(summary['scope']['full_release_status'],'NOT_QUALIFIED')
        self.assertEqual(summary['reports']['expected_assertion_count'],58)
        self.assertEqual(summary['cases'][0]['resource_disposition'],'RETAINED_LOCAL_DATABASE')
        self.assertTrue(all(item['status']=='PASS' for item in summary['requirements']))
        junit=ET.fromstring(render_junit(summary));self.assertEqual(junit.get('name'),'bcs-e2e-trusted-team')
        self.assertIsNone(junit.find('testcase/skipped'))
        self.assertEqual(junit.find("properties/property[@name='expected_assertion_count']").get('value'),'58')
        self.assertEqual(junit.find("properties/property[@name='requirement.FRIEND.cli-accept']").get('value'),'PASS')
        self.assertIn('| FRIEND.cli-accept | PASS |',render_markdown(summary))
        self.assertIn('relationships/history retained',render_markdown(summary))
        with contextlib.redirect_stdout(io.StringIO()) as output,patch('bcs_e2e.cli.ReadClient') as network:
            rc=main(['report','--profile',str(self.root/'profile.json'),'--state-dir',str(self.root),'--format','all'])
        self.assertEqual(rc,0,output.getvalue());network.assert_not_called()
        with self.assertRaises(RunStateError):self.publish()
        self.assertEqual(build_report(self.profile,self.root)['status'],'LOCAL_SUITE_PASS')
        self.assertFalse((self.root/'trust-publication-failure.json').exists())

    def test_missing_completion_partial_outputs_and_old_context_never_upgrade_to_pass(self):
        self.assertEqual(build_report(self.profile,self.root)['status'],'BLOCKED')
        original=read(self.root/'trust-run-context.json')
        old={**original,'contract':'owned-local-trusted-run-context-v1'};write(self.root/'trust-run-context.json',old)
        self.run['run_context_digest']=fingerprint(old);write(self.root/'run.json',self.run)
        with self.assertRaises(RunStateError):build_report(self.profile,self.root)

    def test_nonpassing_assertions_failure_and_cancellation_remain_nonpassing(self):
        self.journal['assertions'][0]['status']='FAIL';self.journal['status']='BLOCKED';self.journal['functional']='FAIL'
        write(self.root/'local-trust-api.json',self.journal)
        result=self.publish(RuntimeError('private-error'))
        self.assertEqual(result['status'],'BLOCKED')
        self.assertEqual(result['summary']['cases'][0]['status'],'FAIL')
        self.assertIsNotNone(ET.fromstring(render_junit(result['summary'])).find('testcase/failure'))
        self.assertNotIn('private-error',json.dumps(result))

    def test_unknown_cli_child_survives_even_when_the_journal_could_not_record_it(self):
        failure=RuntimeError('private');failure.child_shutdown_status='UNCONFIRMED'
        result=self.publish(failure)
        self.assertEqual(result['summary']['cases'][0]['execution_status'],'UNCONFIRMED')
        self.assertEqual(read(self.root/REPORT_NAME)['child_shutdown_status'],'UNCONFIRMED')
        self.assertEqual(result['status'],'BLOCKED')

    def test_visibility_failure_does_not_erase_independent_functional_success(self):
        self.journal['assertions']=[row for row in self.journal['assertions'] if '-final-' not in row['id']]
        self.journal['status']='BLOCKED';self.journal['visibility_restoration']='UNCONFIRMED'
        write(self.root/'local-trust-api.json',self.journal)
        summary=self.publish(RuntimeError('restore failed'))['summary']
        self.assertEqual(summary['cases'][0]['functional'],'PASS')
        self.assertEqual(summary['cases'][0]['cleanup'],'BLOCKED')
        self.assertEqual(summary['status'],'BLOCKED')

    def test_source_change_and_new_integrity_marker_invalidate_saved_completion(self):
        self.publish()
        original=(self.root/'local-trust-api.json').read_bytes()
        self.journal['functional']='FAIL';write(self.root/'local-trust-api.json',self.journal)
        self.assertEqual(build_report(self.profile,self.root)['status'],'BLOCKED')
        (self.root/'local-trust-api.json').write_bytes(original)
        write(self.root/'local-trust-api-integrity.json',{'schema_version':1,'status':'BLOCKED'})
        self.assertEqual(build_report(self.profile,self.root)['status'],'BLOCKED')

    def test_missing_assertions_cannot_be_qualified_from_saved_pass_status(self):
        self.journal['assertions'].pop();write(self.root/'local-trust-api.json',self.journal)
        result=self.publish()
        self.assertEqual(result['status'],'BLOCKED')
        self.assertEqual(result['summary']['cases'][0]['evidence_status'],'INCOMPLETE')

    def test_publication_failure_leaves_no_completion_and_never_retries(self):
        with patch('bcs_e2e.reporting.write_reports',side_effect=OSError('private-output')):
            with self.assertRaises(RunStateError):self.publish()
        self.assertFalse((self.root/'trust-completion.json').exists())
        self.assertEqual(build_report(self.profile,self.root)['status'],'BLOCKED')
        with self.assertRaises(RunStateError):self.publish()

    def test_source_change_between_projection_and_publication_is_rejected(self):
        from bcs_e2e.trust_reporting import _case
        def changed(*args,**kwargs):
            value=_case(*args,**kwargs)
            self.journal['functional']='FAIL';write(self.root/'local-trust-api.json',self.journal)
            return value
        with patch('bcs_e2e.trust_reporting._case',side_effect=changed),self.assertRaises(RunStateError):self.publish()
        self.assertFalse((self.root/'trust-completion.json').exists())
        self.assertFalse((self.root/REPORT_NAME).exists())

    def test_failure_after_completion_write_preserves_marker_but_vetoes_pass(self):
        from bcs_e2e.trust_reporting import _exclusive
        def fail_after(path,value):
            _exclusive(path,value)
            if path.name=='trust-completion.json':raise OSError('private-late-seal-error')
        with patch('bcs_e2e.trust_reporting._exclusive',side_effect=fail_after),self.assertRaises(RunStateError) as failure:
            self.publish()
        self.assertNotIn('private-late-seal-error',str(failure.exception))
        self.assertTrue((self.root/'trust-completion.json').exists())
        self.assertTrue((self.root/'trust-publication-failure.json').exists())
        self.assertEqual(build_report(self.profile,self.root)['status'],'BLOCKED')

    def test_publication_veto_created_during_projection_cannot_return_pass(self):
        self.publish()
        from bcs_e2e.reporting import _validate_case
        def veto(*args,**kwargs):
            issues=_validate_case(*args,**kwargs)
            write(self.root/'trust-publication-failure.json',{'schema_version':1,'status':'BLOCKED'})
            return issues
        with patch('bcs_e2e.reporting._validate_case',side_effect=veto):summary=build_report(self.profile,self.root)
        self.assertEqual(summary['status'],'BLOCKED')
        self.assertEqual(summary['integrity']['status'],'BLOCKED')

    def test_case_replacement_after_exclusive_write_cannot_be_adopted_into_seal(self):
        from bcs_e2e.trust_reporting import _exclusive
        def replace(path,value):
            _exclusive(path,value)
            if path.name==REPORT_NAME:
                changed=read(path);changed['assertions'].append({'id':CASE_ID+'.injected','status':'PASS'})
                write(path,changed)
        with patch('bcs_e2e.trust_reporting._exclusive',side_effect=replace),self.assertRaises(RunStateError):self.publish()
        self.assertFalse((self.root/'trust-completion.json').exists())
        self.assertEqual(read(self.root/REPORT_NAME)['assertions'][-1]['id'],CASE_ID+'.injected')

    def test_report_before_completion_does_not_create_reserved_outputs_or_poison_publisher(self):
        with contextlib.redirect_stdout(io.StringIO()) as output:
            rc=main(['report','--profile',str(self.root/'profile.json'),'--state-dir',str(self.root),'--format','all'])
        self.assertEqual(rc,2);self.assertEqual(json.loads(output.getvalue())['outputs'],{})
        self.assertFalse((self.root/'reports').exists())
        with contextlib.redirect_stdout(io.StringIO()):
            rc=main(['report','--profile',str(self.root/'profile.json'),'--state-dir',str(self.root),
                     '--output-dir',str(self.root/'reports'),'--format','all'])
        self.assertEqual(rc,2);self.assertFalse((self.root/'reports').exists())
        self.assertEqual(self.publish()['status'],'LOCAL_SUITE_PASS')

    def test_cancelled_without_readable_assertions_preserves_cancellation_in_all_formats(self):
        for unreadable in (False,True):
            # Independent fixture per cancellation publication, never replay a directory.
            with self.subTest(unreadable=unreadable):
                if unreadable:self.setUp()
                if unreadable:(self.root/'local-trust-api.json').write_text('unreadable')
                else:(self.root/'local-trust-api.json').unlink()
                result=self.publish(KeyboardInterrupt())
                self.assertEqual(result['status'],'BLOCKED')
                self.assertEqual(result['summary']['cases'][0]['status'],'CANCELLED')
                self.assertEqual(read(self.root/REPORT_NAME)['status'],'CANCELLED')
                self.assertIn('CANCELLED',render_markdown(result['summary']))
                skipped=ET.fromstring(render_junit(result['summary'])).find('testcase/skipped')
                self.assertIn('CANCELLED',skipped.get('message'))

    def test_late_completion_error_and_failed_negative_notice_remain_fenced(self):
        from bcs_e2e.trust_reporting import _exclusive
        def fail_storage(path,value):
            if path.name=='trust-publication-failure.json':raise OSError('cannot allocate negative notice')
            _exclusive(path,value)
            if path.name=='trust-completion.json':raise OSError('late completion fsync failure')
        with patch('bcs_e2e.trust_reporting._exclusive',side_effect=fail_storage),self.assertRaises(RunStateError):
            self.publish()
        self.assertTrue((self.root/'trust-completion.json').exists())
        self.assertTrue((self.root/'trust-publication-pending.json').exists())
        self.assertFalse((self.root/'trust-publication-failure.json').exists())
        self.assertEqual(build_report(self.profile,self.root)['status'],'BLOCKED')

    def test_pending_fence_prevents_early_reader_after_completion_bytes_are_written(self):
        from bcs_e2e.trust_reporting import _exclusive
        observed=[]
        def inspect(path,value):
            _exclusive(path,value)
            if path.name=='trust-completion.json':
                observed.append(build_report(self.profile,self.root)['status'])
        with patch('bcs_e2e.trust_reporting._exclusive',side_effect=inspect):result=self.publish()
        self.assertEqual(observed,['BLOCKED'])
        self.assertEqual(result['status'],'LOCAL_SUITE_PASS')
        self.assertFalse((self.root/'trust-publication-pending.json').exists())
        self.assertEqual(build_report(self.profile,self.root)['status'],'LOCAL_SUITE_PASS')

    def test_failed_initial_pending_fence_prevents_case_and_completion_creation(self):
        from bcs_e2e.trust_reporting import _exclusive
        def fail(path,value):
            if path.name=='trust-publication-pending.json':raise OSError('cannot persist pending')
            return _exclusive(path,value)
        with patch('bcs_e2e.trust_reporting._exclusive',side_effect=fail),self.assertRaises(RunStateError):self.publish()
        self.assertFalse((self.root/REPORT_NAME).exists())
        self.assertFalse((self.root/'trust-completion.json').exists())
        self.assertEqual(build_report(self.profile,self.root)['status'],'BLOCKED')

    def test_failed_pending_removal_and_failed_failure_notice_remain_nonpassing(self):
        from bcs_e2e.trust_reporting import _exclusive
        unlink=Path.unlink
        def fail_unlink(path,*args,**kwargs):
            if path.name=='trust-publication-pending.json':raise OSError('cannot commit')
            return unlink(path,*args,**kwargs)
        def fail_notice(path,value):
            if path.name=='trust-publication-failure.json':raise OSError('cannot persist notice')
            return _exclusive(path,value)
        with patch.object(Path,'unlink',fail_unlink),patch('bcs_e2e.trust_reporting._exclusive',side_effect=fail_notice),self.assertRaises(RunStateError):
            self.publish()
        self.assertTrue((self.root/'trust-publication-pending.json').exists())
        self.assertTrue((self.root/'trust-completion.json').exists())
        self.assertEqual(build_report(self.profile,self.root)['status'],'BLOCKED')

    def test_cancellation_at_commit_retains_pending_and_canonical_cancelled_notice(self):
        unlink=Path.unlink
        def cancel(path,*args,**kwargs):
            if path.name=='trust-publication-pending.json':raise KeyboardInterrupt()
            return unlink(path,*args,**kwargs)
        with patch.object(Path,'unlink',cancel),self.assertRaises(KeyboardInterrupt):self.publish()
        self.assertTrue((self.root/'trust-publication-pending.json').exists())
        self.assertEqual(read(self.root/'trust-publication-failure.json')['status'],'CANCELLED')
        self.assertEqual(build_report(self.profile,self.root)['status'],'BLOCKED')

    def test_pending_mutation_before_seal_verification_cannot_commit(self):
        from bcs_e2e.trust_reporting import _exclusive
        def change(path,value):
            _exclusive(path,value)
            if path.name=='trust-completion.json':
                write(self.root/'trust-publication-pending.json',{'schema_version':1,'status':'changed'})
        with patch('bcs_e2e.trust_reporting._exclusive',side_effect=change),self.assertRaises(RunStateError):self.publish()
        self.assertEqual(read(self.root/'trust-publication-pending.json')['status'],'changed')
        self.assertEqual(build_report(self.profile,self.root)['status'],'BLOCKED')
