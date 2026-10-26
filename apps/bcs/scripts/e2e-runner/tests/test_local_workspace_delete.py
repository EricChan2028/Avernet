"""Actual local Incident business deletion; no shared or remote cleanup."""
import contextlib
import io
from urllib.error import HTTPError
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from bcs_e2e.cli import main
from bcs_e2e.local_workspace_delete import OwnedLocalWorkspaceDelete
from bcs_e2e.local_workspace_messages import OwnedLocalWorkspaceMessages
from bcs_e2e.run_state import RunStateError, read

BINARY=os.environ.get('BCS_E2E_TEST_BINARY','')
DIGEST=os.environ.get('BCS_E2E_TEST_BINARY_SHA256','')
CLI=os.environ.get('BCS_E2E_TEST_CLI_BINARY','')
CLI_DIGEST=os.environ.get('BCS_E2E_TEST_CLI_BINARY_SHA256','')


class WorkspaceDeleteGuardTests(unittest.TestCase):
    def test_saved_report_cannot_authorize_delete(self):
        with self.assertRaisesRegex(RunStateError,'live completed'):
            OwnedLocalWorkspaceDelete({'status':'LOCAL_WORKSPACE_MESSAGES_PASS'})
        case=object.__new__(OwnedLocalWorkspaceMessages)
        case._completed=False
        with self.assertRaises(RunStateError):OwnedLocalWorkspaceDelete(case)

    def test_generic_or_foreign_404_cannot_confirm_group_deletion(self):
        for value in ({'status':404,'code':'not_found'},
                      {'status':404,'code':'group_not_found','params':{'group_id':'foreign'}}):
            case=object.__new__(OwnedLocalWorkspaceDelete)
            case.identities=Mock()
            case.identities.origin='http://127.0.0.1:12345'
            case.group_id='owned'
            case.state={}
            case._save=Mock()
            case.identities.opener.open.side_effect=HTTPError('http://127.0.0.1:12345/groups/owned',404,'missing',{},io.BytesIO(json.dumps(value).encode()))
            with self.subTest(value=value),self.assertRaises(RunStateError):case._verify_not_found()
            case._save.assert_not_called()

    def test_second_dispatch_is_denied_before_admission_or_http(self):
        case=object.__new__(OwnedLocalWorkspaceDelete)
        case._attempted=True
        case._admit=Mock()
        with self.assertRaisesRegex(RunStateError,'twice'):case._delete()
        case._admit.assert_not_called()


@unittest.skipUnless(BINARY and DIGEST and CLI and CLI_DIGEST and shutil.which('lsof'),
                     'approved actual BCS and CLI required')
class WorkspaceDeleteLiveTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name)/'runtime'

    def execute(self):
        args=['local-workspace-run','--with-incident-delete','--state-dir',str(self.root),
              '--run-id','workspace-delete','--confirm-run-id','workspace-delete',
              '--binary',BINARY,'--binary-sha256',DIGEST,'--cli-binary',CLI,'--cli-binary-sha256',CLI_DIGEST,
              '--allow-local-processes','--allow-fixture-write','--allow-write']
        with contextlib.redirect_stdout(io.StringIO()) as output:rc=main(args)
        return rc,json.loads(output.getvalue())

    def test_actual_original_incident_delete_with_exact_cascade_and_retained_history(self):
        rc,result=self.execute()
        self.assertEqual(rc,0,result.get('delete_case'))
        self.assertEqual(result['status'],'LOCAL_WORKSPACE_INCIDENT_ROUNDTRIP_PASS')
        self.assertEqual([r['status'] for r in result['requirements']],['PASS']*17+['NOT_RUN'])
        self.assertEqual(result['parent_requirement_count'],18)
        case=result['delete_case']
        self.assertEqual(case['admission']['business_session_terminal'],'NOT_CLAIMED')
        self.assertEqual(case['storage_verification']['group_rows'],0)
        self.assertEqual(case['storage_verification']['session_rows'],0)
        self.assertEqual(case['storage_verification']['retained_message_rows'],6)
        with sqlite3.connect(self.root/'bcs.sqlite') as db:
            self.assertEqual(db.execute('SELECT count(*) FROM bcs_groups').fetchone()[0],4)
        with sqlite3.connect(self.root/'workspace-incident-resources.sqlite') as db:
            self.assertEqual(db.execute('SELECT kind,state FROM resource ORDER BY rowid').fetchall(),
                             [('group','deleted'),('session','deleted')])
        self.assertEqual(result['shutdown']['status'],'STOPPED')
        self.assertEqual(result['credentials']['credential_memory'],'CLEARED')
        self.assertEqual(result['full_story_status'],'NOT_MIGRATED')

    def test_missing_live_receivers_denies_delete_before_http(self):
        original=OwnedLocalWorkspaceMessages.run
        def erase(message):
            result=original(message)
            message._receivers={}
            return result
        with patch.object(OwnedLocalWorkspaceMessages,'run',erase):
            rc,result=self.execute()
        self.assertEqual(rc,2)
        with sqlite3.connect(self.root/'bcs.sqlite') as db:
            self.assertEqual(db.execute('SELECT count(*) FROM bcs_groups').fetchone()[0],5)
        self.assertFalse((self.root/'local-workspace-delete.json').exists())
        self.assertEqual(result['shutdown']['status'],'STOPPED')

    def test_lost_delete_response_keeps_unknown_and_never_replays(self):
        original=OwnedLocalWorkspaceDelete._admit
        installed=[]
        calls=[]
        def lose(case):
            result=original(case)
            if not installed:
                installed.append(True)
                real_open=case.identities.opener.open
                def open_request(request,*args,**kwargs):
                    response=real_open(request,*args,**kwargs)
                    if request.get_method()=='DELETE':
                        calls.append(request.full_url)
                        response.close()
                        raise OSError('synthetic lost delete response')
                    return response
                case.identities.opener.open=open_request
            return result
        with patch.object(OwnedLocalWorkspaceDelete,'_admit',lose):
            rc,result=self.execute()
        self.assertEqual(rc,2)
        self.assertEqual(len(calls),1)
        self.assertEqual(result['delete_case']['operations'][0]['status'],'UNKNOWN')
        self.assertEqual(result['resource_disposition'],'INCIDENT_DELETION_UNCONFIRMED_OTHER_RESOURCES_RETAINED')
        with sqlite3.connect(self.root/'workspace-incident-resources.sqlite') as db:
            self.assertEqual(db.execute('SELECT state FROM resource ORDER BY rowid').fetchall(),[('live',),('live',)])
        self.assertEqual(result['shutdown']['status'],'STOPPED')

    def test_cancel_after_delete_retains_cancelled_and_does_not_acknowledge_ledger(self):
        original=OwnedLocalWorkspaceDelete._delete
        def cancel(case):
            original(case)
            raise KeyboardInterrupt()
        with patch.object(OwnedLocalWorkspaceDelete,'_delete',cancel):rc,result=self.execute()
        self.assertEqual(rc,130)
        self.assertEqual(result['delete_case']['status'],'CANCELLED')
        self.assertEqual(result['shutdown']['status'],'STOPPED')
        with sqlite3.connect(self.root/'workspace-incident-resources.sqlite') as db:
            self.assertEqual(db.execute('SELECT state FROM resource ORDER BY rowid').fetchall(),[('live',),('live',)])


    def test_unrelated_resource_drift_prevents_cleanup_pass(self):
        original = OwnedLocalWorkspaceDelete._unrelated_snapshot
        calls = []
        def drift(case):
            result = original(case)
            calls.append(1)
            if len(calls) == 2:
                result['groups']['digest'] = 'changed-after-delete'
            return result
        with patch.object(OwnedLocalWorkspaceDelete, '_unrelated_snapshot', drift):
            rc, result = self.execute()
        self.assertEqual(rc, 2)
        self.assertEqual(result['delete_case']['status'], 'BLOCKED')
        with sqlite3.connect(self.root/'workspace-incident-resources.sqlite') as db:
            self.assertEqual(db.execute('SELECT state FROM resource ORDER BY rowid').fetchall(), [('live',),('live',)])
        self.assertEqual(result['shutdown']['status'], 'STOPPED')


if __name__=='__main__':unittest.main()
