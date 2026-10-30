"""Original full Structured parent, lifecycle guards and frozen offline evidence."""
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

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from bcs_e2e.cli import main
from bcs_e2e.local_structured import OwnedLocalStructured, ORDER, INSTRUCTION, REVISED
from bcs_e2e.local_structured_contract import REQUIREMENTS, CASE_ID
from bcs_e2e.local_structured_run import local_structured_run
from bcs_e2e.local_structured_reporting import build_structured_report, _business_evidence
from bcs_e2e.run_state import read, RunStateError
from tests.test_local_template_runtime import approved_asset

BINARY=os.environ.get('BCS_E2E_TEST_BINARY','');SHA=os.environ.get('BCS_E2E_TEST_BINARY_SHA256','')


def arguments(root,asset,**changes):
    return dict(dict(binary=Path(BINARY),binary_sha256=SHA,state_dir=root,run_id='structured-test',
        confirm_run_id='structured-test',allow_local_processes=True,allow_fixture_write=True,allow_write=True,
        template_dir=asset.directory,template_manifest=asset.manifest_path,template_manifest_sha256=asset.manifest_sha256),**changes)


class StructuredGuards(unittest.TestCase):
    def test_permissions_and_fresh_directory_precede_spawn_or_asset_validation(self):
        with tempfile.TemporaryDirectory() as tmp,patch('bcs_e2e.local_structured_run.OwnedBcsProcess') as process:
            root=Path(tmp);asset=approved_asset(root)
            for key,value in [('allow_local_processes',False),('allow_fixture_write',False),('allow_write',False),('confirm_run_id','foreign')]:
                with self.subTest(key=key),self.assertRaises(RunStateError):local_structured_run(**arguments(root/'run',asset,**{key:value}))
            with self.assertRaises(RunStateError):local_structured_run(**arguments(root,asset))
            process.assert_not_called()

    def test_identity_scope_and_original_implicit_execution_are_not_suppressed(self):
        with self.assertRaises(RunStateError):OwnedLocalStructured(object())
        case=object.__new__(OwnedLocalStructured)
        case.state={};case.ceo='ceo';case.pm='pm'
        from bcs_e2e.local_structured_contract import structured_source_binding
        case.source_binding=structured_source_binding()
        method,path,body,status,actor=case._spec(REQUIREMENTS[2])
        self.assertEqual((method,path,status,actor),('POST','/groups',200,'CEO'))
        self.assertNotIn('start_initial_run',body);self.assertNotIn('create_initial_session',body)
        self.assertIn(INSTRUCTION,body['collaboration_definition_yaml'])
        self.assertEqual([x for x in ORDER if x in REQUIREMENTS],list(REQUIREMENTS))

    def test_unknown_or_unconfirmed_execution_never_admits_group_delete(self):
        case=object.__new__(OwnedLocalStructured)
        case.state={'unknown_operations':['lost-create'],'effects':{'group':{'id':'group'}},'group_id':'group'}
        with self.assertRaises(RunStateError):case._admit_delete()
        case.state.update(unknown_operations=[],operations=[{'operation':REQUIREMENTS[2],'status':'VERIFIED','observed':{'group_id':'foreign'}}])
        with self.assertRaises(RunStateError):case._admit_delete()

    def test_cli_guard_rejects_missing_write_permission(self):
        with contextlib.redirect_stdout(io.StringIO()) as out:
            rc=main(['local-structured-run','--binary','/unused','--binary-sha256','0'*64,
                '--state-dir','/unused-state','--run-id','denied','--confirm-run-id','denied',
                '--template-dir','/unused','--template-manifest','/unused.json','--template-manifest-sha256','0'*64])
        self.assertEqual(rc,2);self.assertEqual(json.loads(out.getvalue())['status'],'BLOCKED')

    def test_request_order_or_foreign_path_rejected_before_wire(self):
        case=object.__new__(OwnedLocalStructured);case._active=lambda:None;case._claims=set()
        row={'operation':'fixture-public','method':'DELETE','path':'/groups/foreign','actor':'PM','body_digest':'fake',
             'request_id':'r','status':'INTENDED'}
        case.state={'operations':[row]};case.pm='pm';case.source_binding={}
        with patch('bcs_e2e.local_structured.HTTPConnection') as wire:
            with self.assertRaises(RunStateError):case._wire('fixture-public',row)
            wire.assert_not_called()

    def test_lost_create_is_recorded_unknown_and_is_not_retried(self):
        case=object.__new__(OwnedLocalStructured);case._active=lambda:None;case._save=lambda:None
        case._spec=lambda _:('POST','/groups',{},200,'CEO')
        case.state={'operations':[],'assertions':[],'unknown_operations':[]}
        with patch.object(case,'_wire',side_effect=ConnectionError('lost')) as wire:
            with self.assertRaises(ConnectionError):case._call(REQUIREMENTS[2])
        self.assertEqual(wire.call_count,1)
        self.assertEqual(case.state['unknown_operations'],[REQUIREMENTS[2]])
        self.assertEqual(case.state['assertions'],[])
        self.assertEqual(case.state['operations'][0]['transitions'],['INTENDED','UNKNOWN'])


@unittest.skipUnless(BINARY and SHA and shutil.which('lsof'),'approved actual BCS required')
class StructuredLive(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        evidence=os.environ.get('BCS_E2E_STRUCTURED_EVIDENCE_DIR')
        if evidence:
            root=Path(evidence);root.mkdir(mode=0o700,parents=True,exist_ok=False)
        else:
            tmp=tempfile.TemporaryDirectory();cls.addClassCleanup(tmp.cleanup);root=Path(tmp.name)
        cls.root=root/'run';asset=approved_asset(root)
        cls.result=local_structured_run(**arguments(cls.root,asset))
        if cls.result['status']!='LOCAL_STRUCTURED_PASS':raise AssertionError(json.dumps(cls.result,indent=2))

    def test_full17_obligations_both_real_runs_and_exact_group_deletion(self):
        case=self.result['case']
        self.assertEqual(case['assertions'],[{'id':r,'status':'PASS'} for r in REQUIREMENTS])
        self.assertEqual([r['operation'] for r in case['operations']],list(ORDER))
        self.assertEqual(len(case['receiver']['deliveries']),2)
        self.assertEqual(case['receiver']['state'],'CLOSED')
        self.assertEqual(case['effects']['group']['status'],'DELETED')
        for name in ('implicit','explicit'):
            self.assertEqual(case['effects'][name]['status'],'CANCELLED')
            self.assertEqual(case['terminal_evidence'][name]['run']['status'],'aborted')
        self.assertEqual(self.result['shutdown']['status'],'STOPPED')
        self.assertEqual(self.result['credentials']['credential_memory'],'CLEARED')

    def test_frozen_offline_reports_preserve_original_files_and_denominator(self):
        before={p.relative_to(self.root).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
                for p in self.root.rglob('*') if p.is_file()}
        summary=build_structured_report(self.root)
        self.assertEqual(summary['status'],'LOCAL_SUITE_PASS')
        self.assertEqual(summary,self.result['canonical_summary'])
        self.assertEqual(summary['scope']['legacy_inventory_count'],41)
        self.assertEqual(summary['scope']['full_release_status'],'NOT_QUALIFIED')
        self.assertEqual(summary['reports']['observed_assertion_count'],17)
        self.assertEqual(summary['cases'][0]['cleanup'],'RETAINED')
        ET.parse(self.root/'reports/junit.xml')
        after={p.relative_to(self.root).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
               for p in self.root.rglob('*') if p.is_file()}
        self.assertEqual(before,after)

    def test_semantic_rewrites_cannot_fake_full_parent_success(self):
        binding=read(self.root/'structured-binding.json');original=self.result['case']
        for mutation in ('drop','reorder','implicit','terminal','template','foreign','ack','retention'):
            case=copy.deepcopy(original)
            if mutation=='drop':case['operations'].pop()
            elif mutation=='reorder':case['operations'].reverse()
            elif mutation=='implicit':case['implicit']=dict(case['explicit'])
            elif mutation=='terminal':case['terminal_evidence']['implicit']['run']['status']='running'
            elif mutation=='template':case['operations'][2]['observed']['yaml_sha256']='0'*64
            elif mutation=='foreign':case['group_id']='foreign'
            elif mutation=='ack':case['implicit_delivery']['success_ack_sent']=True
            else:case['retention']={}
            with self.subTest(mutation=mutation):self.assertFalse(_business_evidence(self.root,binding,case))

    def test_offline_cli_refuses_output_into_source_evidence(self):
        with contextlib.redirect_stdout(io.StringIO()) as out:
            rc=main(['local-structured-report','--state-dir',str(self.root),'--output-dir',str(self.root/'overwrite')])
        self.assertEqual(rc,2);self.assertFalse((self.root/'overwrite').exists())


class StructuredFailureFlowTests(unittest.TestCase):
    def test_lost_group_create_or_cancel_stops_without_retry_restore_or_delete(self):
        from unittest.mock import MagicMock
        for failed in (REQUIREMENTS[2],REQUIREMENTS[13]):
            with self.subTest(failed=failed),tempfile.TemporaryDirectory() as tmp:
                case=object.__new__(OwnedLocalStructured)
                case.root=Path(tmp);case.path=case.root/'case.json';case.run_id='failure'
                case.source='source';case.binding={};case._attempted=False
                case._active=lambda:None;case._save=lambda:None;case.identities=object();case.runtime=object()
                implicit={'run_id':'implicit','group_id':'group','session_id':'sid1','definition_id':'definition','definition_version':1}
                calls=[]
                def call(operation):
                    calls.append(operation)
                    if operation==failed:raise ConnectionError('unknown outcome')
                    if operation==REQUIREMENTS[2]:case.state.update(group_id='group',initial_session_id='sid1')
                    if operation==REQUIREMENTS[7]:case.state['explicit']={'run_id':'explicit','session_id':'sid2'}
                    return {'nodes':[{'attempt':1}]}
                case._call=call
                receiver=MagicMock();receiver.__enter__.return_value=receiver;receiver.summary.return_value={'state':'CLOSED'}
                with patch('bcs_e2e.local_structured.fixture_snapshot',return_value={}), \
                     patch('bcs_e2e.local_structured_receiver.OwnedLocalStructuredReceiver',return_value=receiver), \
                     patch('bcs_e2e.local_structured_storage.OwnedLocalStructuredStorage') as storage:
                    storage.return_value.group_runs.return_value=[implicit]
                    with self.assertRaises(ConnectionError):case.run()
                self.assertEqual(calls,list(ORDER[:ORDER.index(failed)+1]))
                self.assertNotIn('fixture-restore',calls);self.assertNotIn(REQUIREMENTS[-1],calls)
                self.assertEqual(case.state['status'],'BLOCKED')
                if failed==REQUIREMENTS[2]:
                    self.assertEqual(case.state['effects']['group']['status'],'UNKNOWN')
                    self.assertEqual(case.state['effects']['implicit']['status'],'UNKNOWN')
                else:self.assertEqual(case.state['effects']['explicit']['status'],'CANCEL_REQUESTED')
                receiver.__exit__.assert_called_once()

    def test_retention_evidence_requires_exact_removed_resources_and_retained_histories(self):
        from bcs_e2e.local_structured_reporting import _retention_valid
        case={'group_id':'g','definition_id':'d','base_version':1,'patched_version':2,
              'implicit':{'run_id':'i'},'explicit':{'run_id':'e'},'retention':{
              'source':'local-sqlite','group_id':'g','group_present':False,'session_count':0,'runtime_binding_count':0,
              'runs':[{'run_id':r,'status':'aborted','group_id':'g','definition_id':'d'} for r in ('i','e')],
              'nodes':[{'run_id':r,'node_id':'answer'} for r in ('i','e')],
              'definitions':[{'definition_id':'d','version':v} for v in (1,2)]}}
        self.assertTrue(_retention_valid(case))
        for key,value in [('group_present',True),('group_id','foreign'),('session_count',1),('runtime_binding_count',1),
                          ('runs',[]),('nodes',[]),('definitions',[]),('source','arbitrary')]:
            altered=copy.deepcopy(case);altered['retention'][key]=value
            with self.subTest(key=key):self.assertFalse(_retention_valid(altered))
