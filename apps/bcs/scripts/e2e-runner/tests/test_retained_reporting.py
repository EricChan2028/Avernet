"""Read-only report inventory is not completion, deletion or pool reuse authority."""
import copy
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from bcs_e2e.reporting import build_report, render_junit, render_markdown, write_reports
from bcs_e2e.retained import retained_run
from bcs_e2e.run_state import read, write
from bcs_e2e.profile import fingerprint
from bcs_e2e.ledger import Ledger
from bcs_e2e.retention_quota import CONTRACT, COSTS
from bcs_e2e.persistent_mock import PersistentLocalMockAdapter
import test_retained as fixtures


class RetainedReportingTests(unittest.TestCase):
    setUp=fixtures.RetainedTests.setUp
    prepare=fixtures.RetainedTests.prepare

    def execute(self,case='RETAIN-GROUP-01'):
        self.case=case
        return retained_run(self.profile,state_dir=self.state,run_id=self.run_id,case_id=case,
                            allow_write=True,confirm_run_id=self.run_id)

    def sources(self):
        return {p:p.read_bytes() for p in self.state.rglob('*') if p.is_file()} | {self.lock:self.lock.read_bytes()}

    def summary(self):return build_report(self.profile,self.state)

    def test_group_and_session_axes_match_all_formats_without_mutating_sources(self):
        for case in ('RETAIN-GROUP-01','RETAIN-SESSION-01'):
            # Each subcase needs its own pool, since retained runs keep theirs.
            with self.subTest(case=case), patch('bcs_e2e.pool.pool_root',return_value=self.root/case/'registry'):
                from bcs_e2e.pool import pool_path,pool_scope
                self.state=self.root/case/'run';self.lock=pool_path(pool_scope(self.profile))
                self.execute(case);before=self.sources()
                summary=self.summary();view=summary['retention']
                self.assertEqual(view['status'],'VERIFIED_LOCAL_INVENTORY')
                self.assertEqual(view['inventory_status'],'COMPLETE')
                self.assertEqual(view['resource_count'],sum(COSTS[case].values()))
                actual=next(c for c in summary['cases'] if c['id']==case)
                self.assertEqual(actual['functional'],'PASS')
                self.assertEqual(actual['execution_status'],'UNCONFIRMED')
                self.assertEqual(actual['resource_disposition'],'RETAINED_UNRESOLVED')
                self.assertEqual(view['handoff']['owner'],self.profile['human']['user_id'])
                self.assertEqual(view['handoff']['status'],'REQUIRED')
                self.assertFalse(view['pool_release_authorized']);self.assertFalse(view['automatic_delete_authorized'])
                self.assertEqual(view['quota']['status'],'NOT_RECORDED')
                output=self.root/case/'reports';write_reports(summary,output,{'all'})
                self.assertEqual(json.loads((output/'summary.json').read_text()),summary)
                node=ET.fromstring((output/'junit.xml').read_text()).find(f"testcase[@name='{case}']")
                self.assertIsNotNone(node.find('skipped'))
                self.assertEqual(node.find("properties/property[@name='execution_status']").get('value'),'UNCONFIRMED')
                md=(output/'summary.md').read_text()
                self.assertIn('Retained resources and manual handoff',md)
                self.assertIn('RETAINED_UNRESOLVED',md)
                for r in view['resources']:self.assertIn(r['resource_id'],md)
                self.assertEqual(self.sources(),before)

    def test_due_deadline_is_handoff_not_ttl_or_unlock(self):
        self.execute();original=read(self.state/'retention.json');deadline=original['review_after_epoch']
        before=self.sources()
        for now,expected in ((deadline-1,'PENDING'),(deadline,'DUE'),(deadline+86400,'DUE')):
            with patch('bcs_e2e.reporting.time.time',return_value=now):summary=self.summary()
            view=summary['retention'];self.assertEqual(view['handoff']['review_state'],expected)
            self.assertFalse(view['automatic_expiry']);self.assertFalse(view['pool_release_authorized'])
            self.assertNotEqual(summary['status'],'PILOT_PASS')
        self.assertEqual(self.sources(),before)

    def test_missing_foreign_tampered_inventory_never_exposes_foreign_ids(self):
        self.execute();path=self.state/'retention.json';original=read(path)
        variants=[{'run_id':'foreign'},{'profile_digest':'0'*64},{'owner':'foreign'},
                  {'case_id':'RETAIN-SESSION-01'},{'automatic_expiry':True},
                  {'review_after_epoch':True},{'review_after_epoch':None},
                  {'resources':[]},{'inventory_status':'UNKNOWN_RESOURCES'}]
        bad=copy.deepcopy(original['resources']);bad[0]['resource_id']='PRIVATE-FOREIGN-RESOURCE'
        variants.append({'resources':bad})
        for change in variants:
            with self.subTest(change=list(change)):
                write(path,{**original,**change});summary=self.summary();view=summary['retention']
                self.assertEqual(view['status'],'BLOCKED');self.assertEqual(view['resources'],[])
                self.assertIn('retention inventory, disposition, quota or source binding is invalid',summary['integrity']['issues'])
                self.assertNotIn('PRIVATE-FOREIGN-RESOURCE',json.dumps(summary))
        path.unlink();self.assertEqual(self.summary()['retention']['status'],'BLOCKED')

    def test_unknown_create_intents_stay_visible_and_are_not_claimed_complete(self):
        with patch.object(PersistentLocalMockAdapter,'group_create',side_effect=RuntimeError('lost')):
            self.execute()
        summary=self.summary();view=summary['retention']
        self.assertEqual(view['status'],'VERIFIED_LOCAL_INVENTORY')
        self.assertEqual(view['inventory_status'],'UNKNOWN_RESOURCES')
        self.assertTrue(view['resources'])
        self.assertTrue(all(r['resource_id'] is None for r in view['resources']))
        self.assertEqual(summary['cases'][0]['functional'],'FAIL')
        self.assertEqual(summary['cases'][0]['execution_status'],'UNCONFIRMED')

    def test_prepared_run_is_not_called_an_executed_case(self):
        self.prepare();summary=self.summary()
        self.assertEqual(summary['retention']['status'],'NOT_RUN')
        self.assertEqual(summary['retention']['handoff']['status'],'NOT_REQUIRED')
        self.assertTrue(all(c['execution_status']=='NOT_RUN' for c in summary['cases']))

    def test_missing_attempted_case_report_does_not_mean_not_run(self):
        self.execute();(self.state/f'report-{self.case}.json').unlink()
        summary=self.summary();case=summary['cases'][0]
        self.assertEqual(case['execution_status'],'UNCONFIRMED')
        self.assertEqual(case['resource_disposition'],'UNVERIFIED')
        self.assertEqual(summary['retention']['status'],'BLOCKED')

    def test_free_text_manual_action_is_not_rendered(self):
        self.execute();path=self.state/'retention.json';value=read(path)
        value['manual_action']='PRIVATE-TEXT; delete all tables'
        write(path,value);summary=self.summary()
        self.assertEqual(summary['retention']['status'],'VERIFIED_LOCAL_INVENTORY')
        self.assertNotIn('PRIVATE-TEXT',json.dumps(summary))
        self.assertNotIn('delete all tables',render_markdown(summary))

    def test_symlink_missing_ledger_and_source_drift_are_blocked(self):
        self.execute();path=self.state/'retention.json';value=read(path)
        private=self.root/'other.json';write(private,value);path.unlink();path.symlink_to(private)
        self.assertEqual(self.summary()['retention']['status'],'BLOCKED')
        path.unlink();write(path,value)
        original_read=read;reads=0
        def drifting(p):
            nonlocal reads
            result=original_read(p)
            if p==path:
                reads+=1
                if reads==2:result['review_after_epoch']+=1
            return result
        with patch('bcs_e2e.retained_reporting.read',side_effect=drifting):
            self.assertEqual(self.summary()['retention']['status'],'BLOCKED')
        state=read(self.state/'run.json');state.pop('ledger_path');write(self.state/'run.json',state)
        self.assertEqual(self.summary()['retention']['status'],'BLOCKED')

    def test_unsupported_completed_or_released_claims_cannot_become_junit_pass(self):
        self.execute();path=self.state/f'report-{self.case}.json';original=read(path)
        for change in ({'execution_status':'CONFIRMED_ENDED'},{'resource_disposition':'RETAINED_BY_POLICY'},
                       {'pool_disposition':'RELEASED'},{'quiescence':'CONFIRMED'}):
            write(path,{**original,**change});summary=self.summary()
            self.assertEqual(summary['retention']['status'],'BLOCKED')
            self.assertEqual(summary['cases'][0]['execution_status'],'UNCONFIRMED')
            self.assertIsNotNone(ET.fromstring(render_junit(summary)).find('testcase/skipped'))

    def quota(self):
        return {'contract':CONTRACT,'status':'RESERVED_MAXIMUM','automatic_refund':False,
            'reservation':{'run_id':self.run_id,'case_id':self.case,'profile_digest':fingerprint(self.profile),
                           'cost':COSTS[self.case]},'cost':COSTS[self.case],
            'accounted_after':{'groups':2,'sessions':3},'limits':{'groups':4,'sessions':8}}

    def install_quota(self,quota):
        for name in ('run.json','retention.json',f'report-{self.case}.json'):
            path=self.state/name;value=read(path);value['retention_quota']=quota;write(path,value)

    def test_quota_snapshot_is_not_current_capacity_or_refund_authority(self):
        self.execute();self.install_quota(self.quota());summary=self.summary();view=summary['retention']['quota']
        self.assertEqual(view['status'],'RESERVED_MAXIMUM')
        self.assertEqual(view['accounted_at_reservation'],{'groups':2,'sessions':3})
        self.assertEqual(view['current_remaining_capacity'],'NOT_VERIFIED')
        self.assertFalse(view['automatic_refund'])
        self.assertIn('groups at reservation: 2/4',render_markdown(summary))

    def test_quota_inconsistency_and_impossible_accounting_fail_closed(self):
        self.execute();self.install_quota(self.quota())
        value=read(self.state/'retention.json');value['retention_quota']['accounted_after']['groups']=3
        write(self.state/'retention.json',value)
        self.assertEqual(self.summary()['retention']['status'],'BLOCKED')
        for change in ({'automatic_refund':True},{'accounted_after':{'groups':True,'sessions':1}},
                       {'accounted_after':{'groups':9,'sessions':9}}, {'cost':{'groups':0,'sessions':0}}, {'cost':{'groups':True,'sessions':1}}):
            self.install_quota({**self.quota(),**change})
            self.assertEqual(self.summary()['retention']['status'],'BLOCKED')

    def test_extra_group_or_wrong_parent_in_both_files_is_invalid_inventory(self):
        self.execute()
        with Ledger(self.state/'run.sqlite',self.run_id,fingerprint(self.profile)) as ledger:
            ledger.db.execute("UPDATE resource SET parent=NULL WHERE kind='session'");ledger.db.commit()
            rows=ledger.resource_snapshot()
        value=read(self.state/'retention.json')
        from bcs_e2e.retained_reporting import FIELDS
        value['resources']=[{k:r[k] for k in FIELDS} for r in rows];write(self.state/'retention.json',value)
        self.assertEqual(self.summary()['retention']['status'],'BLOCKED')

    def test_success_cannot_claim_empty_inventory_even_when_ledger_matches(self):
        self.execute()
        with Ledger(self.state/'run.sqlite',self.run_id,fingerprint(self.profile)) as ledger:
            ledger.db.execute("DELETE FROM resource");ledger.db.commit()
        value=read(self.state/'retention.json');value['resources']=[]
        write(self.state/'retention.json',value)
        self.assertEqual(self.summary()['retention']['status'],'BLOCKED')

    def test_missing_binding_does_not_render_another_cases_resources(self):
        self.execute();value=read(self.state/'retention.json')
        value['resources'][0]['attempt']=True
        write(self.state/'retention.json',value)
        self.assertEqual(self.summary()['retention']['status'],'BLOCKED')

    def test_prepared_quota_crash_needs_handoff_and_never_refunds(self):
        self.prepare()
        path=self.state/'run.json';original=read(path)
        for quota in ({'status':'UNCONFIRMED','automatic_refund':False}, {'status':'RESERVED_MAXIMUM'}):
            write(path,{**original,'retention_quota':quota})
            summary=self.summary();retention=summary['retention']
            self.assertEqual(retention['status'],'BLOCKED')
            self.assertEqual(retention['handoff']['status'],'REQUIRED')
            self.assertTrue(retention['handoff']['actions'])
            self.assertFalse(retention['quota']['automatic_refund'])
            suite=ET.fromstring(render_junit(summary))
            self.assertEqual(suite.find("properties/property[@name='retention.status']").get('value'),'BLOCKED')

    def test_prepared_with_attempt_or_unreadable_ledger_cannot_suppress_handoff(self):
        self.prepare();path=self.state/'run.json';original=read(path)
        for extra in ({'attempted_cases':['RETAIN-GROUP-01']}, {'ledger_path':str(self.root/'missing.sqlite')}):
            write(path,{**original,**extra});summary=self.summary()
            self.assertEqual(summary['retention']['status'],'BLOCKED')
            self.assertEqual(summary['retention']['handoff']['status'],'REQUIRED')

    def test_wrong_profile_cannot_claim_handoff_ownership_even_when_prepared(self):
        self.prepare()
        for execute in (False,True):
            if execute:
                from bcs_e2e.retained import run_retained_case
                run_retained_case(self.profile,self.state,self.run_id,'RETAIN-GROUP-01')
            wrong=copy.deepcopy(self.profile);wrong['human']['user_id']='unrelated-human'
            summary=build_report(wrong,self.state)
            self.assertEqual(summary['retention']['handoff']['owner'],'UNKNOWN')
            self.assertEqual(summary['retention']['handoff']['owner_status'],'UNVERIFIED')
            self.assertEqual(summary['retention']['status'],'BLOCKED')
            suite=ET.fromstring(render_junit(summary))
            self.assertEqual(suite.find("properties/property[@name='retention.owner']").get('value'),'UNKNOWN')

    def test_junit_quota_and_exact_resources_match_json_reservation_view(self):
        self.execute();self.install_quota(self.quota());summary=self.summary()
        suite=ET.fromstring(render_junit(summary))
        for node in (suite,suite.find(f"testcase[@name='{self.case}']")):
            properties={p.get('name'):p.get('value') for p in node.findall('properties/property')}
            self.assertEqual(properties['retention.quota.status'],'RESERVED_MAXIMUM')
            self.assertEqual(properties['retention.quota.accounted_at_reservation.groups'],'2')
            self.assertEqual(properties['retention.quota.limits_at_reservation.groups'],'4')
            self.assertEqual(properties['retention.quota.current_remaining_capacity'],'NOT_VERIFIED')
            self.assertEqual(properties['retention.quota.automatic_refund'],'False')
            self.assertEqual(properties['retention.resource.0.resource_id'],summary['retention']['resources'][0]['resource_id'])


if __name__=='__main__':unittest.main()
