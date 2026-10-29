"""Full original Direct Chat parent; API cancellation is not engine completion."""
import contextlib
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from bcs_e2e.cli import main
from bcs_e2e.local_direct_chat_contract import CASE_ID,PARENT,REQUIREMENTS,direct_chat_source_binding
from bcs_e2e.local_direct_chat_run import local_direct_chat_run
from bcs_e2e.local_direct_chat_reporting import build_direct_chat_report,_business_evidence
from bcs_e2e.run_state import RunStateError,read
from bcs_e2e.inventory import current_manifest
BINARY=os.environ.get('BCS_E2E_TEST_BINARY','');SHA=os.environ.get('BCS_E2E_TEST_BINARY_SHA256','')


def arguments(root,**changes):
    return dict(dict(binary=Path(BINARY),binary_sha256=SHA,state_dir=root,run_id='direct-chat-test',
        confirm_run_id='direct-chat-test',allow_local_processes=True,allow_fixture_write=True,allow_write=True),**changes)


class DirectChatAdmissionTests(unittest.TestCase):
    def test_original_ping_and_main_four_steps_remain_required(self):
        original=direct_chat_source_binding()
        self.assertEqual(original['parent'],PARENT)
        self.assertEqual([r['id'] for r in original['requirements']],list(REQUIREMENTS))
        for edit in ('missing-ping','reorder','method','hash'):
            value=copy.deepcopy(original)
            if edit=='missing-ping':value['requirements'].pop(0)
            elif edit=='reorder':value['requirements'].reverse()
            elif edit=='method':value['requirements'][3]['method']='DELETE'
            else:value['sources']['actor.sh']='0'*64
            with self.subTest(edit=edit),patch('bcs_e2e.local_direct_chat_contract.read',return_value=value):
                with self.assertRaises(RunStateError):direct_chat_source_binding()

    def test_registry_preserves_release_scope(self):
        from bcs_e2e.migration_inventory import migration_plan
        plan=migration_plan();row=next(r for r in plan['local_registered_executors'] if r['case_id']==CASE_ID)
        self.assertEqual(row['requirement_ids'],list(REQUIREMENTS));self.assertFalse(row['grants_release_credit'])
        self.assertEqual(plan['legacy_inventory_count'],len(current_manifest()['legacy_inventory']));self.assertEqual(plan['migrated_story_count'],0)

    def test_permissions_confirmation_and_fresh_directory_precede_runtime(self):
        with tempfile.TemporaryDirectory() as tmp,patch('bcs_e2e.local_direct_chat_run.OwnedBcsProcess') as runtime:
            for key,value in (('allow_local_processes',False),('allow_fixture_write',False),('allow_write',False),('confirm_run_id','foreign')):
                with self.subTest(key=key),self.assertRaises(RunStateError):local_direct_chat_run(**arguments(Path(tmp)/'run',**{key:value}))
            with self.assertRaises(RunStateError):local_direct_chat_run(**arguments(Path(tmp)))
            runtime.assert_not_called()

    def test_entrypoint_permission_denial_needs_no_remote_profile(self):
        with tempfile.TemporaryDirectory() as tmp,contextlib.redirect_stdout(io.StringIO()) as out:
            rc=main(['local-direct-chat-run','--binary','/unused','--binary-sha256','0'*64,
                '--state-dir',str(Path(tmp)/'run'),'--run-id','denied','--confirm-run-id','denied'])
        self.assertEqual(rc,2);self.assertEqual(json.loads(out.getvalue())['status'],'BLOCKED')


@unittest.skipUnless(BINARY and SHA and shutil.which('lsof'),'approved actual BCS required')
class DirectChatLiveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=tempfile.TemporaryDirectory();cls.addClassCleanup(cls.tmp.cleanup)
        cls.root=Path(cls.tmp.name)/'run';cls.result=local_direct_chat_run(**arguments(cls.root))
        if cls.result['status']!='LOCAL_DIRECT_CHAT_PASS':raise AssertionError(json.dumps(cls.result,indent=2))

    def test_original_parent_complete_but_not_release_or_engine_success(self):
        result=self.result;summary=result['canonical_summary']
        self.assertEqual(summary['status'],'LOCAL_SUITE_PASS')
        self.assertEqual(summary['requirements'],[{'id':x,'status':'PASS'} for x in REQUIREMENTS])
        self.assertEqual(summary['reports']['expected_assertion_count'],5)
        self.assertEqual(summary['reports']['observed_assertion_count'],5)
        self.assertEqual(summary['scope']['full_release_status'],'NOT_QUALIFIED')
        self.assertEqual(result['shutdown']['status'],'STOPPED')
        self.assertEqual(result['credentials']['credential_memory'],'CLEARED')
        junit=ET.parse(self.root/'reports/junit.xml').getroot()
        self.assertEqual(junit.attrib['name'],'bcs-e2e-direct-chat')
        self.assertEqual([junit.attrib[k] for k in ('tests','failures','errors','skipped')],['1','0','0','0'])
        for requirement in REQUIREMENTS:self.assertIn(requirement,(self.root/'reports/summary.md').read_text())

    def test_offline_report_matches_without_network_or_artifact_changes(self):
        before={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in self.root.rglob('*') if p.is_file()}
        with patch('http.client.HTTPConnection.request',side_effect=AssertionError('offline')):
            self.assertEqual(build_direct_chat_report(self.root),self.result['canonical_summary'])
        self.assertEqual(before,{str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in self.root.rglob('*') if p.is_file()})
        receipt=read(self.root/'direct-chat-completion.json')
        for name in [*receipt['sources'],*receipt['outputs']]:
            path=self.root/name;raw=path.read_bytes()
            try:
                path.write_bytes(raw+b' ')
                try:value=build_direct_chat_report(self.root)
                except (RunStateError,ValueError):continue
                self.assertNotEqual(value['status'],'LOCAL_SUITE_PASS')
            finally:path.write_bytes(raw)
        with contextlib.redirect_stdout(io.StringIO()):
            rc=main(['local-direct-chat-report','--state-dir',str(self.root),'--output-dir',str(self.root/'reports')])
        self.assertEqual(rc,2)

    def test_missing_or_unknown_operations_cannot_project_as_pass(self):
        binding=read(self.root/'direct-chat-binding.json');case=self.result['case']
        self.assertTrue(_business_evidence(self.root,binding,case))
        for mutation in ('missing','unknown','duplicate'):
            value=copy.deepcopy(case)
            if mutation=='missing':value['operations'].pop()
            elif mutation=='unknown':value['operations'][0]['status']='UNKNOWN'
            else:value['operations'][1]['submitted_request_id']=value['operations'][0]['submitted_request_id']
            with self.subTest(mutation=mutation):self.assertFalse(_business_evidence(self.root,binding,value))


@unittest.skipUnless(BINARY and SHA and shutil.which('lsof'),'approved actual BCS required')
class DirectChatCancellationTests(unittest.TestCase):
    def test_cancellation_stops_owned_runtime_and_never_qualifies_full_story(self):
        with tempfile.TemporaryDirectory() as tmp,patch('bcs_e2e.local_direct_chat.OwnedLocalDirectChat.run',side_effect=KeyboardInterrupt):
            result=local_direct_chat_run(**arguments(Path(tmp)/'run'))
            self.assertEqual(result['status'],'CANCELLED');self.assertEqual(result['case_id'],CASE_ID)
            self.assertEqual(result['shutdown']['status'],'STOPPED')
            self.assertEqual(result['canonical_summary']['status'],'CANCELLED')
