"""Real owned-local friendship transitions and fail-closed durable intents."""
import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import URLError

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from bcs_e2e.cli import main
from bcs_e2e.local_role_identity import OwnedLocalRoleIdentity
from bcs_e2e.local_trust_api import OwnedLocalTrustApi, LocalTrustApiError
from bcs_e2e.run_state import read, write

BINARY=os.environ.get('BCS_E2E_TEST_BINARY','')
DIGEST=os.environ.get('BCS_E2E_TEST_BINARY_SHA256','')


class TrustCommandTests(unittest.TestCase):
    def test_permissions_and_existing_state_refuse_before_runtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=['local-trust-run','--state-dir',str(Path(tmp)/'new'), '--binary','/not/a/binary',
                  '--binary-sha256','a'*64,'--run-id','trust-test','--confirm-run-id','trust-test',
                  '--allow-local-processes','--allow-fixture-write','--allow-write']
            for missing in ('--allow-local-processes','--allow-fixture-write','--allow-write'):
                with contextlib.redirect_stdout(io.StringIO()),patch('bcs_e2e.cli.local_runtime_smoke') as runtime:
                    self.assertEqual(main([arg for arg in base if arg!=missing]),2)
                    runtime.assert_not_called()
            (Path(tmp)/'new').mkdir()
            with contextlib.redirect_stdout(io.StringIO()),patch('bcs_e2e.cli.local_runtime_smoke') as runtime:
                self.assertEqual(main(base),2);runtime.assert_not_called()


@unittest.skipUnless(BINARY and DIGEST and shutil.which('lsof'),'approved actual BCS binary required')
class LocalTrustApiLiveTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name)/'runtime'
        self.args=['local-trust-run','--state-dir',str(self.root),'--binary',BINARY,'--binary-sha256',DIGEST,
                   '--run-id','trust-api-live','--confirm-run-id','trust-api-live',
                   '--allow-local-processes','--allow-fixture-write','--allow-write']

    def execute(self):
        with contextlib.redirect_stdout(io.StringIO()) as output:
            rc=main(self.args)
        return rc,json.loads(output.getvalue())

    def assert_stopped(self):
        receipt=read(self.root/'owned-bcs.json')
        self.assertEqual(receipt['status'],'STOPPED')
        self.assertEqual(receipt['returncode'],0)

    def test_real_three_api_flows_restore_visibility_and_retain_exact_history(self):
        rc,result=self.execute()
        self.assertEqual(rc,0,result)
        self.assertEqual(result['status'],'LOCAL_TRUST_API_SLICE_PASS')
        case=result['trust_api']
        self.assertEqual(case['functional'],'PASS')
        self.assertEqual(case['visibility_restoration'],'PASS')
        self.assertEqual(case['cli_friendship'],'NOT_TESTED')
        self.assertEqual(case['full_story_status'],'NOT_MIGRATED')
        self.assertEqual(case['edge_permission_graph'],'NOT_VERIFIED')
        self.assertFalse(case['ready_to_execute'])
        self.assertEqual([r['status'] for r in case['requests']],['accepted','accepted','rejected'])
        self.assertEqual(len({r['request_id'] for r in case['requests']}),3)
        self.assertTrue(all(a['status']=='PASS' for a in case['assertions']))
        self.assertEqual(len({a['id'] for a in case['assertions']}), len(case['assertions']))
        self.assertTrue(set(case['required_assertions']) <= {a['id'] for a in case['assertions']})
        self.assertEqual(len(case['operations']),7)
        self.assertTrue(all(op['status']=='ACKNOWLEDGED' for op in case['operations']))
        creations=[op for op in case['operations'] if op['operation']=='friend.request']
        self.assertEqual([op['http_status'] for op in creations],[200,201,201])
        self.assertTrue(all(op['resource_binding']=='BOUND' for op in creations))
        self.assertEqual(len({op['submitted_request_id'] for op in case['operations']}),7)
        self.assertEqual(read(self.root/'local-trust-api.json'),case)
        self.assertEqual(result['bot_identities']['credential_memory'],'CLEARED')
        self.assert_stopped()
        with sqlite3.connect((self.root/'bcs.sqlite').as_uri()+'?mode=ro',uri=True) as db:
            for role in case['baseline']['roles']:
                observed=db.execute('SELECT visibility FROM bcs_bots WHERE env=? AND bot_uuid=?',
                                    ('local',role['bot_id'])).fetchone()[0]
                self.assertEqual(observed,role['original_visibility'])
            self.assertEqual(db.execute("SELECT COUNT(*) FROM bcs_friend_requests WHERE env='local'").fetchone()[0],3)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM bcs_friendships WHERE env='local'").fetchone()[0],2)
        before=(self.root/'local-trust-api.json').read_bytes()
        self.assertEqual(self.execute()[0],2)
        self.assertEqual((self.root/'local-trust-api.json').read_bytes(),before)

    def test_actual_commit_with_lost_response_is_unknown_not_retried_or_restored(self):
        self.lost_response(False)

    def test_cancellation_after_actual_commit_stops_later_writes_and_reaps(self):
        self.lost_response(True)

    def lost_response(self,cancel):
        original=OwnedLocalTrustApi._mutate
        sent=[]
        def mutate(case,role,operation,**kwargs):
            if operation=='friend.request':
                opener=case.identities.opener.open
                def lose(request,**options):
                    if request.get_method()!='POST':return opener(request,**options)
                    sent.append(request.get_method())
                    reply=opener(request,**options);reply.close()
                    if cancel:raise KeyboardInterrupt()
                    raise URLError('private-upstream-body')
                with patch.object(case.identities.opener,'open',side_effect=lose):
                    return original(case,role,operation,**kwargs)
            return original(case,role,operation,**kwargs)
        with patch.object(OwnedLocalTrustApi,'_mutate',mutate):rc,result=self.execute()
        self.assertEqual(rc,130 if cancel else 2,result)
        self.assertEqual(sent,['POST'])
        case=read(self.root/'local-trust-api.json')
        self.assertEqual(case['status'],'CANCELLED' if cancel else 'BLOCKED')
        self.assertEqual(case['operations'][-1]['status'],'UNKNOWN')
        self.assertEqual(case['operations'][-1]['resource_binding'],'UNKNOWN')
        self.assertEqual(case['visibility_restoration'],'UNCONFIRMED')
        self.assertEqual(len(case['requests']),0)
        self.assertNotIn('private-upstream-body',json.dumps(case)+json.dumps(result))
        self.assertFalse((self.root/'smoke-report.json').exists())
        self.assert_stopped()
        with sqlite3.connect((self.root/'bcs.sqlite').as_uri()+'?mode=ro',uri=True) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM bcs_friend_requests WHERE env='local'").fetchone()[0],1)

    def test_intent_persistence_failure_prevents_first_mutation(self):
        original=write
        def fail_intent(path,value):
            if value.get('operations') and value['operations'][-1]['status']=='INTENDED':
                raise OSError('private-journal-failure')
            return original(path,value)
        with patch('bcs_e2e.local_trust_api.write',side_effect=fail_intent):
            rc,result=self.execute()
        self.assertEqual(rc,2,result)
        self.assertNotIn('private-journal-failure',json.dumps(result))
        self.assert_stopped()
        with sqlite3.connect((self.root/'bcs.sqlite').as_uri()+'?mode=ro',uri=True) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM bcs_friend_requests WHERE env='local'").fetchone()[0],0)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM bcs_bots WHERE actor_kind='bot' AND visibility='public'").fetchone()[0],0)

    def test_cancel_and_failed_error_publication_preserve_cancellation_and_clear_holder(self):
        original_mutate=OwnedLocalTrustApi._mutate
        original_write=write
        held=[]
        def mutate(case,role,operation,**kwargs):
            if operation=='friend.request':
                held.append(case.identities)
                opener=case.identities.opener.open
                def cancel(request,**options):
                    if request.get_method()!='POST':return opener(request,**options)
                    reply=opener(request,**options);reply.close()
                    raise KeyboardInterrupt()
                with patch.object(case.identities.opener,'open',side_effect=cancel):
                    return original_mutate(case,role,operation,**kwargs)
            return original_mutate(case,role,operation,**kwargs)
        def fail_publication(path,value):
            if (value.get('operations') and value['operations'][-1]['status']=='UNKNOWN') or value.get('status')=='CANCELLED':
                raise OSError('private-secondary-publication')
            return original_write(path,value)
        with patch.object(OwnedLocalTrustApi,'_mutate',mutate), \
             patch('bcs_e2e.local_trust_api.write',side_effect=fail_publication):
            rc,result=self.execute()
        self.assertEqual(rc,130,result)
        self.assertEqual(result['status'],'CANCELLED')
        self.assertEqual(len(held),1)
        self.assertEqual(held[0].summary()['credential_memory'],'CLEARED')
        self.assertEqual(read(self.root/'local-trust-api.json')['operations'][-1]['status'],'INTENDED')
        self.assert_stopped()

    def test_duplicate_pending_200_is_not_fresh_protected_creation(self):
        original=OwnedLocalTrustApi._mutate
        def duplicate(case,role,operation,**kwargs):
            if operation=='friend.request' and role=='QA':
                entry=case.identities._entries[role]
                from urllib.request import Request
                request=Request(case.identities.origin+'/friends/request',method='POST',
                                data=json.dumps({'to_bot':case._id('PM')}).encode(),
                                headers={'Content-Type':'application/json','Authorization':'Bearer '+entry[1]})
                with case.identities.opener.open(request,timeout=5) as response:self.assertEqual(response.status,201)
            return original(case,role,operation,**kwargs)
        with patch.object(OwnedLocalTrustApi,'_mutate',duplicate):rc,result=self.execute()
        self.assertEqual(rc,2,result)
        journal=read(self.root/'local-trust-api.json')
        self.assertEqual(journal['operations'][-1]['http_status'],200)
        self.assertEqual(journal['operations'][-1]['status'],'UNKNOWN')
        self.assertFalse(any(op['operation']=='friend.accept' for op in journal['operations']))
        self.assert_stopped()

    def test_creation_ack_missing_id_never_adopts_existing_history_or_retries(self):
        original=OwnedLocalTrustApi._mutate
        def missing(case,role,operation,**kwargs):
            result=original(case,role,operation,**kwargs)
            if operation=='friend.request': return {'success':True,'message':'Already friends'}
            return result
        with patch.object(OwnedLocalTrustApi,'_mutate',missing):rc,result=self.execute()
        self.assertEqual(rc,2,result)
        journal=read(self.root/'local-trust-api.json')
        self.assertEqual(len([op for op in journal['operations'] if op['operation']=='friend.request']),1)
        self.assertEqual(journal['operations'][-1]['resource_binding'],'UNKNOWN')
        self.assertEqual(journal['requests'],[])
        self.assert_stopped()

    def test_exact_receiver_and_foreign_id_rejected_without_mutation_intent(self):
        original=OwnedLocalTrustApi._mutate
        seen=[]
        def check(case,role,operation,**kwargs):
            if operation=='friend.accept':
                before=len(case.state['operations'])
                with self.assertRaises(LocalTrustApiError):original(case,'CS',operation,**kwargs)
                with self.assertRaises(LocalTrustApiError):original(case,role,operation,request_id='foreign-request')
                self.assertEqual(len(case.state['operations']),before)
                seen.append(True)
            return original(case,role,operation,**kwargs)
        with patch.object(OwnedLocalTrustApi,'_mutate',check):rc,result=self.execute()
        self.assertEqual(rc,0,result);self.assertEqual(seen,[True]);self.assert_stopped()

    def test_restore_drift_never_overwrites_observed_changed_visibility(self):
        original=OwnedLocalTrustApi._final_storage
        def drift(case):
            original(case)
            bot_id=case._id('ENG')
            entry=case.identities._entries['ENG']
            from urllib.request import Request
            request=Request(case.identities.origin+'/bots/'+bot_id+'/visibility',method='PUT',
                            data=b'{"visibility":"private"}',headers={'Content-Type':'application/json',
                            'Authorization':'Bearer '+entry[1]})
            with case.identities.opener.open(request,timeout=5) as response:self.assertEqual(response.status,200)
        with patch.object(OwnedLocalTrustApi,'_final_storage',drift):rc,result=self.execute()
        self.assertEqual(rc,2,result)
        case=read(self.root/'local-trust-api.json')
        self.assertEqual(case['functional'],'PASS')
        self.assertEqual(case['visibility_restoration'],'UNCONFIRMED')
        self.assertFalse(any(op['operation']=='visibility.restore' for op in case['operations']))
        self.assert_stopped()
        with sqlite3.connect((self.root/'bcs.sqlite').as_uri()+'?mode=ro',uri=True) as db:
            role=next(r for r in case['baseline']['roles'] if r['role']=='ENG')
            self.assertEqual(db.execute('SELECT visibility FROM bcs_bots WHERE bot_uuid=?',(role['bot_id'],)).fetchone()[0],'private')

    def test_sqlite_only_restore_drift_is_preserved_not_overwritten_from_cache(self):
        original=OwnedLocalTrustApi._final_storage
        def drift(case):
            original(case)
            with sqlite3.connect(case.runtime.root/'bcs.sqlite') as db:
                db.execute('UPDATE bcs_bots SET visibility=? WHERE env=? AND bot_uuid=?',
                           ('private','local',case._id('ENG')))
        with patch.object(OwnedLocalTrustApi,'_final_storage',drift):rc,result=self.execute()
        self.assertEqual(rc,2,result)
        case=read(self.root/'local-trust-api.json')
        self.assertEqual(case['functional'],'PASS')
        self.assertEqual(case['visibility_restoration'],'UNCONFIRMED')
        self.assertFalse(any(op['operation']=='visibility.restore' for op in case['operations']))
        self.assert_stopped()
        with sqlite3.connect((self.root/'bcs.sqlite').as_uri()+'?mode=ro',uri=True) as db:
            role=next(r for r in case['baseline']['roles'] if r['role']=='ENG')
            self.assertEqual(db.execute('SELECT visibility FROM bcs_bots WHERE bot_uuid=?',(role['bot_id'],)).fetchone()[0],'private')

    def test_untouched_role_drift_blocks_restoration_pass_without_repair(self):
        original=OwnedLocalTrustApi._final_storage
        def drift(case):
            original(case)
            with sqlite3.connect(case.runtime.root/'bcs.sqlite') as db:
                db.execute('UPDATE bcs_bots SET visibility=? WHERE env=? AND bot_uuid=?',
                           ('private','local',case._id('CS')))
        with patch.object(OwnedLocalTrustApi,'_final_storage',drift):rc,result=self.execute()
        self.assertEqual(rc,2,result)
        case=read(self.root/'local-trust-api.json')
        self.assertEqual(case['functional'],'PASS')
        self.assertEqual(case['visibility_restoration'],'UNCONFIRMED')
        self.assertFalse(any(op['role']=='CS' and op['operation'].startswith('visibility.') for op in case['operations']))
        self.assert_stopped()

    def test_journal_drift_after_intent_and_ack_is_preserved_with_separate_blocked_outcome(self):
        for stage in ('INTENDED','ACKNOWLEDGED'):
            if self.root.exists():
                self.root=self.root.with_name(self.root.name+'-next')
                self.args[self.args.index('--state-dir')+1]=str(self.root)
            original=write
            modified=[]
            def tamper(path,value):
                original(path,value)
                if not modified and value.get('operations') and value['operations'][-1]['status']==stage:
                    raw=read(path);raw['external_marker']='must-remain'
                    original(path,raw)
                    modified.append(path.read_bytes())
            with self.subTest(stage=stage),patch('bcs_e2e.local_trust_api.write',side_effect=tamper):
                rc,result=self.execute()
            self.assertEqual(rc,2,result)
            self.assertEqual((self.root/'local-trust-api.json').read_bytes(),modified[0])
            integrity=read(self.root/'local-trust-api-integrity.json')
            self.assertEqual(integrity['status'],'BLOCKED')
            self.assertFalse(integrity['journal_overwritten'])
            self.assert_stopped()
            with sqlite3.connect((self.root/'bcs.sqlite').as_uri()+'?mode=ro',uri=True) as db:
                self.assertEqual(db.execute("SELECT COUNT(*) FROM bcs_friend_requests WHERE env='local'").fetchone()[0],0)

    def test_missing_business_steps_cannot_be_qualified_as_pass(self):
        with patch('bcs_e2e.trust_api_steps.execute_trust_steps') as steps:
            rc,result=self.execute()
        self.assertEqual(rc,2,result)
        steps.assert_called_once()
        case=read(self.root/'local-trust-api.json')
        self.assertGreater(case['expected_assertion_count'],0)
        self.assertEqual(case['observed_assertion_count'],0)
        self.assertEqual(case['operations'],[])
        self.assert_stopped()

    def test_api_positive_with_missing_stored_friendship_cannot_qualify(self):
        original=OwnedLocalTrustApi._persisted
        def remove(case,item,expected):
            if item['sender']=='QA' and expected=='accepted':
                with sqlite3.connect(case.runtime.root/'bcs.sqlite') as db:
                    db.execute('DELETE FROM bcs_friendships WHERE env=? AND '
                               '((left_bot=? AND right_bot=?) OR (left_bot=? AND right_bot=?))',
                               ('local',case._id('QA'),case._id('PM'),case._id('PM'),case._id('QA')))
            return original(case,item,expected)
        with patch.object(OwnedLocalTrustApi,'_persisted',remove):rc,result=self.execute()
        self.assertEqual(rc,2,result)
        case=read(self.root/'local-trust-api.json')
        self.assertEqual(case['functional'],'FAIL')
        self.assertEqual(len(case['requests']),2)
        self.assertFalse(any(op['operation']=='friend.reject' for op in case['operations']))
        self.assert_stopped()
