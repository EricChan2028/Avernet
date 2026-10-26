"""Retained contexts are neither active Send nor whole-scope completion proof."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from bcs_e2e.delivery_observation import snapshot_activity
from bcs_e2e.delivery_observation import summarize, _known_deliveries_settled
from test_delivery_observation import row
import test_delivery_observation as fixtures
import test_delivery_wait as wait


class DeliveryActivityTests(unittest.TestCase):
    setUp=fixtures.DeliveryProjectionTests.setUp

    def inject(self, **extra):
        return row(self.plan,kind='inject',status='pending_context',run_id=None,
                   may_have_been_sent=False,terminal_at_ms=None,**extra)

    def test_completed_sends_and_pending_context_are_not_active_engine_work(self):
        rows=[row(self.plan),self.inject(delivery_id='context')]
        result=snapshot_activity(rows)
        self.assertEqual(result['status'],'NO_ACTIVE_SEND_IN_SNAPSHOT')
        self.assertEqual(result['terminal_sends'],1)
        self.assertEqual(result['pending_contexts'],1)
        self.assertEqual(result['scope_status'],'UNCONFIRMED')
        self.assertFalse(result['pool_reuse_authorized'])
        summary=summarize(rows)
        self.assertEqual(summary['snapshot_activity'],result)
        self.assertFalse(_known_deliveries_settled([{'row_count':2,**summary}]))

    def test_pending_context_alone_never_implies_a_send_existed(self):
        result=snapshot_activity([self.inject()])
        self.assertEqual(result['status'],'NO_ACTIVE_SEND_IN_SNAPSHOT')
        self.assertEqual(result['terminal_sends'],0)
        self.assertEqual(result['pending_contexts'],1)
        self.assertFalse(result['pool_reuse_authorized'])

    def test_queued_dispatching_running_cancelling_remain_active(self):
        for status in ('queued','dispatching','running','cancelling'):
            result=snapshot_activity([row(self.plan,status=status),self.inject(delivery_id='ctx')])
            self.assertEqual(result['status'],'ACTIVE_SEND_OBSERVED')
            self.assertEqual(result['active_sends'],1)
            self.assertEqual(result['pending_contexts'],1)

    def test_expired_or_unknown_send_is_not_reported_quiet(self):
        for status in ('unknown','cancel_unknown','expired'):
            result=snapshot_activity([row(self.plan,status=status)])
            self.assertEqual(result['status'],'UNRESOLVED_ACTIVITY')
            self.assertEqual(result['uncertain_sends'],1)

    def test_bound_context_requires_reconciliation_not_pending_classification(self):
        value=self.inject();value.update(status='bound',bound_to_delivery_id='carrier')
        result=snapshot_activity([value])
        self.assertEqual(result['status'],'UNRESOLVED_ACTIVITY')
        self.assertEqual(result['bound_contexts'],1)
        self.assertEqual(result['pending_contexts'],0)

    def test_inconsistent_context_shapes_are_unclassified(self):
        for patch_value in ({'may_have_been_sent':True},{'bound_to_delivery_id':'carrier'},
                            {'status':'bound'},{'status':'running'}):
            value={**self.inject(),**patch_value}
            result=snapshot_activity([value])
            self.assertEqual(result['status'],'UNRESOLVED_ACTIVITY')
            self.assertEqual(result['unclassified_rows'],1)

    def test_terminal_context_and_failed_send_do_not_mean_functional_success(self):
        for status in ('consumed','discarded_context','cancelled','expired','failed'):
            value=self.inject();value['status']=status
            result=snapshot_activity([value,row(self.plan,status='failed',delivery_id='send')])
            self.assertEqual(result['terminal_contexts'],1)
            self.assertEqual(result['terminal_sends'],1)
            self.assertEqual(result['status'],'NO_ACTIVE_SEND_IN_SNAPSHOT')
            self.assertEqual(result['scope_status'],'UNCONFIRMED')

    def test_empty_snapshot_means_no_evidence(self):
        result=snapshot_activity([])
        self.assertEqual(result['status'],'NO_EVIDENCE')
        self.assertFalse(result['pool_reuse_authorized'])


class ActivityObserverTests(unittest.TestCase):
    setUp=wait.DeliveryWaitTests.setUp
    scripted=wait.DeliveryWaitTests.scripted
    observe=wait.DeliveryWaitTests.observe

    def test_pending_context_diagnostic_does_not_relax_wait_or_release(self):
        def values(plan,n):
            return {'success':True,'data':[row(plan),row(plan,delivery_id='ctx',kind='inject',
                status='pending_context',run_id=None,may_have_been_sent=False,terminal_at_ms=None)]}
        self.scripted([values]*3)
        with patch('bcs_e2e.delivery_observation.time.sleep'):result=self.observe()
        self.assertEqual(result['queries'],6)
        self.assertEqual(result['stop_reason'],'OBSERVATION_LIMIT')
        self.assertEqual(result['scope_status'],'UNCONFIRMED')
        for session in result['sessions']:
            self.assertEqual(session['snapshot_activity']['status'],'NO_ACTIVE_SEND_IN_SNAPSHOT')
            self.assertEqual(session['snapshot_activity']['pending_contexts'],1)
        self.assertEqual(len(result['rounds']),3)
