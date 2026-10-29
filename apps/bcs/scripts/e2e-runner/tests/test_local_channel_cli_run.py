"""Complete CLI Channel original parent and frozen evidence qualification."""
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

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from bcs_e2e.cli import main
from bcs_e2e.local_channel_cli_contract import CASE_ID, PARENT, REQUIREMENTS, channel_cli_source_binding
from bcs_e2e.local_channel_cli_run import local_channel_cli_run
from bcs_e2e.local_channel_cli_reporting import build_channel_cli_report, _business_evidence
from bcs_e2e.run_state import RunStateError, read
from bcs_e2e.inventory import current_manifest
BINARY = os.environ.get('BCS_E2E_TEST_BINARY','')
SHA = os.environ.get('BCS_E2E_TEST_BINARY_SHA256','')
CLI = os.environ.get('BCS_E2E_TEST_CLI_BINARY','')
CLI_SHA = os.environ.get('BCS_E2E_TEST_CLI_BINARY_SHA256','')


def args(root, **changes):
    return dict(dict(binary=Path(BINARY), binary_sha256=SHA, cli_binary=Path(CLI), cli_binary_sha256=CLI_SHA,
        state_dir=root, run_id='channel-cli-test', confirm_run_id='channel-cli-test', allow_local_processes=True,
        allow_fixture_write=True, allow_write=True), **changes)


class ChannelCliAdmissionTests(unittest.TestCase):
    def test_all_nine_original_steps_and_source_drift(self):
        original = channel_cli_source_binding()
        self.assertEqual(original['parent'],PARENT)
        self.assertEqual([r['id'] for r in original['requirements']],list(REQUIREMENTS))
        for mutation in ('drop','reverse','command','hash'):
            value = copy.deepcopy(original)
            if mutation == 'drop':value['requirements'].pop()
            elif mutation == 'reverse':value['requirements'].reverse()
            elif mutation == 'command':value['requirements'][2]['command']='http group create'
            else:value['sources']['cli-stories.sh']='0'*64
            with self.subTest(mutation=mutation), patch('bcs_e2e.local_channel_cli_contract.read',return_value=value):
                with self.assertRaises(RunStateError):channel_cli_source_binding()

    def test_registry_keeps_http_parent_separate_and_no_release_credit(self):
        from bcs_e2e.migration_inventory import migration_plan
        plan=migration_plan();row=next(r for r in plan['local_registered_executors'] if r['case_id']==CASE_ID)
        self.assertEqual(row['requirement_ids'],list(REQUIREMENTS));self.assertFalse(row['grants_release_credit'])
        self.assertEqual(plan['legacy_inventory_count'],len(current_manifest()['legacy_inventory']));self.assertEqual(plan['migrated_story_count'],0)
        self.assertNotEqual(row['legacy_id'],'story_user_validates_external_channel_setup')

    def test_missing_permissions_precede_actual_binary_or_process(self):
        with tempfile.TemporaryDirectory() as tmp, patch('bcs_e2e.local_channel_cli_run.OwnedBcsProcess') as proc:
            for key,value in (('allow_write',False),('allow_fixture_write',False),('allow_local_processes',False),('confirm_run_id','foreign')):
                with self.subTest(key=key), self.assertRaises(RunStateError):
                    local_channel_cli_run(**args(Path(tmp)/'run',**{key:value}))
            proc.assert_not_called()

    def test_cli_permission_gate_needs_no_remote_profile(self):
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(io.StringIO()) as out:
            rc=main(['local-channel-cli-run','--binary','/unused','--binary-sha256','0'*64,
                '--cli-binary','/unused-cli','--cli-binary-sha256','1'*64,
                '--state-dir',str(Path(tmp)/'run'),'--run-id','denied','--confirm-run-id','denied'])
        self.assertEqual(rc,2);self.assertEqual(json.loads(out.getvalue())['status'],'BLOCKED')


@unittest.skipUnless(BINARY and SHA and CLI and CLI_SHA and shutil.which('lsof'),'actual BCS and CLI required')
class ChannelCliLiveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=tempfile.TemporaryDirectory();cls.addClassCleanup(cls.tmp.cleanup)
        cls.root=Path(cls.tmp.name)/'run';cls.result=local_channel_cli_run(**args(cls.root))
        if cls.result['status'] != 'LOCAL_CHANNEL_CLI_PASS':raise AssertionError(json.dumps(cls.result,indent=2))

    def test_nine_obligations_actual_cli_and_no_fullrelease_credit(self):
        result=self.result;summary=result['canonical_summary']
        self.assertEqual(summary['status'],'LOCAL_SUITE_PASS')
        self.assertEqual(summary['requirements'],[{'id':x,'status':'PASS'} for x in REQUIREMENTS])
        self.assertEqual(summary['reports']['expected_assertion_count'],9)
        self.assertEqual(summary['reports']['observed_assertion_count'],9)
        self.assertEqual(summary['scope']['full_release_status'],'NOT_QUALIFIED')
        self.assertEqual(result['shutdown']['status'],'STOPPED')
        self.assertEqual(result['credentials']['credential_memory'],'CLEARED')
        operations=result['case']['operations']
        self.assertEqual(sum(r.get('transport')=='PRODUCT_CLI' for r in operations),7)
        checks={r['id']:r['evidence'] for r in result['case']['checks']}
        self.assertEqual(checks['retained-history-before']['row_count'],4)
        self.assertEqual(checks['group-delete-proof']['retained_history'],checks['retained-history-before'])
        self.assertEqual(result['case']['preservation']['unrelated']['before'],
                         result['case']['preservation']['unrelated']['after'])
        junit=ET.parse(self.root/'reports/junit.xml').getroot()
        self.assertEqual(junit.attrib['name'],'bcs-e2e-channel-cli')
        self.assertEqual([junit.attrib[k] for k in ('tests','failures','errors','skipped')],['1','0','0','0'])
        for name in REQUIREMENTS:self.assertIn(name,(self.root/'reports/summary.md').read_text())

    def test_offline_read_rejects_corruption_without_overwrite(self):
        before={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in self.root.rglob('*') if p.is_file()}
        with patch('http.client.HTTPConnection.request',side_effect=AssertionError('offline')):
            self.assertEqual(build_channel_cli_report(self.root),self.result['canonical_summary'])
        receipt=read(self.root/'channel-cli-completion.json')
        for name in [*receipt['sources'],*receipt['outputs']]:
            p=self.root/name;raw=p.read_bytes()
            try:
                p.write_bytes(raw+b' ')
                try:value=build_channel_cli_report(self.root)
                except (RunStateError,ValueError):continue
                self.assertNotEqual(value['status'],'LOCAL_SUITE_PASS')
            finally:p.write_bytes(raw)
        self.assertEqual(before,{str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in self.root.rglob('*') if p.is_file()})
        with contextlib.redirect_stdout(io.StringIO()):
            rc=main(['local-channel-cli-report','--state-dir',str(self.root),'--output-dir',str(self.root/'reports')])
        self.assertEqual(rc,2)

    def test_semantic_projection_rejects_missing_cli_unknown_and_foreign_delete(self):
        binding=read(self.root/'channel-cli-binding.json');case=self.result['case']
        self.assertTrue(_business_evidence(self.root,binding,case))
        for edit in ('missing','unknown','duplicate','fake-cli'):
            value=copy.deepcopy(case)
            if edit=='missing':value['operations'].pop()
            elif edit=='unknown':value['operations'][0]['status']='UNKNOWN'
            elif edit=='duplicate':value['operations'][1]['submitted_request_id']=value['operations'][0]['submitted_request_id']
            else:
                next(r for r in value['operations'] if r.get('transport')=='PRODUCT_CLI')['transport']='HTTP'
            with self.subTest(edit=edit):self.assertFalse(_business_evidence(self.root,binding,value))


@unittest.skipUnless(BINARY and SHA and CLI and CLI_SHA and shutil.which('lsof'),'actual BCS and CLI required')
class ChannelCliFailureTests(unittest.TestCase):
    def test_cancellation_preserves_scope_and_stops_child(self):
        with tempfile.TemporaryDirectory() as tmp, patch('bcs_e2e.local_channel_cli.OwnedLocalChannelCli.run',side_effect=KeyboardInterrupt):
            result=local_channel_cli_run(**args(Path(tmp)/'run'))
            self.assertEqual(result['status'],'CANCELLED');self.assertEqual(result['case_id'],CASE_ID)
            self.assertEqual(result['shutdown']['status'],'STOPPED')
            self.assertEqual(result['canonical_summary']['status'],'CANCELLED')
