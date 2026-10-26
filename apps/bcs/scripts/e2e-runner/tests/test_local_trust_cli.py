"""Actual product CLI against owned BCS, including committed-but-lost outcomes."""
import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bcs_e2e.cli import main
from bcs_e2e.local_trust_cli import OwnedLocalTrustCli, LocalTrustCliError
from bcs_e2e.local_cli_exchange import LocalCliExchange
from bcs_e2e.bounded_process import capture
from bcs_e2e.run_state import read, write

BINARY=os.environ.get('BCS_E2E_TEST_BINARY','')
DIGEST=os.environ.get('BCS_E2E_TEST_BINARY_SHA256','')
CLI=os.environ.get('BCS_E2E_TEST_CLI_BINARY','')
CLI_DIGEST=os.environ.get('BCS_E2E_TEST_CLI_BINARY_SHA256','')


class TrustCliAdmissionTests(unittest.TestCase):
    def test_partial_cli_selection_denied_before_runtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            args=['local-trust-run','--state-dir',str(Path(tmp)/'new'), '--run-id','cli-test',
                  '--confirm-run-id','cli-test','--binary','/no/binary','--binary-sha256','a'*64,
                  '--allow-local-processes','--allow-fixture-write','--allow-write']
            for extra in (['--cli-binary','/no/cli'], ['--cli-binary-sha256','a'*64]):
                with patch('bcs_e2e.cli.local_runtime_smoke') as runtime, contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(main(args+extra),2)
                    runtime.assert_not_called()


@unittest.skipUnless(BINARY and DIGEST and CLI and CLI_DIGEST and shutil.which('lsof'),'approved product BCS and CLI binaries required')
class TrustCliLiveTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name)/'runtime'
        self.args=['local-trust-run','--state-dir',str(self.root),'--binary',BINARY,'--binary-sha256',DIGEST,
                   '--cli-binary',CLI,'--cli-binary-sha256',CLI_DIGEST,
                   '--run-id','trust-cli-live','--confirm-run-id','trust-cli-live',
                   '--allow-local-processes','--allow-fixture-write','--allow-write']

    def execute(self):
        with contextlib.redirect_stdout(io.StringIO()) as output:
            rc=main(self.args)
        return rc,json.loads(output.getvalue())

    def assert_closed(self):
        self.assertEqual(read(self.root/'owned-bcs.json')['status'],'STOPPED')
        for context in self.root.glob('trust-cli-*/bot/.bcs/session.json'):
            self.assertEqual(read(context)['token'],'')
            self.assertEqual(context.stat().st_mode & 0o777,0o600)
        import threading
        self.assertFalse(any(t.name=='e2e-local-cli-exchange' for t in threading.enumerate()))

    def journal(self):
        return read(self.root/'local-trust-api.json')

    def test_actual_four_cli_leaves_use_distinct_roles_and_exact_stored_relationship(self):
        rc,result=self.execute()
        self.assertEqual(rc,0,result)
        self.assertEqual(result['status'],'LOCAL_TRUST_API_CLI_PASS')
        case=self.journal()
        self.assertEqual(case['cli_friendship'],'PASS')
        self.assertEqual(case['full_story_status'],'NOT_MIGRATED')
        self.assertEqual(case['visibility_restoration'],'PASS')
        self.assertEqual([op['operation'] for op in case['cli_operations']],['request','requests','accept','list'])
        self.assertEqual([op['role'] for op in case['cli_operations']],['CEO','PM','PM','CEO'])
        self.assertEqual([op['http_status'] for op in case['cli_operations']],[201,200,200,200])
        self.assertEqual(len({op['context_dir'] for op in case['cli_operations']}),4)
        self.assertTrue(all(op['submitted_request_id']==op['response_header_request_id'] for op in case['cli_operations']))
        self.assertTrue(all(op['status']=='ACKNOWLEDGED' and op['credential_cleanup']=='CLEARED'
                            and op['resource_binding']=='BOUND' for op in case['cli_operations']))
        self.assertEqual(len(case['requests']),4)
        registration=read(self.root/'trust-registration.json')
        self.assertEqual(case['manifest_digest'],registration['manifest_digest'])
        self.assertEqual(case['executor_digest'],registration['executor_digest'])
        self.assertEqual(case['case_version'],1)
        from bcs_e2e.profile import load, fingerprint
        profile=load(self.root/'profile.json')
        context=read(self.root/'trust-run-context.json')
        run=read(self.root/'run.json')
        self.assertEqual(profile['schema_version'],2)
        self.assertEqual(set(profile['actors']),{'CEO','ENG','PM','QA','CS'})
        self.assertEqual(case['profile_digest'],fingerprint(profile))
        self.assertEqual(case['run_context_digest'],fingerprint(context))
        self.assertEqual(run['run_context_digest'],case['run_context_digest'])
        self.assertEqual(run['status'],'CONTEXT_BOUND')
        self.assertEqual(run['canonical_reporting'],'COMPLETION_REQUIRED')
        self.assertFalse(run['ready_to_execute'])
        canonical=result['canonical_report']
        self.assertEqual(canonical['status'],'LOCAL_SUITE_PASS')
        self.assertEqual(canonical['summary']['reports']['expected_assertion_count'],58)
        self.assertEqual(set(canonical['outputs']),{'json','junit','markdown'})
        self.assertTrue((self.root/'trust-completion.json').exists())
        self.assertEqual(read(self.root/'reports/summary.json')['status'],'LOCAL_SUITE_PASS')
        self.assertEqual(case['expected_assertion_count'],58)
        self.assertEqual(len(case['requirement_results']),5)
        self.assertTrue(all(row['status']=='PASS' for row in case['requirement_results']))
        self.assertTrue(set(case['required_assertions'])<={a['id'] for a in case['assertions']})
        with sqlite3.connect((self.root/'bcs.sqlite').as_uri()+'?mode=ro',uri=True) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM bcs_friend_requests WHERE env='local'").fetchone()[0],4)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM bcs_friendships WHERE env='local'").fetchone()[0],3)
        self.assert_closed()

    def test_registration_drift_before_fixtures_blocks_without_creating_actors(self):
        from bcs_e2e.trust_registration import TrustRegistration
        original=TrustRegistration.attach
        modified=[]
        def drift(registration,runtime):
            original(registration,runtime)
            path=runtime.root/'trust-registration.json'
            value=read(path);value['case_version']=True;write(path,value)
            modified.append(path.read_bytes())
        with patch.object(TrustRegistration,'attach',drift),patch('bcs_e2e.local_fixtures.OwnedLocalFixtures.apply') as apply:
            rc,result=self.execute()
        self.assertEqual(rc,2,result);apply.assert_not_called()
        self.assertFalse((self.root/'local-fixtures.json').exists())
        self.assertEqual((self.root/'trust-registration.json').read_bytes(),modified[0])
        self.assert_closed()

    def test_frozen_manifest_drift_during_business_prevents_further_writes(self):
        from bcs_e2e.local_trust_api import OwnedLocalTrustApi
        original=OwnedLocalTrustApi._mutate
        def drift(case,role,operation,**kwargs):
            value=original(case,role,operation,**kwargs)
            if operation=='friend.request':
                path=case.runtime.root/'manifest.json';manifest=read(path)
                manifest['cases'][0]['assertions'].pop();write(path,manifest)
            return value
        with patch.object(OwnedLocalTrustApi,'_mutate',drift):rc,result=self.execute()
        self.assertEqual(rc,2,result)
        journal=self.journal()
        self.assertEqual(len([op for op in journal['operations'] if op['operation']=='friend.request']),1)
        self.assertEqual(journal['cli_operations'],[])
        self.assertEqual(journal['visibility_restoration'],'UNCONFIRMED')
        self.assertEqual(len(read(self.root/'manifest.json')['cases'][0]['assertions']),57)
        self.assert_closed()

    def test_missing_registered_storage_target_assertion_cannot_qualify(self):
        from bcs_e2e.local_trust_api import OwnedLocalTrustApi
        original=OwnedLocalTrustApi._visibility_stored
        def omit(case,role,expected,phase):
            if phase=='target' and role=='ENG':return
            return original(case,role,expected,phase)
        with patch.object(OwnedLocalTrustApi,'_visibility_stored',omit):rc,result=self.execute()
        self.assertEqual(rc,2,result)
        self.assertEqual(self.journal()['status'],'BLOCKED')
        self.assertEqual(self.journal()['expected_assertion_count'],58)
        self.assertNotEqual(self.journal()['cli_friendship'],'PASS')
        self.assert_closed()

    def test_reduced_in_memory_denominator_with_missing_assertion_cannot_pass(self):
        from bcs_e2e.local_trust_api import OwnedLocalTrustApi
        original=OwnedLocalTrustApi._qualify
        def reduce(case,originals):
            omitted=case.registration.manifest['cases'][0]['assertions'].pop()
            case.state['assertions']=[row for row in case.state['assertions'] if row['id']!=omitted]
            return original(case,originals)
        with patch.object(OwnedLocalTrustApi,'_qualify',reduce):rc,result=self.execute()
        self.assertEqual(rc,2,result)
        self.assertEqual(self.journal()['status'],'BLOCKED')
        self.assertNotEqual(self.journal()['cli_friendship'],'PASS')
        self.assertEqual(len(read(self.root/'manifest.json')['cases'][0]['assertions']),58)
        self.assert_closed()

    def test_context_is_frozen_before_business_and_cannot_rebind(self):
        from bcs_e2e.local_trust_api import OwnedLocalTrustApi
        from bcs_e2e.run_state import RunStateError
        original=OwnedLocalTrustApi.run
        def inspect(case):
            self.assertFalse((self.root/'local-trust-api.json').exists())
            before={name:(self.root/name).read_bytes() for name in ('profile.json','trust-run-context.json','run.json')}
            with self.assertRaises(RunStateError):case.registration.bind_context(case.identities)
            self.assertEqual(before,{name:(self.root/name).read_bytes() for name in before})
            return original(case)
        with patch.object(OwnedLocalTrustApi,'run',inspect):rc,result=self.execute()
        self.assertEqual(rc,0,result);self.assert_closed()

    def test_profile_drift_after_ack_stops_later_writes_and_preserves_changed_file(self):
        from bcs_e2e.local_trust_api import OwnedLocalTrustApi
        from bcs_e2e.profile import load
        original=OwnedLocalTrustApi._mutate
        changed=[]
        def drift(case,role,operation,**kwargs):
            value=original(case,role,operation,**kwargs)
            if operation=='friend.request':
                path=self.root/'profile.json';profile=load(path)
                profile['actors']['CS']['bot_id']='external-role';write(path,profile)
                changed.append(path.read_bytes())
            return value
        with patch.object(OwnedLocalTrustApi,'_mutate',drift):rc,result=self.execute()
        self.assertEqual(rc,2,result)
        self.assertEqual((self.root/'profile.json').read_bytes(),changed[0])
        self.assertEqual(len([op for op in self.journal()['operations'] if op['operation']=='friend.request']),1)
        self.assertEqual(self.journal()['cli_operations'],[])
        self.assert_closed()

    def test_context_publication_failure_prevents_business_mutation_without_rebinding(self):
        from bcs_e2e.trust_context import _exclusive
        def fail(path,value):
            if path.name=='trust-run-context.json':raise OSError('private-context-write')
            return _exclusive(path,value)
        with patch('bcs_e2e.trust_context._exclusive',side_effect=fail),patch('bcs_e2e.local_trust_api.OwnedLocalTrustApi.run') as business:
            rc,result=self.execute()
        self.assertEqual(rc,2,result);business.assert_not_called()
        self.assertNotIn('private-context-write',json.dumps(result))
        self.assertTrue((self.root/'profile.json').exists())
        self.assertFalse((self.root/'run.json').exists())
        self.assert_closed()

    def test_run_context_drift_before_business_cannot_grant_execution(self):
        from bcs_e2e.trust_registration import TrustRegistration
        original=TrustRegistration.bind_context
        def drift(registration,identities):
            original(registration,identities)
            path=self.root/'run.json';run=read(path);run['status']='COMPLETED';write(path,run)
        with patch.object(TrustRegistration,'bind_context',drift):rc,result=self.execute()
        self.assertEqual(rc,2,result)
        self.assertFalse((self.root/'local-trust-api.json').exists())
        self.assertEqual(read(self.root/'run.json')['status'],'COMPLETED')
        self.assert_closed()

    def test_cancel_during_context_publication_retains_partial_files_without_business(self):
        from bcs_e2e.trust_context import _exclusive
        def cancel(path,value):
            if path.name=='trust-run-context.json':raise KeyboardInterrupt()
            return _exclusive(path,value)
        with patch('bcs_e2e.trust_context._exclusive',side_effect=cancel),patch('bcs_e2e.local_trust_api.OwnedLocalTrustApi.run') as business:
            rc,result=self.execute()
        self.assertEqual(rc,130,result);business.assert_not_called()
        self.assertEqual(result['status'],'CANCELLED')
        self.assertTrue((self.root/'profile.json').exists())
        self.assertFalse((self.root/'run.json').exists())
        self.assertFalse((self.root/'local-trust-api.json').exists())
        self.assert_closed()

    def test_cancellation_during_initial_baseline_keeps_canonical_cancelled_with_no_assertions(self):
        from bcs_e2e.local_role_identity import OwnedLocalRoleIdentity
        from bcs_e2e.trust_reporting import REPORT_NAME
        with patch.object(OwnedLocalRoleIdentity,'trust_baseline',side_effect=KeyboardInterrupt()):
            rc,result=self.execute()
        self.assertEqual(rc,130,result)
        self.assertFalse((self.root/'local-trust-api.json').exists())
        case=read(self.root/REPORT_NAME);summary=read(self.root/'reports/summary.json')
        self.assertEqual(case['status'],'CANCELLED');self.assertEqual(case['assertions'],[])
        self.assertEqual(summary['cases'][0]['status'],'CANCELLED')
        self.assertEqual(summary['status'],'BLOCKED')
        self.assertEqual(summary['integrity']['status'],'BLOCKED')
        self.assertTrue((self.root/'trust-completion.json').exists())
        self.assert_closed()

    def test_successful_business_with_failed_report_publication_cannot_return_success(self):
        with patch('bcs_e2e.reporting.write_reports',side_effect=OSError('private-render-failure')):
            rc,result=self.execute()
        self.assertEqual(rc,2,result)
        self.assertNotIn('private-render-failure',json.dumps(result))
        self.assertEqual(self.journal()['status'],'LOCAL_TRUST_API_CLI_PASS')
        self.assertFalse((self.root/'trust-completion.json').exists())
        self.assertTrue((self.root/'trust-publication-failure.json').exists())
        self.assert_closed()

    def test_early_report_during_real_business_does_not_block_eventual_completion(self):
        from bcs_e2e.local_trust_api import OwnedLocalTrustApi
        original=OwnedLocalTrustApi._qualify
        def view(case,originals):
            with contextlib.redirect_stdout(io.StringIO()) as output:
                rc=main(['report','--profile',str(self.root/'profile.json'),'--state-dir',str(self.root),'--format','all'])
            self.assertEqual(rc,2);self.assertEqual(json.loads(output.getvalue())['outputs'],{})
            self.assertFalse((self.root/'reports').exists())
            return original(case,originals)
        with patch.object(OwnedLocalTrustApi,'_qualify',view):rc,result=self.execute()
        self.assertEqual(rc,0,result)
        self.assertEqual(result['canonical_report']['status'],'LOCAL_SUITE_PASS')
        self.assert_closed()

    def test_completion_and_failure_notice_write_errors_cannot_leave_offline_pass(self):
        from bcs_e2e.trust_reporting import _exclusive
        from bcs_e2e.profile import load
        from bcs_e2e.reporting import build_report
        def fail(path,value):
            if path.name=='trust-publication-failure.json':raise OSError('private-negative-notice-error')
            _exclusive(path,value)
            if path.name=='trust-completion.json':raise OSError('private-completion-sync-error')
        with patch('bcs_e2e.trust_reporting._exclusive',side_effect=fail):rc,result=self.execute()
        self.assertEqual(rc,2,result)
        self.assertNotIn('private-',json.dumps(result))
        self.assertEqual(self.journal()['status'],'LOCAL_TRUST_API_CLI_PASS')
        self.assertTrue((self.root/'trust-completion.json').exists())
        self.assertTrue((self.root/'trust-publication-pending.json').exists())
        self.assertFalse((self.root/'trust-publication-failure.json').exists())
        self.assertEqual(build_report(load(self.root/'profile.json'),self.root)['status'],'BLOCKED')
        self.assert_closed()

    def test_cli_commit_then_lost_stdout_never_adopts_or_retries(self):
        calls=[]
        def lost(*args,**kwargs):
            result=capture(*args,**kwargs);calls.append(result.returncode)
            return subprocess.CompletedProcess(result.args,0,b'invalid-json',b'')
        with patch('bcs_e2e.local_trust_cli.capture',side_effect=lost):rc,result=self.execute()
        self.assertEqual(rc,2,result);self.assertEqual(calls,[0])
        journal=self.journal()
        self.assertEqual(len(journal['cli_operations']),1)
        self.assertEqual(journal['cli_operations'][0]['status'],'UNKNOWN')
        self.assertEqual(journal['cli_operations'][0]['resource_binding'],'UNKNOWN')
        self.assertEqual(journal['visibility_restoration'],'UNCONFIRMED')
        self.assertEqual(len(journal['requests']),3)
        with sqlite3.connect(self.root/'bcs.sqlite') as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM bcs_friend_requests WHERE status='pending'").fetchone()[0],1)
        self.assert_closed()

    def test_cancellation_after_real_cli_commit_clears_context_and_stops(self):
        def cancel(*args,**kwargs):
            capture(*args,**kwargs)
            raise KeyboardInterrupt()
        with patch('bcs_e2e.local_trust_cli.capture',side_effect=cancel):rc,result=self.execute()
        self.assertEqual(rc,130,result)
        self.assertEqual(self.journal()['status'],'CANCELLED')
        self.assertEqual(len(self.journal()['cli_operations']),1)
        self.assert_closed()

    def test_failed_cli_intent_prevents_first_cli_process_and_write(self):
        def fail(path,value):
            if value.get('cli_operations'):
                raise OSError('private-intent-error')
            write(path,value)
        with patch('bcs_e2e.local_trust_api.write',side_effect=fail),patch('bcs_e2e.local_trust_cli.capture') as process:
            rc,result=self.execute()
        self.assertEqual(rc,2,result);process.assert_not_called()
        self.assertNotIn('private-intent-error',json.dumps(result))
        self.assert_closed()

    def test_wrong_route_is_rejected_before_upstream(self):
        original=LocalCliExchange.__enter__
        def wrong(exchange):
            exchange.event['path']='/not-admitted'
            return original(exchange)
        with patch.object(LocalCliExchange,'__enter__',wrong):rc,result=self.execute()
        self.assertEqual(rc,2,result)
        self.assertNotIn('http_status',self.journal()['cli_operations'][0])
        self.assert_closed()

    def test_duplicate_pending_wire_200_does_not_qualify_as_created_201(self):
        original=OwnedLocalTrustCli.execute
        def duplicate(adapter,case,operation,**kwargs):
            if operation=='request':
                from urllib.request import Request
                request=Request(case.identities.origin+'/friends/request',method='POST',
                                data=json.dumps({'to_bot':case._id('PM')}).encode(),
                                headers={'Content-Type':'application/json',
                                         'Authorization':'Bearer '+case.identities._entries['CEO'][1]})
                with case.identities.opener.open(request,timeout=5) as response:
                    self.assertEqual(response.status,201)
            return original(adapter,case,operation,**kwargs)
        with patch.object(OwnedLocalTrustCli,'execute',duplicate):rc,result=self.execute()
        self.assertEqual(rc,2,result)
        event=self.journal()['cli_operations'][0]
        self.assertEqual(event['http_status'],200);self.assertEqual(event['status'],'UNKNOWN')
        self.assertEqual(len(self.journal()['cli_operations']),1)
        self.assert_closed()

    def test_missing_cli_business_steps_cannot_qualify(self):
        with patch('bcs_e2e.trust_api_steps.execute_cli_friend_steps') as steps:
            rc,result=self.execute()
        self.assertEqual(rc,2,result);steps.assert_called_once()
        self.assertNotEqual(self.journal()['cli_friendship'],'PASS')
        self.assert_closed()

    def test_changed_context_preserved_and_cleanup_marked_unconfirmed(self):
        changed=[]
        def drift(*args,**kwargs):
            result=capture(*args,**kwargs)
            context=Path(kwargs['environment']['BOT_DATA_DIR'])/'.bcs'/'session.json'
            value=read(context);value['token']='external-replacement';write(context,value)
            changed.append((context,context.read_bytes()))
            return result
        with patch('bcs_e2e.local_trust_cli.capture',side_effect=drift):rc,result=self.execute()
        self.assertEqual(rc,2,result)
        self.assertEqual(len(changed),1)
        self.assertEqual(changed[0][0].read_bytes(),changed[0][1])
        self.assertEqual(self.journal()['cli_operations'][0]['credential_cleanup'],'UNCONFIRMED')
        self.assertEqual(self.journal()['cli_operations'][0]['status'],'UNKNOWN')
        self.assertEqual(read(self.root/'owned-bcs.json')['status'],'STOPPED')

    def test_cancel_with_failed_context_cleanup_keeps_cancelled_result(self):
        def cancel(*args,**kwargs):
            capture(*args,**kwargs);raise KeyboardInterrupt()
        def fail(path,value):
            if value.get('token')=='':raise OSError('private-clear-failure')
            write(path,value)
        with patch('bcs_e2e.local_trust_cli.capture',side_effect=cancel), patch('bcs_e2e.local_trust_cli.write',side_effect=fail):
            rc,result=self.execute()
        self.assertEqual(rc,130,result)
        self.assertEqual(self.journal()['status'],'CANCELLED')
        self.assertEqual(self.journal()['cli_operations'][0]['credential_cleanup'],'UNCONFIRMED')
        self.assertNotIn('private-clear-failure',json.dumps(result))
        self.assertEqual(read(self.root/'owned-bcs.json')['status'],'STOPPED')

    def test_unconfirmed_cli_child_shutdown_is_not_lost_in_wrapping(self):
        from bcs_e2e.bounded_process import ProcessCaptureError
        error=ProcessCaptureError('child termination uncertain');error.child_shutdown_status='UNCONFIRMED'
        with patch('bcs_e2e.local_trust_cli.capture',side_effect=error):rc,result=self.execute()
        self.assertEqual(rc,2,result)
        self.assertEqual(result['child_shutdown_status'],'UNCONFIRMED')
        self.assertEqual(self.journal()['child_shutdown_status'],'UNCONFIRMED')
        self.assertEqual(self.journal()['cli_operations'][0]['child_shutdown_status'],'UNCONFIRMED')
        self.assert_closed()

    def test_numeric_cli_success_cannot_equal_boolean_success(self):
        def numeric(*args,**kwargs):
            result=capture(*args,**kwargs)
            if 'accept' in args[0]:
                return subprocess.CompletedProcess(result.args,0,b'{"success":1}',b'')
            return result
        with patch('bcs_e2e.local_trust_cli.capture',side_effect=numeric):rc,result=self.execute()
        self.assertEqual(rc,2,result)
        self.assertEqual(len(self.journal()['cli_operations']),3)
        self.assertEqual(self.journal()['cli_operations'][-1]['status'],'UNKNOWN')
        self.assert_closed()

    def test_deleted_context_does_not_pass_as_successfully_cleared(self):
        def remove(*args,**kwargs):
            result=capture(*args,**kwargs)
            context=Path(kwargs['environment']['BOT_DATA_DIR'])/'.bcs'/'session.json'
            context.unlink()
            return result
        with patch('bcs_e2e.local_trust_cli.capture',side_effect=remove):rc,result=self.execute()
        self.assertEqual(rc,2,result)
        self.assertEqual(self.journal()['cli_operations'][0]['credential_cleanup'],'UNCONFIRMED')
        self.assert_closed()

    def test_shutdown_uncertainty_survives_failed_failure_publication(self):
        from bcs_e2e.bounded_process import ProcessCaptureError
        error=ProcessCaptureError('child termination uncertain');error.child_shutdown_status='UNCONFIRMED'
        def fail(path,value):
            if any(op.get('child_shutdown_status')=='UNCONFIRMED' for op in value.get('cli_operations',[])):
                raise OSError('private-publication-failure')
            write(path,value)
        with patch('bcs_e2e.local_trust_cli.capture',side_effect=error), patch('bcs_e2e.local_trust_api.write',side_effect=fail):
            rc,result=self.execute()
        self.assertEqual(rc,2,result)
        self.assertEqual(result['child_shutdown_status'],'UNCONFIRMED')
        self.assertNotIn('private-publication-failure',json.dumps(result))
        self.assert_closed()

    def test_cli_replay_wrong_request_and_order_rejected_before_new_intent(self):
        original=OwnedLocalTrustCli.execute
        attempts=[]
        def check(adapter,case,operation,**kwargs):
            if operation=='accept':
                before=len(case.state['cli_operations'])
                with self.assertRaises(LocalTrustCliError):original(adapter,case,'request')
                with self.assertRaises(LocalTrustCliError):original(adapter,case,'accept',request_id='foreign-request')
                self.assertEqual(len(case.state['cli_operations']),before)
                attempts.append(True)
            return original(adapter,case,operation,**kwargs)
        with patch.object(OwnedLocalTrustCli,'execute',check):rc,result=self.execute()
        self.assertEqual(rc,0,result);self.assertEqual(attempts,[True]);self.assert_closed()


if __name__=='__main__':unittest.main()
