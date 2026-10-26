"""Fail-closed guards for the complete owned-local Channel CLI parent."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from bcs_e2e.errors import has_cancellation
from bcs_e2e.local_channel_cli import (CASE_ID,CLI_OPERATIONS,FULL_OPERATIONS,REQUIREMENTS,
    LocalChannelCliError,OwnedLocalChannelCli)
from bcs_e2e.local_channel_cli_driver import ChannelCliDriver,ChannelCliDriverError
from bcs_e2e.run_state import write as state_write


class ChannelCliContractGuards(unittest.TestCase):
    def fake_case(self,index):
        case=object.__new__(OwnedLocalChannelCli);case._active=lambda:None
        case._id=lambda role:{'CEO':'ceo','PM':'pm','ENG':'eng','QA':'qa'}[role]
        case.account='cli-channel-account';case.missing='missing-channel-cli-'+'a'*24
        case.state={'operations':[{'operation':x} for x in FULL_OPERATIONS[:index]],
                    'assertions':[],'resources':{'group':None,'session':None}}
        case.ledger=object();return case


    @staticmethod
    def bind_resources(case, explicit_state='live'):
        case.state['resources']={'group':{'id':'g','handle':'gh','initial_session_id':'g:i','initial_session_handle':'ih'},
                                 'session':{'id':'g:s','handle':'sh','group_id':'g'}}
        rows={'gh':{'kind':'group','case_id':CASE_ID,'attempt':1,'origin':'created','resource_id':'g','state':'live','parent':None},
              'ih':{'kind':'session','case_id':CASE_ID,'attempt':1,'origin':'created','resource_id':'g:i','state':'live','parent':'gh'},
              'sh':{'kind':'session','case_id':CASE_ID,'attempt':1,'origin':'created','resource_id':'g:s','state':explicit_state,'parent':'gh'}}
        case._ledger_row=lambda handle:rows[handle]
        return rows

    def test_schema_has_nine_requirements_seven_cli_and_fourteen_wires(self):
        self.assertEqual(len(REQUIREMENTS),9);self.assertEqual(len(CLI_OPERATIONS),7)
        self.assertEqual(FULL_OPERATIONS,(
          'channel-bind','channel-list-before','eng-visibility-before','eng-visibility-public',
          'eng-visibility-public-read','group-create','session-create','channel-conversation-empty',
          'channel-unbind','channel-list-after','session-delete','group-delete',
          'eng-visibility-restore','eng-visibility-restored-read'))

    def test_constructor_rejects_unmanaged_identity(self):
        with self.assertRaisesRegex(LocalChannelCliError,'freshly verified'):
            OwnedLocalChannelCli(object(),Path('/missing'),'0'*64)

    def test_malformed_cli_arguments_are_rejected(self):
        case=self.fake_case(FULL_OPERATIONS.index('channel-list-before'))
        spec=case._spec('channel-list-before')
        with self.assertRaisesRegex(LocalChannelCliError,'finite request'):
            case._admit_cli('channel-list-before',*spec[:4],['channel','list','--all'],*spec[5:],(), '')

    def test_borrowed_group_intents_are_rejected(self):
        case=self.fake_case(FULL_OPERATIONS.index('group-create'))
        rows={'gh':{'kind':'group','parent':None,'case_id':CASE_ID,'attempt':1,'origin':'borrowed','state':'live','resource_id':'foreign'},
              'ih':{'kind':'session','parent':'wrong','case_id':CASE_ID,'attempt':1,'origin':'unknown','state':'intended','resource_id':None}}
        case._ledger_row=lambda handle:rows[handle]
        role,method,path,body,args,status,kind=case._spec('group-create')
        with self.assertRaises(LocalChannelCliError):
            case._admit_cli('group-create',role,method,path,body,args,status,kind,('gh','ih'),'')


    def test_unbind_requires_persisted_empty_table_admission_before_wire(self):
        case=self.fake_case(FULL_OPERATIONS.index('channel-unbind'));self.bind_resources(case)
        role,method,path,body,args,status,kind=case._spec('channel-unbind')
        with self.assertRaisesRegex(LocalChannelCliError,'durable empty-table admission'):
            case._admit_cli('channel-unbind',role,method,path,body,args,status,kind,(), '')

    def test_resource_delete_requires_durable_admission_before_connection(self):
        case=self.fake_case(FULL_OPERATIONS.index('session-delete'));self.bind_resources(case)
        case.port=1;case.identities=FakeIdentities()
        with patch('bcs_e2e.local_channel_cli.HTTPConnection') as connection:
            with self.assertRaisesRegex(LocalChannelCliError,'durable owned-local admission'):
                case._http('session-delete')
        connection.assert_not_called()

    def test_group_delete_requires_explicit_session_tombstone(self):
        case=self.fake_case(FULL_OPERATIONS.index('group-delete'));self.bind_resources(case,'live')
        case.state['checks']=[{'id':'cleanup-admission','status':'PASS','evidence':{
          'status':'LOCAL_DELETION_ADMITTED','contract':'owned-local-foreground-transport-only-v1',
          'engine':'NOT_STARTED','provider_bindings':0,'queued_deliveries':0,'state_machine_runs':0}}]
        with self.assertRaisesRegex(LocalChannelCliError,'exact owned'):
            case._require_delete_admission('group-delete')

    def test_lost_create_is_unknown_and_never_admits_delete(self):
        case=self.fake_case(FULL_OPERATIONS.index('group-create')+1);case.invalidated=False
        case.state['operations'][-1].update(method='POST',status='INTENDED',transitions=[{'status':'INTENDED'}])
        unknown=[]
        class Ledger:
            def unknown(self,handle):unknown.append(handle)
        case.ledger=Ledger();case._save=lambda:None
        case._operation_failed(case.state['operations'][-1],RuntimeError('lost'),handles=('gh','ih'),mutation=True)
        self.assertEqual(unknown,['gh','ih']);self.assertTrue(case.invalidated)
        self.assertEqual(case.state['operations'][-1]['status'],'UNKNOWN')
        self.assertFalse(any(row.get('method')=='DELETE' for row in case.state['operations']))


class FakeIdentities:
    origin='http://127.0.0.1:1'
    _entries={'CEO':('ceo','ceo-token'),'PM':('pm','pm-token')}


class FakeCase:
    def __init__(self,root):
        self.root=Path(root);self.identities=FakeIdentities();self.invalidated=False
        self.event=None;self.failed=[];self.save_calls=0;self.fail_save_at=None
    def _active(self):pass
    def _admit_cli(self,*args):pass
    def _append_operation(self,operation,method,path,role,request_id,**kwargs):
        self.event={'operation':operation,'method':method,'path':path,'role':role,
                    'submitted_request_id':request_id,'status':'INTENDED','transitions':[{'status':'INTENDED'}]}
        return self.event
    def _operation_failed(self,event,error,**kwargs):
        event['status']='UNKNOWN';self.failed.append(type(error).__name__)
    def _save(self):
        self.save_calls+=1
        if self.fail_save_at==self.save_calls:raise RuntimeError('checkpoint')


class FakeExchange:
    def __init__(self,case,event,**kwargs):
        self.case=case;self.event=event;self.origin='http://127.0.0.1:2'
        self.failure=None;self.attempts=1;self.forwarded=1;self.response={'items':[]}
    def __enter__(self):self.event['http_status']=200;return self
    def __exit__(self,*args):return False


class ChannelCliDriverFinalizationTests(unittest.TestCase):
    def driver(self,case):
        value=object.__new__(ChannelCliDriver);value.case=case;value.binary=Path('/approved-cli');value.binary_sha256='a'*64
        return value

    def invoke(self,case,capture_result):
        driver=self.driver(case)
        with patch('bcs_e2e.local_channel_cli_driver.require_cli_binary'), \
             patch('bcs_e2e.local_channel_cli_driver.LocalCliExchange',FakeExchange), \
             patch.object(driver,'_capture',**({'side_effect':capture_result} if isinstance(capture_result,BaseException)
                                              else {'return_value':capture_result})) as capture:
            result=driver.invoke(operation='channel-list-before',role='CEO',method='GET',path='/channels/bindings',
                body=None,arguments=['channel','list'],expected_status=200,output_kind='json',request_id='request-1')
        return result,capture

    def test_original_global_json_flag_is_retained(self):
        with tempfile.TemporaryDirectory() as tmp:
            result,capture=self.invoke(FakeCase(tmp),subprocess.CompletedProcess([],0,b'{"items":[]}',b''))
            self.assertEqual(result,{'items':[]})
            command=capture.call_args.args[0]
            self.assertEqual(command[1:4],['--json','--log-level','error'])

    def test_cleanup_failure_fails_closed_even_after_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            case=FakeCase(tmp);actual=state_write
            def fail_clear(path,value):
                if value.get('token')=='':raise OSError('clear failed')
                return actual(path,value)
            with patch('bcs_e2e.local_channel_cli_driver.write',side_effect=fail_clear):
                with self.assertRaisesRegex(ChannelCliDriverError,'cleanup or durable checkpoint'):
                    self.invoke(case,subprocess.CompletedProcess([],0,b'{"items":[]}',b''))
            self.assertTrue(case.invalidated);self.assertEqual(case.event['credential_cleanup'],'UNCONFIRMED')

    def test_checkpoint_failure_after_success_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            case=FakeCase(tmp);case.fail_save_at=2
            with self.assertRaisesRegex(ChannelCliDriverError,'cleanup or durable checkpoint'):
                self.invoke(case,subprocess.CompletedProcess([],0,b'{"items":[]}',b''))
            self.assertTrue(case.invalidated);self.assertEqual(case.event['credential_cleanup'],'CLEARED')

    def test_cancellation_is_preserved_and_credentials_are_cleared(self):
        with tempfile.TemporaryDirectory() as tmp:
            case=FakeCase(tmp)
            with self.assertRaises(KeyboardInterrupt) as raised:self.invoke(case,KeyboardInterrupt())
            self.assertTrue(has_cancellation(raised.exception));self.assertEqual(case.event['credential_cleanup'],'CLEARED')
            self.assertIn('KeyboardInterrupt',case.failed)

    def test_cleanup_failure_during_cancellation_preserves_cancellation(self):
        with tempfile.TemporaryDirectory() as tmp:
            case=FakeCase(tmp);actual=state_write
            def fail_clear(path,value):
                if value.get('token')=='':raise OSError('clear failed')
                return actual(path,value)
            with patch('bcs_e2e.local_channel_cli_driver.write',side_effect=fail_clear):
                with self.assertRaises(KeyboardInterrupt) as raised:self.invoke(case,KeyboardInterrupt())
            self.assertTrue(has_cancellation(raised.exception));self.assertTrue(case.invalidated)
            self.assertEqual(getattr(raised.exception,'credential_cleanup_status',None),'UNCONFIRMED')


if __name__=='__main__':unittest.main()
