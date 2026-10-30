"""Guards and opt-in live coverage for the final three original Session helpers."""
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from bcs_e2e.bounded_process import ProcessCaptureError
from bcs_e2e.inventory import current_manifest,executor_digest
from bcs_e2e.ledger import Ledger
from bcs_e2e.local_fixtures import OwnedLocalFixtures
from bcs_e2e.local_role_identity import OwnedLocalRoleIdentity
from bcs_e2e.local_session_contract import PUBLIC_CASE,PUBLIC_SUITE,REQUIREMENTS,session_source_binding
from bcs_e2e.local_session_public import OwnedLocalSessionPublic
from bcs_e2e.local_session_sharing import OwnedLocalSessionSharing
from bcs_e2e.local_session_completion import (BINDING_NAME,CASE_ID,CLI_OPERATIONS,CONTRACT,
    JOURNAL_NAME,LEDGER_NAME,OPERATIONS,PLAN_NAME,ROLES,SELECTED,
    LocalSessionCompletionError,OwnedLocalSessionCompletion)
from bcs_e2e.local_session_completion_cli import CompletionCliDriver,CompletionCliError
from bcs_e2e.owned_bcs import OwnedBcsProcess
from bcs_e2e.profile import fingerprint
from bcs_e2e.run_state import read,write

BCS_BINARY=os.environ.get('BCS_E2E_TEST_BINARY','');BCS_SHA=os.environ.get('BCS_E2E_TEST_BINARY_SHA256','')
CLI_BINARY=os.environ.get('BCS_E2E_TEST_CLI_BINARY','');CLI_SHA=os.environ.get('BCS_E2E_TEST_CLI_BINARY_SHA256','')

def base(runtime):
    return {'schema_version':1,'contract':'owned-local-session-public-v1','suite':PUBLIC_SUITE,
      'case_id':PUBLIC_CASE,'run_id':runtime.run_id,'executor_digest':executor_digest(),
      'bcs_binary_sha256':runtime.binary_sha256,'source_binding':session_source_binding(),
      'selected_requirement_ids':[REQUIREMENTS[0]],'parent_requirement_ids':list(REQUIREMENTS),
      'legacy_inventory':current_manifest()['legacy_inventory'],'runtime_dir':str(runtime.root.resolve()),
      'store_messages':True}
def sharing_plan(runtime):
    return dict(base(runtime),contract='owned-local-session-sharing-v1',suite='owned-local-session-sharing-v1',
                case_id='LOCAL-SESSION-SHARING-01',cli_binary_sha256=CLI_SHA,
                selected_requirement_ids=list(REQUIREMENTS[:6]))
def completion_plan(runtime):
    return dict(base(runtime),contract=CONTRACT,suite=CONTRACT,case_id=CASE_ID,
                cli_binary_sha256=CLI_SHA,selected_requirement_ids=list(REQUIREMENTS))

class CompletionGuardTests(unittest.TestCase):
    def test_contract_is_final_three_original_helpers_and_two_cli_calls(self):
        self.assertEqual(SELECTED,REQUIREMENTS[6:]);self.assertEqual(CLI_OPERATIONS,('lifecycle-group-create','collection-group-create'))
        self.assertEqual(len(OPERATIONS),59);self.assertEqual(CASE_ID,'LOCAL-SESSION-FLOW-01')
    def test_constructor_rejects_unmanaged_identity(self):
        with self.assertRaisesRegex(LocalSessionCompletionError,'freshly verified'):
            OwnedLocalSessionCompletion(object(),Path('/missing'),'0'*64)
    def test_missing_pm_token_precedes_cli_request(self):
        case=Mock();case.identities._entries={'PM':('pm','')};case._active=Mock()
        driver=object.__new__(CompletionCliDriver);driver.case=case
        with self.assertRaisesRegex(CompletionCliError,'missing or invalid'):driver.require_pm_token()
    def admission_case(self,index):
        case=object.__new__(OwnedLocalSessionCompletion);case._active=Mock();case.ledger=Mock()
        case._id=lambda r:{'PM':'pm','ENG':'eng','QA':'qa','CEO':'ceo'}[r]
        case.state={'operations':[{'operation':x} for x in OPERATIONS[:index]],'resources':{'groups':{},'sessions':{}},'cleanup_admitted':{}}
        return case
    def test_wrong_caller_service_read_contract_is_ceo_403(self):
        index=OPERATIONS.index('complete-service-foreign');case=self.admission_case(index)
        case.state['resources']['groups']['complete']={'id':'g','handle':'gh','initial_session_id':'g:i','initial_session_handle':'ih'};case.state['resources']['sessions']['complete']={'id':'g:s','handle':'sh'}
        case.state['resources']['service_session']={'id':'g:ss','handle':'ssh'}
        rows={'gh':{'kind':'group','case_id':CASE_ID,'attempt':1,'origin':'created','resource_id':'g','state':'live','parent':None},'ih':{'kind':'session','case_id':CASE_ID,'attempt':1,'origin':'created','resource_id':'g:i','state':'live','parent':'gh'},'sh':{'kind':'session','case_id':CASE_ID,'attempt':1,'origin':'created','resource_id':'g:s','state':'live','parent':'gh'},'ssh':{'kind':'session','case_id':CASE_ID,'attempt':1,'origin':'created','resource_id':'g:ss','state':'live','parent':'gh'}}
        case._ledger_row=lambda h:rows[h]
        spec=case._spec('complete-service-foreign');self.assertEqual(spec,('GET','/services/g/sessions/g%3Ass',None,'CEO',403))
        with self.assertRaisesRegex(LocalSessionCompletionError,'differs'):
            case._admit('complete-service-foreign','GET',spec[1],None,'PM',403)
    def test_nonparticipant_collect_contract_is_qa_404(self):
        index=OPERATIONS.index('collection-qa-nonparticipant');case=self.admission_case(index)
        case.state['resources']['groups']['collection']={'id':'g','handle':'gh','initial_session_id':'g:i','initial_session_handle':'ih'};case.state['resources']['sessions']['collection']={'id':'g:s','handle':'sh'}
        rows={'gh':{'kind':'group','case_id':CASE_ID,'attempt':1,'origin':'created','resource_id':'g','state':'live','parent':None},'ih':{'kind':'session','case_id':CASE_ID,'attempt':1,'origin':'created','resource_id':'g:i','state':'live','parent':'gh'},'sh':{'kind':'session','case_id':CASE_ID,'attempt':1,'origin':'created','resource_id':'g:s','state':'live','parent':'gh'}}
        case._ledger_row=lambda h:rows[h]
        spec=case._spec('collection-qa-nonparticipant');self.assertEqual(spec,('POST','/sessions/g%3As/collect',{},'QA',404))
        with self.assertRaisesRegex(LocalSessionCompletionError,'differs'):
            case._admit('collection-qa-nonparticipant','POST',spec[1],{},'PM',404)
    def test_group_create_rejects_borrowed_or_wrong_parent_intents(self):
        index=OPERATIONS.index('lifecycle-group-create');case=self.admission_case(index)
        body=case._spec('lifecycle-group-create')[2]
        rows={'gh':{'kind':'group','parent':None,'case_id':CASE_ID,'attempt':1,'origin':'borrowed','state':'live','resource_id':'foreign'},
              'ih':{'kind':'session','parent':'wrong','case_id':CASE_ID,'attempt':1,'origin':'unknown','state':'intended','resource_id':None}}
        case._ledger_row=lambda h:rows[h]
        args=['create-group','--driver','pm','--participants','pm,eng','--topic','e2e-session-completion-lifecycle']
        with self.assertRaises(LocalSessionCompletionError):
            case._admit('lifecycle-group-create','POST','/groups',body,'PM',200,('gh','ih'),'PRODUCT_CLI',args)
    def test_explicit_session_rejects_borrowed_parent(self):
        index=OPERATIONS.index('lifecycle-session-create');case=self.admission_case(index)
        case.state['resources']['groups']['lifecycle']={'id':'g','handle':'gh','initial_session_id':'g:i','initial_session_handle':'ih'}
        rows={'gh':{'kind':'group','case_id':CASE_ID,'attempt':1,'origin':'created','resource_id':'g','state':'live','parent':None},
              'ih':{'kind':'session','case_id':CASE_ID,'attempt':1,'origin':'created','resource_id':'g:i','state':'live','parent':'gh'},
              'sh':{'kind':'session','case_id':CASE_ID,'attempt':1,'origin':'unknown','resource_id':None,'state':'intended','parent':'foreign'}}
        case._ledger_row=lambda h:rows[h]
        method,path,body,auth,status=case._spec('lifecycle-session-create')
        with self.assertRaisesRegex(LocalSessionCompletionError,'parent differs'):
            case._admit('lifecycle-session-create',method,path,body,auth,status,('sh',))
    def test_delete_before_local_admission_is_rejected(self):
        index=OPERATIONS.index('lifecycle-session-delete');case=self.admission_case(index)
        case.state['resources']['groups']['lifecycle']={'id':'g','handle':'gh','initial_session_id':'g:i','initial_session_handle':'ih'}
        case.state['resources']['sessions']['lifecycle']={'id':'g:s','handle':'sh'}
        rows={'gh':{'kind':'group','case_id':CASE_ID,'attempt':1,'origin':'created','resource_id':'g','state':'live','parent':None},
              'ih':{'kind':'session','case_id':CASE_ID,'attempt':1,'origin':'created','resource_id':'g:i','state':'live','parent':'gh'},
              'sh':{'kind':'session','case_id':CASE_ID,'attempt':1,'origin':'created','resource_id':'g:s','state':'live','parent':'gh'}}
        case._ledger_row=lambda h:rows[h]
        spec=case._spec('lifecycle-session-delete')
        with self.assertRaisesRegex(LocalSessionCompletionError,'prior local deletion admission'):
            case._admit('lifecycle-session-delete',*spec)
    def test_service_settings_accepts_exact_retired_410_and_ack_forms(self):
        retired={'error':'service_policy_retired','message':'service_spec settings have been retired'}
        self.assertEqual(OwnedLocalSessionCompletion._settings_form(410,retired),'retired_410')
        self.assertEqual(OwnedLocalSessionCompletion._settings_form(200,{'service_spec':{'max_concurrency':2}}),'service_spec_ack')
        with self.assertRaisesRegex(LocalSessionCompletionError,'retired service settings'):
            OwnedLocalSessionCompletion._settings_form(410,{'error':'wrong','message':'private'})
        with self.assertRaisesRegex(LocalSessionCompletionError,'acknowledgement'):
            OwnedLocalSessionCompletion._settings_form(200,{'service_spec':{'max_concurrency':1}})
    def test_no_engine_queue_or_fake_reply_claims(self):
        source=(ROOT/'bcs_e2e/local_session_completion.py').read_text()
        self.assertIn("'engine':'NOT_STARTED'",source);self.assertIn("'service_business_execution':'NOT_CLAIMED'",source)
        self.assertIn("chat.get('delivered_count')!=0",source);self.assertNotIn('success ACK',source)
    def test_pass_is_written_only_after_cleanup_proof(self):
        source=(ROOT/'bcs_e2e/local_session_completion.py').read_text()
        marker="self._record_check(helper,{'cleanup':'PASS','sqlite':proof})"
        self.assertEqual(source.count(marker),1)
        self.assertGreater(source.index(marker),source.index('proof=deletion_proof'))
        cleanup_assert="self._assert('SESSION.complete-invoke.service-group-cleanup'"
        self.assertEqual(source.count(cleanup_assert),1)
        self.assertGreater(source.index(cleanup_assert),source.index("self._cleanup('complete'"))

@unittest.skipUnless(BCS_BINARY and BCS_SHA and CLI_BINARY and CLI_SHA and shutil.which('lsof'),'approved actual binaries required')
class CompletionLiveTests(unittest.TestCase):
    def execute(self,hook=None):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup);root=Path(temporary.name)/'runtime'
        with OwnedBcsProcess(binary=Path(BCS_BINARY),binary_sha256=BCS_SHA,root=root,run_id='session-completion-live',store_messages=True) as runtime:
            public=base(runtime);sharing=sharing_plan(runtime);completion=completion_plan(runtime)
            write(root/'session-binding.json',public);write(root/'session-sharing-plan.json',sharing);write(root/PLAN_NAME,completion)
            fixtures=OwnedLocalFixtures(runtime,fixture_set='workspace')
            with OwnedLocalRoleIdentity(fixtures,roles=fixtures.roles) as identities:
                fixtures.apply(allow_write=True,role_identity=identities)
                for role in fixtures.roles:identities.verify(role)
                public_result=OwnedLocalSessionPublic(identities).run()
                write(root/'session-sharing-binding.json',dict(sharing,public_journal_digest=fingerprint(public_result)))
                sharing_result=OwnedLocalSessionSharing(identities,Path(CLI_BINARY),CLI_SHA).run()
                binding=dict(completion,public_journal_digest=fingerprint(public_result),sharing_journal_digest=fingerprint(sharing_result))
                write(root/BINDING_NAME,binding);case=OwnedLocalSessionCompletion(identities,Path(CLI_BINARY),CLI_SHA)
                result=case.run() if hook is None else hook(case)
        return root,result
    def test_real_success_three_helpers_seven_sessions_ten_tombstones(self):
        root,result=self.execute();self.assertEqual(result['status'],'LOCAL_SESSION_COMPLETION_PASS')
        self.assertEqual(result['assertions'],[{'id':x,'status':'PASS'} for x in SELECTED])
        self.assertEqual([x['operation'] for x in result['operations']],list(OPERATIONS));self.assertTrue(all(x['status']=='ACKNOWLEDGED' for x in result['operations']))
        self.assertEqual(result['resources']['group_count'],3);self.assertEqual(result['resources']['session_count'],7)
        self.assertEqual(result['resources']['ledger_resource_count'],10);self.assertEqual(result['resources']['ledger_deleted_count'],10)
        self.assertTrue(all(x['attempt']==1 and x['origin']=='created' and x['state']=='deleted' for x in result['resources']['ledger']))
        self.assertEqual(result['cli'],{'operation_count':2,'acknowledged_count':2,'credential_contexts_cleared':True})
        self.assertEqual(result['histories']['predecessor_before'],result['histories']['predecessor_after'])
        self.assertEqual(result['histories']['predecessor_before']['row_count'],21)
        self.assertEqual(len(result['deletion_admissions']),3);self.assertEqual(len(result['deletion_proofs']),3)
        self.assertEqual(read(root/JOURNAL_NAME),result);self.assertNotIn('max_groups_as_driver',json.loads((root/'bcs-config.json').read_text()))
        names=[x['operation'] for x in result['operations']]
        self.assertLess(names.index('lifecycle-group-deleted-404'),names.index('complete-group-create'))
        self.assertLess(names.index('complete-group-deleted-404'),names.index('collection-group-create'))
    def test_lost_create_response_is_unknown_once_without_cleanup(self):
        from bcs_e2e import local_session_completion_cli as module
        actual=module.capture
        def hook(case):
            calls=[]
            def lose(command,**kwargs):
                result=actual(command,**kwargs)
                if 'create-group' in command and not calls:calls.append(1);raise ProcessCaptureError('private-lost')
                return result
            with patch.object(module,'capture',side_effect=lose):
                with self.assertRaises(LocalSessionCompletionError):case.run()
            value=read(case.path);value['_calls']=len(calls);return value
        root,result=self.execute(hook);self.assertEqual(result['_calls'],1);self.assertEqual(result['operations'][3]['status'],'UNKNOWN')
        self.assertFalse(any(x['method']=='DELETE' for x in result['operations']))
        binding=read(root/BINDING_NAME)
        with Ledger(root/LEDGER_NAME,binding['run_id'],fingerprint(binding),read_only=True) as ledger:rows=ledger.resource_snapshot()
        self.assertEqual([x['state'] for x in rows],['unknown','unknown']);self.assertNotIn('private-lost',json.dumps(result))
    def test_nonzero_cli_is_unknown_and_stdout_not_persisted(self):
        from bcs_e2e import local_session_completion_cli as module
        actual=module.capture
        def hook(case):
            changed=[]
            def nonzero(command,**kwargs):
                result=actual(command,**kwargs)
                if 'create-group' in command and not changed:changed.append(1);return subprocess.CompletedProcess(command,9,b'private-output',b'')
                return result
            with patch.object(module,'capture',side_effect=nonzero):
                with self.assertRaises(LocalSessionCompletionError):case.run()
            return read(case.path)
        _root,result=self.execute(hook);self.assertEqual(result['operations'][3]['status'],'UNKNOWN');self.assertNotIn('private-output',json.dumps(result))
    def test_cleanup_proof_failure_never_tombstones_unknown_absence(self):
        from bcs_e2e import local_session_completion_storage as storage
        def hook(case):
            original=storage.deletion_proof
            def fail(scope,helper,history):
                if helper=='lifecycle':raise LocalSessionCompletionError('private-cleanup-proof')
                return original(scope,helper,history)
            with patch.object(storage,'deletion_proof',side_effect=fail):
                with self.assertRaises(LocalSessionCompletionError):case.run()
            return read(case.path)
        root,result=self.execute(hook);self.assertEqual(result['status'],'BLOCKED')
        binding=read(root/BINDING_NAME)
        with Ledger(root/LEDGER_NAME,binding['run_id'],fingerprint(binding),read_only=True) as ledger:rows=ledger.resource_snapshot()
        self.assertEqual([x['state'] for x in rows],['live','live','deleted']);self.assertNotIn('private-cleanup-proof',json.dumps(result))

if __name__=='__main__':unittest.main()
