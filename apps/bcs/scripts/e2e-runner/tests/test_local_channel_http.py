"""Full HTTP Channel parent, finite no-op admission and offline report evidence."""
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
from bcs_e2e.local_channel_contract import CASE_ID, PARENT, REQUIREMENTS, channel_source_binding
from bcs_e2e.local_channel_http import OwnedLocalChannelHttp
from bcs_e2e.local_channel_run import local_channel_http_run
from bcs_e2e.local_channel_reporting import build_channel_http_report, _business_evidence
from bcs_e2e.run_state import RunStateError, read
BINARY = os.environ.get('BCS_E2E_TEST_BINARY','')
SHA = os.environ.get('BCS_E2E_TEST_BINARY_SHA256','')


def args(root, **changes):
    return dict(dict(binary=Path(BINARY), binary_sha256=SHA, state_dir=root,
                     run_id='channel-test', confirm_run_id='channel-test', allow_local_processes=True,
                     allow_fixture_write=True, allow_write=True), **changes)


class ChannelGuards(unittest.TestCase):
    def test_original_complete_source_map_and_drift_rejection(self):
        original = channel_source_binding()
        self.assertEqual(original['parent'],PARENT)
        self.assertEqual([x['id'] for x in original['requirements']],list(REQUIREMENTS))
        for mutation in ('missing','reverse','method','path','hash'):
            value = copy.deepcopy(original)
            if mutation == 'missing': value['requirements'].pop()
            elif mutation == 'reverse': value['requirements'].reverse()
            elif mutation == 'method': value['requirements'][4]['method'] = 'POST'
            elif mutation == 'path': value['requirements'][4]['path'] = '/groups'
            else: value['sources']['stories.sh'] = '0'*64
            with self.subTest(mutation=mutation), patch('bcs_e2e.local_channel_contract.read',return_value=value):
                with self.assertRaises(RunStateError): channel_source_binding()

    def test_registry_keeps_cli_parent_separate_and_no_release_credit(self):
        from bcs_e2e.migration_inventory import migration_plan
        plan = migration_plan()
        row = next(r for r in plan['local_registered_executors'] if r['case_id'] == CASE_ID)
        self.assertEqual(row['requirement_ids'],list(REQUIREMENTS))
        self.assertFalse(row['grants_release_credit'])
        self.assertEqual(plan['legacy_inventory_count'],34)
        self.assertEqual(plan['migrated_story_count'],0)
        self.assertNotEqual(row['legacy_id'],'story_cli_operator_validates_channel_management')

    def test_permissions_and_fresh_directory_precede_process_start(self):
        with tempfile.TemporaryDirectory() as tmp, patch('bcs_e2e.local_channel_run.OwnedBcsProcess') as proc:
            for key,value in (('allow_write',False),('allow_fixture_write',False),('allow_local_processes',False),('confirm_run_id','other')):
                with self.subTest(key=key), self.assertRaises(RunStateError):
                    local_channel_http_run(**args(Path(tmp)/'run',**{key:value}))
            with self.assertRaises(RunStateError): local_channel_http_run(**args(Path(tmp)))
            proc.assert_not_called()

    def test_unmanaged_identity_and_missing_cli_permissions_refused(self):
        with self.assertRaises(RunStateError): OwnedLocalChannelHttp(object())
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(io.StringIO()) as out:
            rc = main(['local-channel-http-run','--binary','/unused','--binary-sha256','0'*64,
                       '--state-dir',str(Path(tmp)/'run'),'--run-id','denied','--confirm-run-id','denied'])
        self.assertEqual(rc,2); self.assertEqual(json.loads(out.getvalue())['status'],'BLOCKED')

    def test_wire_refuses_foreign_path_and_duplicate_without_dispatch(self):
        case = object.__new__(OwnedLocalChannelHttp)
        case._active = lambda: None
        case._claims = set()
        case._spec = lambda _: ('DELETE','/channels/bindings/missing',None,200)
        row = {'operation':REQUIREMENTS[4],'method':'DELETE','path':'/channels/bindings/borrowed',
               'submitted_request_id':'request','status':'INTENDED'}
        case.missing = 'missing'
        before = {'empty':True}
        case.state = {'operations':[{'operation':name} for name in REQUIREMENTS[:4]]+[row],
            'assertions':[{'id':name,'status':'PASS'} for name in REQUIREMENTS[:4]],
            'fixture_state':{'before':before},'no_op_admissions':[{'operation':REQUIREMENTS[4],
            'binding_id':'missing','basis':'DISABLED_BRIDGE_AND_EMPTY_OWNED_DATABASE','snapshot':before}]}
        with patch('bcs_e2e.local_channel_http.HTTPConnection') as conn:
            with self.assertRaises(RunStateError): case._wire(4,row)
            row['path'] = '/channels/bindings/missing'; case._claims.add('request')
            with self.assertRaises(RunStateError): case._wire(4,row)
            conn.assert_not_called()


@unittest.skipUnless(BINARY and SHA and shutil.which('lsof'),'approved real BCS required')
class ChannelLive(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory();cls.addClassCleanup(cls.tmp.cleanup)
        cls.root = Path(cls.tmp.name)/'run';cls.result = local_channel_http_run(**args(cls.root))
        if cls.result['status'] != 'LOCAL_CHANNEL_HTTP_PASS': raise AssertionError(json.dumps(cls.result,indent=2))

    def test_all6_steps_no_resource_deletion_and_actual_stop(self):
        result = self.result;summary = result['canonical_summary']
        self.assertEqual(summary['status'],'LOCAL_SUITE_PASS')
        self.assertEqual(summary['requirements'],[{'id':x,'status':'PASS'} for x in REQUIREMENTS])
        self.assertEqual(summary['scope']['full_release_status'],'NOT_QUALIFIED')
        self.assertEqual(result['case']['resources'],{'created':[],'deleted':[],'unknown':[]})
        self.assertEqual(result['shutdown']['status'],'STOPPED')
        self.assertEqual(result['credentials']['credential_memory'],'CLEARED')
        self.assertEqual(len(result['case']['no_op_admissions']),2)
        junit = ET.parse(self.root/'reports/junit.xml').getroot()
        self.assertEqual(junit.attrib['name'],'bcs-e2e-channel-http')
        self.assertEqual([junit.attrib[k] for k in ('tests','failures','errors','skipped')],['1','0','0','0'])
        for name in REQUIREMENTS:self.assertIn(name,(self.root/'reports/summary.md').read_text())

    def test_offline_read_only_and_source_corruption_rejected(self):
        before = {str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in self.root.rglob('*') if p.is_file()}
        with patch('http.client.HTTPConnection.request',side_effect=AssertionError('offline')):
            self.assertEqual(build_channel_http_report(self.root),self.result['canonical_summary'])
        receipt = read(self.root/'channel-http-completion.json')
        for name in [*receipt['sources'],*receipt['outputs']]:
            p = self.root/name;raw = p.read_bytes()
            try:
                p.write_bytes(raw+b' ')
                try: value = build_channel_http_report(self.root)
                except (RunStateError,ValueError): continue
                self.assertNotEqual(value['status'],'LOCAL_SUITE_PASS')
            finally:p.write_bytes(raw)
        self.assertEqual(before,{str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in self.root.rglob('*') if p.is_file()})
        with contextlib.redirect_stdout(io.StringIO()):
            rc = main(['local-channel-http-report','--state-dir',str(self.root),'--output-dir',str(self.root/'reports')])
        self.assertEqual(rc,2)

    def test_semantic_projection_rejects_unknown_foreign_delete_or_missing_admission(self):
        binding = read(self.root/'channel-http-binding.json');case = self.result['case']
        self.assertTrue(_business_evidence(self.root,binding,case))
        for edit in ('unknown','foreign-delete','missing-admission','created-resource','fixture-drift','duplicate-request'):
            value = copy.deepcopy(case)
            if edit == 'unknown':value['operations'][0]['status']='UNKNOWN'
            elif edit == 'foreign-delete':value['operations'][4]['path']='/channels/bindings/foreign'
            elif edit == 'missing-admission':value['no_op_admissions'].pop()
            elif edit == 'created-resource':value['resources']['created']=['unexpected']
            elif edit == 'fixture-drift':value['fixture_state']['preserved']=False
            else:value['operations'][1]['submitted_request_id']=value['operations'][0]['submitted_request_id']
            with self.subTest(edit=edit):self.assertFalse(_business_evidence(self.root,binding,value))


@unittest.skipUnless(BINARY and SHA and shutil.which('lsof'),'approved real BCS required')
class ChannelFailures(unittest.TestCase):
    def test_lost_reply_never_retries_or_runs_noop_delete(self):
        original = OwnedLocalChannelHttp._wire
        calls=[]
        def lost(case,index,row):
            calls.append(index);original(case,index,row);raise OSError('injected lost response')
        with tempfile.TemporaryDirectory() as tmp, patch.object(OwnedLocalChannelHttp,'_wire',lost):
            root=Path(tmp)/'run';result=local_channel_http_run(**args(root))
            self.assertEqual(result['status'],'BLOCKED');self.assertEqual(calls,[0])
            self.assertEqual(result['case']['operations'][0]['status'],'UNKNOWN')
            self.assertEqual(result['case']['no_op_admissions'],[])
            self.assertEqual(len(result['case']['resources']['unknown']),1)
            self.assertEqual(result['shutdown']['status'],'STOPPED')

    def test_failed_absence_proof_blocks_before_any_business_request(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(OwnedLocalChannelHttp,'_snapshot',side_effect=RunStateError('occupied')):
            result=local_channel_http_run(**args(Path(tmp)/'run'))
            self.assertEqual(result['status'],'BLOCKED')
            self.assertEqual(result['case']['operations'],[])
            self.assertEqual(result['shutdown']['status'],'STOPPED')
