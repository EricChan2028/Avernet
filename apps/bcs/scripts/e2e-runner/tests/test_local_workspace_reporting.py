"""Canonical Workspace reports are projections of frozen actual local evidence."""
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import socket
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from bcs_e2e.cli import main
from bcs_e2e.local_workspace_reporting import build_workspace_report, _project, _bound, _sha, REPORT_NAME
from bcs_e2e.profile import fingerprint
from bcs_e2e.run_state import read, write, RunStateError

BINARY=os.environ.get('BCS_E2E_TEST_BINARY','')
DIGEST=os.environ.get('BCS_E2E_TEST_BINARY_SHA256','')
CLI=os.environ.get('BCS_E2E_TEST_CLI_BINARY','')
CLI_DIGEST=os.environ.get('BCS_E2E_TEST_CLI_BINARY_SHA256','')


def args(root):
    return ['local-workspace-run','--with-incident-delete','--state-dir',str(root),
            '--run-id','workspace-report','--confirm-run-id','workspace-report',
            '--binary',BINARY,'--binary-sha256',DIGEST,'--cli-binary',CLI,'--cli-binary-sha256',CLI_DIGEST,
            '--allow-local-processes','--allow-fixture-write','--allow-write']


@unittest.skipUnless(BINARY and DIGEST and CLI and CLI_DIGEST and shutil.which('lsof'),
                     'approved actual BCS and CLI required')
class WorkspaceReportingLiveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.tmp.cleanup)
        cls.root=Path(cls.tmp.name)/'runtime'
        with contextlib.redirect_stdout(io.StringIO()) as output:
            rc=main(args(cls.root))
        cls.result=json.loads(output.getvalue())
        if rc!=0 or cls.result.get('canonical_status')!='LOCAL_SUITE_PASS':
            raise AssertionError(cls.result)

    def copy(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        target=(Path(tmp.name)/'copy').resolve();shutil.copytree(self.root,target)
        binding=read(target/'workspace-binding.json');binding['runtime_dir']=str(target)
        write(target/'workspace-binding.json',binding)
        completion=read(target/'workspace-completion.json')
        completion['binding_digest']=fingerprint(binding)
        completion['sources']['workspace-binding.json']=_sha(target/'workspace-binding.json')
        write(target/'workspace-completion.json',completion)
        return target

    def test_actual_three_formats_and_offline_read_are_consistent(self):
        with patch.object(socket,'create_connection',side_effect=AssertionError('offline must not connect')):
            summary=build_workspace_report(self.root)
        self.assertEqual(summary,self.result['canonical_summary'])
        self.assertEqual(summary['reports'],{'expected_assertion_count':17,'observed_assertion_count':17})
        self.assertEqual(summary['cases'][0]['status'],'PASS')
        self.assertEqual(summary['scope']['full_release_status'],'NOT_QUALIFIED')
        xml=ET.parse(self.root/'reports'/'junit.xml').getroot()
        self.assertEqual(xml.attrib['tests'],'1')
        self.assertEqual(xml.attrib['failures'],'0')
        self.assertEqual(xml.attrib['skipped'],'0')
        self.assertIn('WORKSPACE.incident-delete',(self.root/'reports'/'summary.md').read_text())
        self.assertEqual(read(self.root/'reports'/'summary.json'),summary)
        self.assertEqual(summary['cases'][0]['retained_visibility']['restoration'],'NOT_PERFORMED')

    def test_each_canonical_output_mutation_or_deletion_blocks(self):
        for leaf in ('summary.json','junit.xml','summary.md'):
            for action in ('mutate','delete'):
                with self.subTest(leaf=leaf,action=action):
                    root=self.copy();p=root/'reports'/leaf
                    if action=='mutate':p.write_text('changed')
                    else:p.unlink()
                    self.assertEqual(build_workspace_report(root)['status'],'BLOCKED')

    def test_later_executable_inventory_change_does_not_break_frozen_v1_read(self):
        with patch('bcs_e2e.local_workspace_run.REQUIREMENTS', ('FUTURE.requirement',)), \
             patch('bcs_e2e.local_workspace_cli.COMMAND_SEQUENCE', ('future-command',)), \
             patch('bcs_e2e.local_workspace_api.WRITE_SEQUENCE', ('future-write',)):
            self.assertEqual(build_workspace_report(self.root)['status'],'LOCAL_SUITE_PASS')

    def test_source_mutation_blocks_offline_pass(self):
        root=self.copy()
        journal=read(root/'local-workspace-messages.json');journal['wire_receipts'].pop()
        write(root/'local-workspace-messages.json',journal)
        result=build_workspace_report(root)
        self.assertEqual(result['status'],'BLOCKED')
        self.assertEqual(result['cases'][0]['status'],'BLOCKED')

    def test_pending_marker_blocks_even_complete_results(self):
        root=self.copy();write(root/'workspace-publication-pending.json',{'schema_version':1,'run_id':'workspace-report'})
        self.assertEqual(build_workspace_report(root)['status'],'BLOCKED')

    def test_ledger_drift_blocks_and_is_never_repaired(self):
        root=self.copy();p=root/'workspace-incident-resources.sqlite'
        with sqlite3.connect(p) as db:db.execute("UPDATE resource SET state='live'")
        before=p.read_bytes()
        result=build_workspace_report(root)
        self.assertEqual(result['status'],'BLOCKED')
        self.assertEqual(p.read_bytes(),before)
        # Even a refreshed file hash cannot manufacture semantic deletion evidence.
        completion=read(root/'workspace-completion.json');completion['sources'][p.name]=_sha(p)
        write(root/'workspace-completion.json',completion)
        self.assertEqual(build_workspace_report(root)['status'],'BLOCKED')

    def test_missing_obligation_does_not_pass_when_outer_flow_claims_success(self):
        root=self.copy();flow=read(root/'workspace-incident-roundtrip-report.json')
        journal=read(root/'local-workspace-messages.json');journal['assertions']=[]
        flow['message_case']=journal
        write(root/'local-workspace-messages.json',journal);write(root/'workspace-incident-roundtrip-report.json',flow)
        case=_project(root,_bound(root))
        self.assertEqual(case['status'],'BLOCKED')
        self.assertEqual(next(r for r in case['requirements'] if r['id']=='WORKSPACE.messages')['status'],'NOT_RUN')

    def test_changed_fusion_input_blocks(self):
        root=self.copy();context=read(root/'workspace-fusion-context.json')
        p=root/'bots'/context['roles']['PM']/'MEMORY.md';p.write_text('changed')
        self.assertEqual(build_workspace_report(root)['status'],'BLOCKED')

    def test_legacy_execution_report_without_publication_cannot_be_upgraded(self):
        root=self.copy();(root/'workspace-completion.json').unlink()
        self.assertEqual(build_workspace_report(root)['status'],'BLOCKED')

    def test_offline_export_refuses_to_overwrite_original_artifacts(self):
        original=(self.root/'reports'/'summary.json').read_bytes()
        with contextlib.redirect_stdout(io.StringIO()) as output:
            rc=main(['local-workspace-report','--state-dir',str(self.root),
                     '--output-dir',str(self.root/'reports')])
        self.assertEqual(rc,2)
        self.assertEqual(json.loads(output.getvalue())['status'],'BLOCKED')
        self.assertEqual((self.root/'reports'/'summary.json').read_bytes(),original)

    def test_offline_cli_does_not_change_original_evidence(self):
        before={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in self.root.iterdir() if p.is_file()}
        with contextlib.redirect_stdout(io.StringIO()) as output, patch.object(socket,'create_connection',side_effect=AssertionError('no network')):
            rc=main(['local-workspace-report','--state-dir',str(self.root)])
        self.assertEqual(rc,0)
        self.assertEqual(json.loads(output.getvalue())['status'],'LOCAL_SUITE_PASS')
        after={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in self.root.iterdir() if p.is_file()}
        self.assertEqual(after,before)


@unittest.skipUnless(BINARY and DIGEST and CLI and CLI_DIGEST and shutil.which('lsof'),
                     'approved actual BCS and CLI required')
class WorkspacePublicationFailureTests(unittest.TestCase):
    def test_failed_output_write_keeps_pending_and_child_stopped(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'runtime'
            with patch('bcs_e2e.reporting.write_reports',side_effect=OSError('synthetic output failure')),contextlib.redirect_stdout(io.StringIO()) as output:
                rc=main(args(root))
            self.assertEqual(rc,2)
            self.assertEqual(json.loads(output.getvalue())['status'],'BLOCKED')
            self.assertEqual(read(root/'owned-bcs.json')['status'],'STOPPED')
            self.assertTrue((root/'workspace-publication-pending.json').is_file())
            self.assertFalse((root/'workspace-completion.json').exists())
            self.assertEqual(build_workspace_report(root)['status'],'BLOCKED')


if __name__=='__main__':unittest.main()
