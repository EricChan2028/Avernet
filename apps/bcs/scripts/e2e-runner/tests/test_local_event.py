"""Whole original Event Story, failure ordering and offline preservation."""
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
from bcs_e2e.local_event import OwnedLocalEventStory,HTTP_IDS
from bcs_e2e.local_event_contract import REQUIREMENTS,event_source_binding
from bcs_e2e.local_event_run import local_event_run
from bcs_e2e.local_event_reporting import build_event_report,_business_evidence
from bcs_e2e.run_state import read,RunStateError
BINARY=os.environ.get('BCS_E2E_TEST_BINARY','');SHA=os.environ.get('BCS_E2E_TEST_BINARY_SHA256','')


def arguments(root,**changes):
    return dict(dict(binary=Path(BINARY),binary_sha256=SHA,state_dir=root,run_id='event-test',confirm_run_id='event-test',
        allow_local_processes=True,allow_fixture_write=True,allow_write=True),**changes)


class EventGuards(unittest.TestCase):
    def test_permission_denial_precedes_listener_process_and_fixture_creation(self):
        with tempfile.TemporaryDirectory() as tmp,patch('bcs_e2e.local_event_run.OwnedLocalEventSink') as sink:
            for key,value in [('allow_write',False),('allow_fixture_write',False),('allow_local_processes',False),('confirm_run_id','foreign')]:
                with self.subTest(key=key),self.assertRaises(RunStateError):local_event_run(**arguments(Path(tmp)/'run',**{key:value}))
            with self.assertRaises(RunStateError):local_event_run(**arguments(Path(tmp)))
            sink.assert_not_called()

    def test_unmanaged_identity_and_cli_permission_are_rejected(self):
        with self.assertRaises(RunStateError):OwnedLocalEventStory(object(),object())
        with contextlib.redirect_stdout(io.StringIO()) as out:
            rc=main(['local-event-run','--binary','/unused','--binary-sha256','0'*64,'--state-dir','/unused',
                     '--run-id','denied','--confirm-run-id','denied'])
        self.assertEqual(rc,2);self.assertEqual(json.loads(out.getvalue())['status'],'BLOCKED')

    def test_original_group_and_message_requests_keep_their_route_and_semantics(self):
        case=object.__new__(OwnedLocalEventStory);case.state={'group_id':'group'}
        case.pm='pm';case.eng='eng';case.url='http://127.0.0.1:45001/events/owned';case.source_binding=event_source_binding()
        method,path,body,status,actor=case._spec(REQUIREMENTS[2])
        self.assertEqual((method,path,status,actor),('POST','/openapi/v1/collaboration/groups',201,'HUMAN_PRINCIPAL'))
        self.assertNotIn('create_initial_session',body);self.assertEqual(body['event_subscriptions'][0]['sink']['url'],case.url)
        self.assertEqual(body['event_subscriptions'][0]['event_filters'],['group.*','session.*','message.*'])
        self.assertEqual(case._spec(REQUIREMENTS[12])[:2],('POST','/groups/group/messages'))

    def test_unknown_or_foreign_effects_block_delete_without_network(self):
        case=object.__new__(OwnedLocalEventStory);case.state={'unknown_operations':['lost']}
        with self.assertRaises(RunStateError):case._admit_delete(REQUIREMENTS[17])
        case.state={'unknown_operations':[],'group_id':'g','subscription_id':'s',
                    'operations':[{'operation':REQUIREMENTS[2],'status':'VERIFIED','observed':{'group_id':'other','subscription_id':'s'}}]}
        with self.assertRaises(RunStateError):case._admit_delete(REQUIREMENTS[17])

    def test_lost_create_stays_unknown_and_is_not_retried(self):
        case=object.__new__(OwnedLocalEventStory);case._active=lambda:None;case._save=lambda:None
        case._spec=lambda _:('POST','/groups',{},201,'HUMAN_PRINCIPAL')
        case.state={'operations':[],'unknown_operations':[]}
        with patch.object(case,'_wire',side_effect=ConnectionError()) as wire:
            with self.assertRaises(ConnectionError):case._call(REQUIREMENTS[2])
        self.assertEqual(wire.call_count,1);self.assertEqual(case.state['operations'][0]['status'],'UNKNOWN')
        self.assertEqual(case.state['unknown_operations'],[REQUIREMENTS[2]])


@unittest.skipUnless(BINARY and SHA and shutil.which('lsof'),'approved actual BCS required')
class EventLive(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        evidence=os.environ.get('BCS_E2E_EVENT_EVIDENCE_DIR')
        if evidence:
            root=Path(evidence);root.mkdir(mode=0o700,parents=True,exist_ok=False)
        else:
            tmp=tempfile.TemporaryDirectory();cls.addClassCleanup(tmp.cleanup);root=Path(tmp.name)
        cls.root=root/'run';cls.result=local_event_run(**arguments(cls.root))
        if cls.result['status']!='LOCAL_EVENT_PASS':raise AssertionError(json.dumps(cls.result,indent=2))

    def test_complete20_obligations_actual_callbacks_and_exact_retirement(self):
        case=self.result['case']
        self.assertEqual(case['assertions'],[{'id':key,'status':'PASS'} for key in REQUIREMENTS])
        self.assertEqual([row['operation'] for row in case['operations']],list(HTTP_IDS))
        self.assertEqual(case['receiver']['state'],'CLOSED');self.assertFalse(case['receiver']['failed'])
        self.assertTrue({'group.created','event_subscription.test','session.created','message.created'}<=
                        {row['event_type'] for row in case['receiver']['records']})
        self.assertEqual(case['effects']['group']['status'],'DELETED')
        self.assertEqual(case['effects']['subscription']['status'],'TOMBSTONED')
        self.assertEqual(self.result['shutdown']['status'],'STOPPED')
        self.assertEqual(self.result['credentials']['credential_memory'],'CLEARED')

    def test_offline_projection_matches_canonical_and_does_not_change_evidence(self):
        before={str(p.relative_to(self.root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in self.root.rglob('*') if p.is_file()}
        summary=build_event_report(self.root)
        self.assertEqual(summary,self.result['canonical_summary']);self.assertEqual(summary['status'],'LOCAL_SUITE_PASS')
        self.assertEqual(summary['scope']['legacy_inventory_count'],34);self.assertEqual(summary['reports']['observed_assertion_count'],20)
        self.assertEqual(summary['cases'][0]['cleanup'],'RETAINED');ET.parse(self.root/'reports/junit.xml')
        after={str(p.relative_to(self.root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in self.root.rglob('*') if p.is_file()}
        self.assertEqual(before,after)

    def test_offline_semantic_tampering_cannot_claim_success(self):
        original=self.result['case'];binding=read(self.root/'event-binding.json')
        for mutation in ('drop','order','foreign','callback','auth','sequence','retention','subscription'):
            case=copy.deepcopy(original)
            if mutation=='drop':case['operations'].pop()
            elif mutation=='order':case['operations'].reverse()
            elif mutation=='foreign':case['group_id']='foreign'
            elif mutation=='callback':case['receiver']['records']=[]
            elif mutation=='auth':case['receiver']['records'][0]['authorization']='present'
            elif mutation=='sequence':case['receiver']['records'][0]['sequence']=False
            elif mutation=='subscription':case['effects']['subscription']['status']='ADMITTED'
            else:case['retention']={}
            with self.subTest(mutation=mutation):self.assertFalse(_business_evidence(self.root,binding,case))

    def test_report_cli_cannot_overwrite_original_artifacts(self):
        with contextlib.redirect_stdout(io.StringIO()):
            rc=main(['local-event-report','--state-dir',str(self.root),'--output-dir',str(self.root/'overwrite')])
        self.assertEqual(rc,2);self.assertFalse((self.root/'overwrite').exists())


class EventHistoryTests(unittest.TestCase):
    def test_history_links_callbacks_delivery_attempts_and_preserved_message(self):
        from bcs_e2e.local_event_reporting import _history_valid
        event={'event_id':'e','group_id':'g','fanout_status':'completed'}
        delivery={'event_id':'e','delivery_id':'d','subscription_id':'s','subscription_revision':3,'status':'succeeded'}
        original={'group_id':'g','subscription_id':'s','delivery_id':'d',
            'retention':{'events':[event],'deliveries':[delivery]},
            'receiver':{'records':[{'event_id':'e','event_type':'group.created'},{'event_id':'t','event_type':'event_subscription.test'}]},
            'delivery_evidence':{'delivery':delivery,'event':event,'attempts':[{'delivery_id':'d','result':'success','http_status':204}]},
            'message_evidence':{'message_id':'m'},'message_retained':{'message_id':'m'}}
        self.assertTrue(_history_valid(original))
        for change in ('event','delivery','subscription','attempt','message','missing'):
            case=copy.deepcopy(original)
            if change=='event':case['receiver']['records'][0]['event_id']='foreign'
            elif change=='delivery':case['delivery_id']='foreign'
            elif change=='subscription':case['subscription_id']='foreign'
            elif change=='attempt':case['delivery_evidence']['attempts'][0]['http_status']=503
            elif change=='message':case['message_retained']={}
            else:case['retention']['events']=[]
            with self.subTest(change=change):self.assertFalse(_history_valid(case))
