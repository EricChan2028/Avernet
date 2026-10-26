"""Complete local Workspace original obligations and failure boundaries."""
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

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from bcs_e2e.cli import main
from bcs_e2e.local_workspace_proposal import OwnedLocalWorkspaceProposal, REDACTED_CONFIRM_PATH
from bcs_e2e.run_state import read, RunStateError

BINARY=os.environ.get('BCS_E2E_TEST_BINARY','')
DIGEST=os.environ.get('BCS_E2E_TEST_BINARY_SHA256','')
CLI=os.environ.get('BCS_E2E_TEST_CLI_BINARY','')
CLI_DIGEST=os.environ.get('BCS_E2E_TEST_CLI_BINARY_SHA256','')


@unittest.skipUnless(BINARY and DIGEST and CLI and CLI_DIGEST and shutil.which('lsof'),
                     'approved actual BCS and CLI required')
class WorkspaceFlowLiveTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name)/'runtime'

    def execute(self):
        args=['local-workspace-run','--with-proposal','--state-dir',str(self.root),
              '--run-id','workspace-flow','--confirm-run-id','workspace-flow',
              '--binary',BINARY,'--binary-sha256',DIGEST,'--cli-binary',CLI,'--cli-binary-sha256',CLI_DIGEST,
              '--allow-local-processes','--allow-fixture-write','--allow-write']
        with contextlib.redirect_stdout(io.StringIO()) as output:rc=main(args)
        return rc,json.loads(output.getvalue())

    def test_actual_eighteen_original_obligations_with_two_exact_deletions(self):
        capabilities=[]
        original=OwnedLocalWorkspaceProposal._html_request
        def capture(case,path,expected):
            capabilities.append(path)
            return original(case,path,expected)
        with patch.object(OwnedLocalWorkspaceProposal,'_html_request',capture):rc,result=self.execute()
        self.assertEqual(rc,0,result)
        self.assertEqual(result['status'],'LOCAL_WORKSPACE_FLOW_PASS')
        self.assertEqual(result['selected_requirement_count'],18)
        self.assertEqual([r['status'] for r in result['requirements']],['PASS']*18)
        self.assertEqual(result['full_story_status'],'NOT_MIGRATED')
        self.assertEqual(result['proposal_delete']['status'],'LOCAL_WORKSPACE_PROPOSAL_DELETE_PASS')
        self.assertEqual(result['proposal_case']['capability']['memory'],'CLEARED_REFERENCE_ONLY')
        self.assertEqual(result['proposal_case']['html_validation']['action'],REDACTED_CONFIRM_PATH)
        self.assertEqual(result['shutdown']['status'],'STOPPED')
        self.assertEqual(result['credentials']['credential_memory'],'CLEARED')
        for path in capabilities:
            self.assertNotIn(path,json.dumps(result))
            self.assertNotIn(path.split('/')[2],json.dumps(result))
        with sqlite3.connect(self.root/'bcs.sqlite') as db:
            self.assertEqual(db.execute('SELECT count(*) FROM bcs_groups').fetchone()[0],4)
            self.assertEqual(db.execute('SELECT count(*) FROM bcs_group_sessions').fetchone()[0],4)
        for filename in ('workspace-proposal-resources.sqlite','workspace-incident-resources.sqlite'):
            with sqlite3.connect(self.root/filename) as db:
                self.assertEqual(db.execute('SELECT state FROM resource ORDER BY rowid').fetchall(),[('deleted',),('deleted',)])

    def test_html_mismatch_blocks_confirmation_and_never_grants_proposal(self):
        with patch('bcs_e2e.local_workspace_proposal.validate_confirmation_html',side_effect=RunStateError('synthetic wrong form')):
            rc,result=self.execute()
        self.assertEqual(rc,2)
        case=read(self.root/'local-workspace-proposal.json')
        self.assertEqual([r['operation'] for r in case['operations']],['proposal-create','proposal-preview'])
        self.assertFalse((self.root/'local-workspace-proposal-delete.json').exists())
        self.assertEqual(result['requirements'][-1]['status'],'NOT_RUN')
        self.assertEqual(result['shutdown']['status'],'STOPPED')

    def test_lost_confirmation_response_preserves_unknown_handles_without_retry_or_delete(self):
        original=OwnedLocalWorkspaceProposal._record_intent
        calls=[]
        def intercept(case,*args,**kwargs):
            event=original(case,*args,**kwargs)
            if event['operation']=='proposal-confirm':
                opener=case.identities.opener;real_open=opener.open
                def lose(request,*a,**kw):
                    response=real_open(request,*a,**kw)
                    if request.get_method()=='POST' and request.full_url.endswith('/confirm'):
                        calls.append(1);response.close();raise OSError('synthetic lost confirmation response')
                    return response
                opener.open=lose
            return event
        with patch.object(OwnedLocalWorkspaceProposal,'_record_intent',intercept):rc,result=self.execute()
        self.assertEqual(rc,2)
        self.assertEqual(calls,[1])
        self.assertEqual(result['proposal_case']['operations'][-1]['status'],'UNKNOWN')
        self.assertFalse((self.root/'local-workspace-proposal-delete.json').exists())
        with sqlite3.connect(self.root/'workspace-proposal-resources.sqlite') as db:
            self.assertEqual(db.execute('SELECT origin,state FROM resource ORDER BY rowid').fetchall(),
                             [('unknown','unknown'),('unknown','unknown')])
        with sqlite3.connect(self.root/'bcs.sqlite') as db:
            self.assertEqual(db.execute('SELECT count(*) FROM bcs_groups').fetchone()[0],5)
        self.assertEqual(result['shutdown']['status'],'STOPPED')

    def test_proposal_ledger_drift_is_not_deletion_authority(self):
        original=OwnedLocalWorkspaceProposal.run_create_confirm
        def drift(case):
            result=original(case)
            with sqlite3.connect(case.ledger_path) as db:
                db.execute("UPDATE resource SET origin='borrowed' WHERE kind='group'")
            return result
        with patch.object(OwnedLocalWorkspaceProposal,'run_create_confirm',drift):rc,result=self.execute()
        self.assertEqual(rc,2)
        with sqlite3.connect(self.root/'bcs.sqlite') as db:
            self.assertEqual(db.execute('SELECT count(*) FROM bcs_groups').fetchone()[0],5)
        self.assertFalse((self.root/'local-workspace-proposal-delete.json').exists())
        self.assertEqual(result['shutdown']['status'],'STOPPED')


if __name__=='__main__':unittest.main()
