"""Retained subset shares real business steps but never auto-deletes or releases."""
import contextlib
import copy
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bcs_e2e.adapters import LocalMockAdapter
from bcs_e2e.cli import main
from bcs_e2e.effect_policy import validate_effect_policy
from bcs_e2e.inventory import current_manifest
from bcs_e2e.ledger import Ledger
from bcs_e2e.persistent_mock import PersistentLocalMockAdapter
from bcs_e2e.pool import PoolBusyError, PoolLease, pool_path, pool_scope
from bcs_e2e.prepare import prepare_run
from bcs_e2e.profile import ConfigError, fingerprint, load
from bcs_e2e.retained import SUITE, retained_run, run_retained_case
from bcs_e2e.run_state import RunStateError, read
from bcs_e2e.write_transport import WriteTransportError


class RetainedTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name); self.state = self.root / 'run'
        self.profile = load(ROOT / 'profiles/local.mock.json')
        registry = patch('bcs_e2e.pool.pool_root', return_value=self.root / 'registry')
        registry.start(); self.addCleanup(registry.stop)
        self.lock = pool_path(pool_scope(self.profile))
        self.run_id = 'retain-test'

    def prepare(self):
        prepare_run(self.profile, self.state, self.run_id, manifest=current_manifest(SUITE))

    def assert_reserved(self):
        self.assertTrue(self.lock.is_file())
        with self.assertRaises(PoolBusyError):
            PoolLease.acquire(self.lock, 'another', fingerprint(self.profile))

    def assert_no_delete(self):
        snapshot = read(self.state / 'local-mock.json')
        self.assertEqual(snapshot['deleted'], [])
        self.assertEqual(snapshot['deleted_sessions'], [])
        self.assertEqual(snapshot['deleted_files'], [])
        self.assert_reserved()

    def test_both_shared_slices_preserve_resources_and_do_not_quiesce(self):
        for case, count in [('RETAIN-GROUP-01', 2), ('RETAIN-SESSION-01', 3)]:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as d, \
                 patch('bcs_e2e.pool.pool_root', return_value=Path(d)/'registry'), \
                 patch.object(LocalMockAdapter, 'quiesce_execution', side_effect=AssertionError('no stop')), \
                 patch.object(PersistentLocalMockAdapter, 'group_delete', side_effect=AssertionError('no delete')), \
                 patch.object(PersistentLocalMockAdapter, 'session_delete', side_effect=AssertionError('no delete')):
                state=Path(d)/'run'
                result=retained_run(self.profile,state_dir=state,run_id=self.run_id,case_id=case,
                                    allow_write=True,confirm_run_id=self.run_id)
                self.assertEqual(result['functional'], 'PASS')
                self.assertEqual(result['status'], 'BLOCKED')
                self.assertEqual(result['execution_status'], 'UNCONFIRMED')
                self.assertEqual(result['cleanup'], 'RETAINED')
                self.assertEqual(result['pool_disposition'], 'QUARANTINED')
                self.assertEqual(read(state/'local-mock.json')['connected_sessions'], [])
                self.assertEqual(len(read(state/'retention.json')['resources']),count)
                self.assertEqual(read(state/'retention.json')['inventory_status'],'COMPLETE')
                self.assertFalse(read(state/'retention.json')['automatic_expiry'])
                self.assertTrue(pool_path(pool_scope(self.profile)).exists())
                self.assertTrue(result['excluded_assertions'])
                self.assertFalse(any('deleted' in a['id'] or 'cleaned' in a['id'] for a in result['assertions']))
                with Ledger(state/'run.sqlite',self.run_id,fingerprint(self.profile),read_only=True) as ledger:
                    self.assertTrue(all(r['state']=='live' for r in ledger.resource_snapshot()))
                    self.assertEqual(ledger.termination_states(), [])
                    self.assertTrue(any(e['state']=='QUEUED' for e in ledger.execution_effects()))

    def test_assertion_failure_retains_exact_resources_without_parent_delete(self):
        self.prepare()
        with patch.object(PersistentLocalMockAdapter,'group_read',side_effect=RuntimeError('private-error')):
            report=run_retained_case(self.profile,self.state,self.run_id,'RETAIN-GROUP-01')
        self.assertEqual(report['functional'],'FAIL')
        self.assertEqual(report['status'],'FAIL')
        self.assertNotIn('private-error', json.dumps(report))
        self.assert_no_delete()
        self.assertEqual(len(read(self.state/'retention.json')['resources']),2)

    def test_lost_create_response_retains_unknown_intents_without_retry(self):
        self.prepare()
        with patch.object(PersistentLocalMockAdapter,'group_create',side_effect=WriteTransportError('lost response')) as create:
            report=run_retained_case(self.profile,self.state,self.run_id,'RETAIN-GROUP-01')
        self.assertEqual(create.call_count,1)
        self.assertEqual(report['functional'],'FAIL')
        retention=read(self.state/'retention.json')
        self.assertEqual(retention['inventory_status'],'UNKNOWN_RESOURCES')
        self.assertTrue(all(r['resource_id'] is None for r in retention['resources']))
        self.assert_no_delete()

    def test_cancel_after_socket_handoff_closes_owned_socket_and_preserves_cancel(self):
        self.prepare()
        from bcs_e2e.correlation import CorrelatedEvidence
        original=CorrelatedEvidence.append
        def interrupted(evidence, item):
            if item.get('operation')=='session.ws.connect':
                raise KeyboardInterrupt()
            return original(evidence,item)
        with patch.object(CorrelatedEvidence,'append',interrupted):
            report=run_retained_case(self.profile,self.state,self.run_id,'RETAIN-SESSION-01')
        self.assertEqual(report['functional'],'CANCELLED')
        self.assertEqual(report['status'],'CANCELLED')
        self.assertEqual(report['socket_status'],'CLOSED')
        self.assertEqual(read(self.state/'local-mock.json')['connected_sessions'],[])
        self.assert_no_delete()

    def test_socket_close_failure_is_not_a_functional_pass(self):
        self.prepare()
        with patch.object(PersistentLocalMockAdapter,'session_ws_close',side_effect=RuntimeError('private')):
            report=run_retained_case(self.profile,self.state,self.run_id,'RETAIN-SESSION-01')
        self.assertEqual(report['functional'],'FAIL')
        self.assertEqual(report['socket_status'],'UNCONFIRMED')
        self.assert_no_delete()

    def test_repeat_or_next_case_cannot_resume_unknown_work(self):
        self.prepare()
        run_retained_case(self.profile,self.state,self.run_id,'RETAIN-GROUP-01')
        for case in ('RETAIN-GROUP-01','RETAIN-SESSION-01'):
            with patch('bcs_e2e.retained.adapter_for') as factory, self.assertRaises(ConfigError):
                run_retained_case(self.profile,self.state,self.run_id,case)
            factory.assert_not_called()
        self.assert_no_delete()

    def test_remote_is_blocked_before_directory_lease_or_adapter(self):
        for kind in ('gateway','bcs-direct'):
            p=copy.deepcopy(self.profile);p['entrypoint']['kind']=kind;p['environment']['tier']='preprod'
            with patch('bcs_e2e.retained.adapter_for') as factory, self.assertRaises(ConfigError):
                retained_run(p,state_dir=self.state,run_id=self.run_id,case_id='RETAIN-GROUP-01',
                             allow_write=True,confirm_run_id=self.run_id)
            factory.assert_not_called()
            self.assertFalse(self.state.exists());self.assertFalse(self.lock.exists())

    def test_inventory_forbids_delete_and_keeps_original_denominator(self):
        original=current_manifest(); retained=current_manifest(SUITE)
        self.assertEqual([c['id'] for c in original['cases']],['PRE-01','PRE-02','PRE-03'])
        self.assertEqual(len(retained['legacy_inventory']),len(original['legacy_inventory']))
        changed=copy.deepcopy(retained);changed['cases'][0]['effects'].append('delete:owned_group')
        with self.assertRaises(RunStateError):validate_effect_policy(changed)
        changed=copy.deepcopy(retained);changed['cases']=changed['cases'][:1]
        with self.assertRaises(RunStateError):validate_effect_policy(changed)
        self.assertEqual(current_manifest(),original)

    def test_cli_partial_result_is_nonzero_and_release_cannot_bypass(self):
        profile_path=self.root/'profile.json';profile_path.write_text(json.dumps(self.profile))
        argv=['retained-run','--profile',str(profile_path),'--state-dir',str(self.state),
              '--run-id',self.run_id,'--case','RETAIN-GROUP-01','--confirm-run-id',self.run_id]
        with contextlib.redirect_stdout(io.StringIO()):self.assertEqual(main(argv),2)
        self.assertFalse(self.state.exists())
        with contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main([*argv,'--allow-write']),2)
        self.assertEqual(json.loads(output.getvalue())['functional'],'PASS')
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(['release','--state-dir',str(self.state)]),2)
        self.assert_no_delete()


    def test_lost_lease_latches_authority_without_recreating_or_cleaning(self):
        self.prepare()
        original=PersistentLocalMockAdapter.group_read
        def lose_lease(adapter, group_id):
            result=original(adapter,group_id)
            self.lock.unlink()
            return result
        with patch.object(PersistentLocalMockAdapter,'group_read',lose_lease), \
             patch.object(PersistentLocalMockAdapter,'group_update') as update:
            report=run_retained_case(self.profile,self.state,self.run_id,'RETAIN-GROUP-01')
        update.assert_not_called()
        self.assertEqual(report['operation_authority'],'INVALIDATED')
        self.assertEqual(read(self.state/'run.json')['status'],'QUARANTINED')
        self.assertFalse(self.lock.exists())
        self.assertEqual(read(self.state/'local-mock.json')['deleted'],[])

    def test_false_socket_close_response_is_not_confirmed(self):
        self.prepare()
        with patch.object(PersistentLocalMockAdapter,'session_ws_close',return_value=({'closed':False},{})):
            report=run_retained_case(self.profile,self.state,self.run_id,'RETAIN-SESSION-01')
        self.assertEqual(report['functional'],'FAIL')
        self.assertEqual(report['socket_status'],'UNCONFIRMED')
        self.assert_no_delete()

    def test_real_sigterm_after_socket_handoff_seals_cancelled_retention(self):
        import subprocess
        profile_path=self.root/'profile.json';profile_path.write_text(json.dumps(self.profile))
        script = f"""
import os, signal, sys
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,{str(ROOT)!r})
from bcs_e2e.cli import main
from bcs_e2e.correlation import CorrelatedEvidence
original=CorrelatedEvidence.append
fired=False
def interrupt(evidence,item):
    global fired
    if item.get('operation')=='session.ws.connect' and not fired:
        fired=True
        os.kill(os.getpid(),signal.SIGTERM)
    return original(evidence,item)
with patch('bcs_e2e.pool.pool_root',return_value=Path({str(self.root/'registry')!r})), patch.object(CorrelatedEvidence,'append',interrupt):
    rc=main(['retained-run','--profile',{str(profile_path)!r},'--state-dir',{str(self.state)!r},
             '--run-id',{self.run_id!r},'--case','RETAIN-SESSION-01','--allow-write','--confirm-run-id',{self.run_id!r}])
sys.exit(rc)
"""
        result=subprocess.run([sys.executable,'-c',script],capture_output=True,text=True,timeout=15)
        self.assertEqual(result.returncode,130,result.stderr)
        report=json.loads(result.stdout)
        self.assertEqual(report['status'],'CANCELLED')
        self.assertEqual(report['socket_status'],'CLOSED')
        self.assertEqual(read(self.state/'run.json')['status'],'QUARANTINED')
        self.assertEqual(len(read(self.state/'retention.json')['resources']),3)
        self.assertEqual(read(self.state/'local-mock.json')['connected_sessions'],[])
        self.assert_no_delete()

    def test_existing_report_and_junit_never_promote_partial_functional_pass(self):
        from bcs_e2e.reporting import build_report, render_junit
        self.prepare()
        run_retained_case(self.profile,self.state,self.run_id,'RETAIN-GROUP-01')
        summary=build_report(self.profile,self.state)
        self.assertNotEqual(summary['status'],'PILOT_PASS')
        self.assertEqual(len(summary['cases']),2)
        self.assertEqual(summary['cases'][0]['functional'],'PASS')
        self.assertNotEqual(summary['cases'][0]['status'],'PASS')
        self.assertEqual(summary['cases'][1]['status'],'BLOCKED')
        self.assertEqual(summary['cases'][1]['functional'],'NOT_RUN')
        self.assertNotEqual(summary['scope']['full_release_status'],'QUALIFIED')
        import xml.etree.ElementTree as ET
        suite=ET.fromstring(render_junit(summary))
        self.assertEqual(len(suite.findall('testcase')),2)
        self.assertEqual(suite.attrib['skipped'],'2')
        self.assertTrue(all(case.find('skipped') is not None for case in suite.findall('testcase')))

    def test_persisted_connect_without_handoff_is_unknown_not_unopened(self):
        self.prepare()
        original=PersistentLocalMockAdapter.session_ws_connect
        def lost_response(adapter,*args):
            original(adapter,*args)
            raise WriteTransportError('response lost after persistence')
        with patch.object(PersistentLocalMockAdapter,'session_ws_connect',lost_response), \
             patch.object(PersistentLocalMockAdapter,'session_ws_close') as close:
            report=run_retained_case(self.profile,self.state,self.run_id,'RETAIN-SESSION-01')
        close.assert_not_called()  # No guessed ownership of a lost handoff.
        self.assertEqual(report['socket_status'],'UNCONFIRMED')
        self.assertEqual(report['functional'],'FAIL')
        self.assertEqual(len(read(self.state/'local-mock.json')['connected_sessions']),1)
        self.assert_no_delete()

    def test_first_sigterm_during_inventory_or_report_publication_is_deferred(self):
        import subprocess
        for stage in ('socket','inventory','report'):
            with self.subTest(stage=stage), tempfile.TemporaryDirectory() as d:
                root=Path(d);state=root/'run';profile=root/'profile.json'
                profile.write_text(json.dumps(self.profile))
                script=f"""
import os,signal,sys
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,{str(ROOT)!r})
from bcs_e2e.cli import main
from bcs_e2e.ledger import Ledger
from bcs_e2e.retained import write
from bcs_e2e.case_scope import CaseScopedAdapter
original_close=CaseScopedAdapter.session_ws_close
original=Ledger.resource_snapshot
fired=False
def snapshot(ledger):
    global fired
    if {stage!r}=='inventory' and not fired:
        fired=True
        os.kill(os.getpid(),signal.SIGTERM)
    return original(ledger)
def close_owned(adapter,session_id):
    global fired
    if {stage!r}=='socket' and not fired:
        fired=True
        os.kill(os.getpid(),signal.SIGTERM)
    return original_close(adapter,session_id)
def publish(path,value):
    global fired
    if {stage!r}=='report' and path.name.startswith('report-') and not fired:
        fired=True
        os.kill(os.getpid(),signal.SIGTERM)
    return write(path,value)
with patch('bcs_e2e.pool.pool_root',return_value=Path({str(root/'registry')!r})), patch.object(Ledger,'resource_snapshot',snapshot), patch('bcs_e2e.retained.write',publish), patch.object(CaseScopedAdapter,'session_ws_close',close_owned):
    rc=main(['retained-run','--profile',{str(profile)!r},'--state-dir',{str(state)!r},'--run-id','retain-finalize',
             '--case','RETAIN-SESSION-01','--allow-write','--confirm-run-id','retain-finalize'])
assert fired
sys.exit(rc)
"""
                result=subprocess.run([sys.executable,'-c',script],capture_output=True,text=True,timeout=15)
                self.assertEqual(result.returncode,130,result.stderr)
                report=json.loads(result.stdout)
                self.assertEqual(report['status'],'CANCELLED')
                self.assertEqual(report['functional'],'PASS')
                self.assertEqual(report['socket_status'],'CLOSED')
                self.assertEqual(read(state/'report-RETAIN-SESSION-01.json')['status'],'CANCELLED')
                self.assertEqual(read(state/'retention.json')['inventory_status'],'COMPLETE')
                snapshot_data=read(state/'local-mock.json')
                self.assertEqual(snapshot_data['connected_sessions'],[])
                self.assertEqual(snapshot_data['deleted'],[])
                self.assertEqual(snapshot_data['deleted_sessions'],[])
                self.assertEqual(read(state/'run.json')['status'],'QUARANTINED')
                self.assertEqual(len(list((root/'registry').glob('*.lock'))),1)
