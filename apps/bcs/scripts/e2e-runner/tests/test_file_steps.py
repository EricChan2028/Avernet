"""Shared file business steps never choose deletion or hide partial ownership."""
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from bcs_e2e.case_scope import CaseScopedAdapter
from bcs_e2e.correlation import CorrelatedEvidence
from bcs_e2e.file_steps import FileProgress, file_control_steps
from bcs_e2e.control_steps import PilotError
from bcs_e2e.inventory import current_manifest
from bcs_e2e.ledger import Ledger
from bcs_e2e.observation_gate import AdmittedObserver
from bcs_e2e.profile import fingerprint
from bcs_e2e.write_transport import WriteTransportError
import test_file_story_assertions as fixtures


class FileStepsTests(unittest.TestCase):
    setUp=fixtures.FileStoryAssertionTests.setUp
    prepare=fixtures.FileStoryAssertionTests.prepare

    def test_file_progress_repr_omits_minted_share_token(self):
        progress=FileProgress(share_token='test-only')
        self.assertNotIn('test-only',repr(progress))
        self.assertNotIn('share_token',repr(progress))

    def execute_steps(self, raw, *, expected_backend='local'):
        self.progress=FileProgress();self.assertions=[];self.semantic=[]
        def assertion(name,condition,detail=''):
            self.assertions.append({'id':'PRE-03.'+name,'status':'PASS' if condition else 'FAIL'})
            if not condition:raise PilotError(detail or name)
        case=next(c for c in current_manifest()['cases'] if c['id']=='PRE-03')
        with Ledger(self.state/'run.sqlite',self.run_id,fingerprint(self.profile)) as ledger:
            adapter=CaseScopedAdapter(raw,ledger,fingerprint(self.profile),case,attempt=1)
            observer=AdmittedObserver(adapter,self.profile,'PRE-03',resource_scope=adapter.observation_scope)
            evidence=CorrelatedEvidence(ledger,'PRE-03',1)
            file_control_steps(self.profile,self.run_id,'PRE-03',ledger=ledger,adapter=adapter,
                               observer=observer,control=self.progress,evidence=evidence,
                               assertions=self.assertions,semantic_evidence=self.semantic,
                               assertion=assertion,expected_backend=expected_backend)
            self.evidence=list(evidence)
            return ledger.resource_snapshot()

    def test_all_business_assertions_pass_without_deletion_and_resources_stay_owned(self):
        raw=self.prepare()
        with patch.object(raw,'file_delete',side_effect=AssertionError('no file delete')), \
             patch.object(raw,'session_delete',side_effect=AssertionError('no session delete')), \
             patch.object(raw,'group_delete',side_effect=AssertionError('no group delete')):
            rows=self.execute_steps(raw)
        self.assertEqual(len(rows),4)
        self.assertTrue(all(r['origin']=='created' and r['state']=='live' for r in rows))
        self.assertEqual(self.progress.evidence_status,'LOCAL_SURROGATE')
        self.assertEqual(len(self.semantic),1)
        self.assertTrue(all(a['status']=='PASS' for a in self.assertions))
        ids={a['id'] for a in self.assertions}
        manifest=next(c for c in current_manifest()['cases'] if c['id']=='PRE-03')
        required={a for a in manifest['assertions'] if a not in
                  {'PRE-03.owned_initial_session_deleted','PRE-03.owned_resources_cleaned'}}
        self.assertTrue(required<=ids)
        self.assertFalse(any('deleted' in i or 'cleaned' in i for i in ids))
        self.assertTrue(self.progress.share_token)
        self.assertNotIn(self.progress.share_token,json.dumps(self.evidence))

    def test_lost_file_prepare_keeps_intent_without_adopting_or_retrying(self):
        raw=self.prepare()
        with patch.object(raw,'file_prepare',side_effect=WriteTransportError('lost')) as prepare:
            with self.assertRaises(WriteTransportError):self.execute_steps(raw)
        self.assertEqual(prepare.call_count,1)
        self.assertTrue(self.progress.file_handle)
        self.assertEqual(self.progress.file_id,'')
        with Ledger(self.state/'run.sqlite',self.run_id,fingerprint(self.profile)) as ledger:
            rows=ledger.resource_snapshot()
        file=next(r for r in rows if r['kind']=='file')
        self.assertIsNone(file['resource_id']);self.assertEqual(file['origin'],'unknown')
        self.assertEqual(file['parent'],self.progress.session_handle)
        self.assertTrue(all(r['state']=='live' for r in rows if r['kind']!='file'))

    def test_byte_assertion_failure_preserves_scope_without_cleanup(self):
        raw=self.prepare();original=raw.file_download
        def wrong(*args):
            _,record=original(*args)
            return b'wrong bytes',record
        with patch.object(raw,'file_download',wrong), patch.object(raw,'file_delete') as delete:
            with self.assertRaises(PilotError):self.execute_steps(raw)
            delete.assert_not_called()
        self.assertTrue(self.progress.file_id)
        self.assertEqual(self.assertions[-1],{'id':'PRE-03.download_bytes_match','status':'FAIL'})

    def test_invalid_backend_is_rejected_before_preflight_or_creation(self):
        raw=self.prepare()
        with patch.object(raw,'preflight') as preflight,patch.object(raw,'group_create') as create:
            with self.assertRaises(PilotError):self.execute_steps(raw,expected_backend='unknown')
            preflight.assert_not_called();create.assert_not_called()
