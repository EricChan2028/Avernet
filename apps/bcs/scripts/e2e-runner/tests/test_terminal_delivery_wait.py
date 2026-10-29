"""Terminal passive context stops polling, not whole-scope qualification."""
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bcs_e2e.delivery_observation import DeliveryObserver, WAIT_CONTRACT, BUDGETED_WAIT_CONTRACT
from bcs_e2e.run_state import read
import test_delivery_observation as fixtures
import test_delivery_wait as wait
import test_mapping_recheck as mapping

TERMINAL_CONTEXTS = ('consumed', 'discarded_context', 'cancelled', 'expired', 'failed')


def context(plan, **extra):
    return fixtures.row(plan, **{'delivery_id': 'context', 'kind': 'inject',
        'status': 'expired', 'run_id': None, 'may_have_been_sent': False, **extra})


def retained_shape(plan):
    """Synthetic identities, actual observed shape: three Sends and two Injects."""
    return [fixtures.row(plan, delivery_id=f'send-{n}', run_id=f'run-{n}') for n in range(3)] + [
        context(plan, delivery_id='context-consumed', status='consumed', bound_to_delivery_id='send-0'),
        context(plan, delivery_id='context-expired')]


class TerminalDeliveryWaitTests(unittest.TestCase):
    setUp = fixtures.DeliveryObserverTests.setUp
    scripted = wait.DeliveryWaitTests.scripted

    def observe(self, contract=WAIT_CONTRACT):
        budget = ({'timeout_seconds': 120, 'max_rounds': 6, 'poll_interval_seconds': 1}
                  if contract == BUDGETED_WAIT_CONTRACT else None)
        observer = DeliveryObserver(client=self.client, database='testdb', environment='pre',
            allowed_bots=frozenset({'bot-1', 'bot-2'}), contract=contract, observation_budget=budget)
        observer.admit()
        return observer.observe(self.ledger, self.scoped.observation_scope, self.active,
                                lease=self.lease, case_id=self.case, timeout_seconds=10)

    def snapshots(self, make_rows):
        return self.scripted([lambda plan, n: {'success': True, 'data': make_rows(plan, n)}]*6)

    def test_observed_shape_stops_first_round_for_both_wait_contracts(self):
        for contract in (WAIT_CONTRACT, BUDGETED_WAIT_CONTRACT):
            with self.subTest(contract=contract):
                calls = self.snapshots(lambda plan, n: retained_shape(plan))
                with patch('bcs_e2e.delivery_observation.time.sleep') as sleep:
                    result = self.observe(contract)
                self.assertEqual(result['status'], 'OBSERVED')
                self.assertEqual(result['stop_reason'], 'KNOWN_DELIVERIES_SETTLED')
                self.assertEqual(len(calls), 2)  # one query per owned Session
                self.assertEqual(len(result['rounds']), 1)
                sleep.assert_not_called()
                self.assertEqual(result['scope_status'], 'UNCONFIRMED')
                for session in result['sessions']:
                    self.assertEqual(session['row_count'], 5)
                    self.assertEqual(len(session['known_completed_send_ids']), 3)
                    self.assertEqual(session['snapshot_activity']['terminal_contexts'], 2)
                    self.assertFalse(session['snapshot_activity']['pool_reuse_authorized'])

    def test_each_terminal_passive_context_is_settled(self):
        for status in TERMINAL_CONTEXTS:
            for sent in (False, 0):
                with self.subTest(status=status, sent=sent):
                    calls = self.snapshots(lambda plan, n: [fixtures.row(plan),
                        context(plan, status=status, may_have_been_sent=sent)])
                    with patch('bcs_e2e.delivery_observation.time.sleep') as sleep:
                        result = self.observe()
                    self.assertEqual(result['stop_reason'], 'KNOWN_DELIVERIES_SETTLED')
                    self.assertEqual(len(calls), 2)
                    sleep.assert_not_called()

    def test_terminal_looking_inject_with_possible_send_is_never_settled(self):
        for status in TERMINAL_CONTEXTS:
            for sent in (True, 1):
                with self.subTest(status=status, sent=sent):
                    calls = self.snapshots(lambda plan, n: [fixtures.row(plan),
                        context(plan, status=status, may_have_been_sent=sent)])
                    with patch('bcs_e2e.delivery_observation.time.sleep'):
                        result = self.observe()
                    self.assertEqual(result['stop_reason'], 'OBSERVATION_LIMIT')
                    self.assertEqual(len(calls), 6)
                    self.assertEqual(result['sessions'][0]['snapshot_activity']['unclassified_rows'], 1)

    def test_expired_send_remains_uncertain_even_with_terminal_context(self):
        for sent in (False, True):
            with self.subTest(sent=sent):
                self.snapshots(lambda plan, n: [fixtures.row(plan), context(plan),
                    fixtures.row(plan, delivery_id='uncertain', status='expired', may_have_been_sent=sent)])
                with patch('bcs_e2e.delivery_observation.time.sleep'):
                    result = self.observe()
                self.assertEqual(result['stop_reason'], 'OBSERVATION_LIMIT')
                self.assertEqual(result['queries'], 6)
                self.assertEqual(result['sessions'][0]['unresolved_send_ids'], ['uncertain'])
                self.assertEqual(result['sessions'][0]['snapshot_activity']['uncertain_sends'], 1)

    def test_pending_bound_empty_context_only_and_unsuccessful_send_do_not_settle(self):
        variants = [[], lambda p: [context(p)]]
        variants += [lambda p, status=status: [fixtures.row(p), context(p, status=status,
                        bound_to_delivery_id='delivery-1' if status == 'bound' else None)]
                     for status in ('pending_context', 'bound')]
        variants += [lambda p, status=status: [fixtures.row(p), context(p),
                        fixtures.row(p, delivery_id='unsuccessful', status=status,
                                     may_have_been_sent=status != 'rejected_capacity')]
                     for status in ('failed', 'cancelled', 'rejected_capacity')]
        variants += [lambda p, extra=extra: [fixtures.row(p, **extra), context(p)]
                     for extra in ({'run_id': None}, {'terminal_at_ms': None})]
        for index, value in enumerate(variants):
            with self.subTest(index=index):
                self.snapshots(lambda plan, n: value(plan) if callable(value) else value)
                with patch('bcs_e2e.delivery_observation.time.sleep'):
                    result = self.observe()
                self.assertEqual(result['stop_reason'], 'OBSERVATION_LIMIT')
                self.assertEqual(result['queries'], 6)
                self.assertEqual(result['scope_status'], 'UNCONFIRMED')

    def test_all_terminal_status_reversals_rejected_while_other_session_keeps_polling(self):
        transitions = [('inject', status, 'pending_context') for status in TERMINAL_CONTEXTS]
        transitions += [('inject', 'expired', 'consumed')]
        transitions += [('send', status, 'running') for status in
                        ('completed', 'failed', 'cancelled', 'expired', 'rejected_capacity')]
        for kind, before, after in transitions:
            with self.subTest(kind=kind, before=before, after=after):
                def values(plan, n):
                    if plan.session_id == 'session-2':
                        return [fixtures.row(plan, status='running', terminal_at_ms=None)]
                    changing = fixtures.row(plan, delivery_id='terminal', kind=kind,
                        status=before if n == 0 else after, state_version=4+n,
                        may_have_been_sent=kind == 'send' and (n > 0 or before != 'rejected_capacity'))
                    return [fixtures.row(plan), changing]
                self.snapshots(values)
                with patch('bcs_e2e.delivery_observation.time.sleep'):
                    result = self.observe()
                self.assertEqual(result['stop_reason'], 'INCONSISTENT_HISTORY')
                self.assertEqual(result['status'], 'INCOMPLETE')
                self.assertEqual(result['queries'], 4)
                self.assertEqual(result['rounds'][-1]['inconsistent_delivery_ids'], ['terminal'])

    def test_history_reversal_wins_over_same_round_settlement(self):
        for contract in (WAIT_CONTRACT, BUDGETED_WAIT_CONTRACT):
            with self.subTest(contract=contract):
                def values(plan, n):
                    if plan.session_id == 'session-2':
                        return [fixtures.row(plan, state_version=4+n,
                            status='running' if n == 0 else 'completed',
                            terminal_at_ms=None if n == 0 else 100)]
                    return [fixtures.row(plan), context(plan, state_version=4+n,
                        status='expired' if n == 0 else 'consumed')]
                self.snapshots(values)
                with patch('bcs_e2e.delivery_observation.time.sleep'):
                    result = self.observe(contract)
                self.assertEqual(result['stop_reason'], 'INCONSISTENT_HISTORY')
                self.assertEqual(result['status'], 'INCOMPLETE')
                self.assertEqual(result['queries'], 4)
                self.assertEqual(result['rounds'][-1]['inconsistent_delivery_ids'], ['context'])

    def test_context_release_and_definitely_not_sent_retry_are_not_terminal_reversals(self):
        def values(plan, n):
            return [fixtures.row(plan),
                context(plan, status='bound' if n == 0 else 'pending_context', state_version=4+n,
                        bound_to_delivery_id='retry' if n == 0 else None),
                fixtures.row(plan, delivery_id='retry', status='dispatching' if n == 0 else 'queued',
                             state_version=4+n, may_have_been_sent=n == 0, terminal_at_ms=None)]
        self.snapshots(values)
        with patch('bcs_e2e.delivery_observation.time.sleep'):
            result = self.observe()
        self.assertEqual(result['stop_reason'], 'OBSERVATION_LIMIT')
        self.assertEqual(result['status'], 'OBSERVED')
        self.assertTrue(all(not r['inconsistent_delivery_ids'] for r in result['rounds']))


class TerminalRecheckTests(unittest.TestCase):
    setUp = mapping.MappedProducerTests.setUp
    fixtures = mapping.MappedProducerTests.fixtures
    command = mapping.MappedProducerTests.command
    boundaries = mapping.MappedProducerTests.boundaries
    execute = mapping.MappedProducerTests.execute
    args = mapping.MappedProducerTests.args
    assert_original_unchanged = mapping.MappedProducerTests.assert_original_unchanged

    def process(self, command, **kwargs):
        response = mapping.MappedProducerTests.process(self, command, **kwargs)
        args = json.loads(command[4]) if '--args' in command else {}
        if args.get('table') == 'bcs_message_deliveries' and command[2].endswith('.db_query'):
            payload = json.loads(response.stdout)
            payload['data'] = [{**value, 'target_bot_id': 'test-driver'}
                               for value in retained_shape(self.delivery_queries[-1])]
            response.stdout = json.dumps(payload)
        return response

    def test_source_bound_recheck_stops_after_eight_calls_without_releasing_or_rewriting(self):
        guard = self.lock.with_suffix('.guard')
        guard_bytes = guard.read_bytes()
        with self.boundaries(), patch('bcs_e2e.delivery_observation.time.sleep') as sleep:
            rc, report = self.command(self.args())
        self.assertEqual(rc, 2, report)
        self.assertEqual(report['status'], 'BLOCKED')
        self.assertEqual(report['persistence'], 'SAVED')
        observation = report['execution_observation']
        self.assertEqual(observation['status'], 'OBSERVED')
        self.assertEqual(observation['stop_reason'], 'KNOWN_DELIVERIES_SETTLED')
        self.assertEqual(len(observation['rounds']), 1)
        self.assertEqual(len(self.calls), 8)  # 4 schema + 3 scope facts + 1 delivery
        sleep.assert_not_called()
        self.assertEqual(report['execution_status'], 'UNCONFIRMED')
        self.assertEqual(report['pool_disposition'], 'QUARANTINED')
        self.assertFalse(report['pool_release_authorized'])
        self.assertEqual(report['business_writes'], 0)
        self.assertEqual(read(self.state/'run.json')['status'], 'QUARANTINED')
        self.assertEqual(guard.read_bytes(), guard_bytes)
        self.assert_original_unchanged()
