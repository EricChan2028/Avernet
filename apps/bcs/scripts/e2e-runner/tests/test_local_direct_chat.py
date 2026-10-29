"""Finite Direct-chat executor guards; no live service is started here."""
from __future__ import annotations

import copy
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bcs_e2e.local_direct_chat import (
    CASE_ID,
    FULL_OPERATIONS,
    LEDGER_CONTRACT,
    LocalDirectChatError,
    OwnedLocalDirectChat,
    REQUIREMENTS,
    _CANCEL_FIELDS,
    _STATUS_FIELDS,
)


CEO = 'bot-CEO'
PM = 'bot-PM'


def identities():
    value = Mock()
    value._entries = {
        'CEO': (CEO, 'ceo-secret-token'),
        'PM': (PM, 'pm-secret-token'),
        'ENG': ('bot-ENG', 'eng-secret-token'),
        'QA': ('bot-QA', 'qa-secret-token'),
    }
    return value


def effect(name='main', state='ADMITTED'):
    return {
        'name': name,
        'case_id': CASE_ID,
        'source': 'ORIGINAL_DIRECT_CHAT_STORY',
        'owner_id': CEO,
        'target_id': PM,
        'request_id': 'request-' + name,
        'run_id': 'run-' + name,
        'session_id': 'session-' + name,
        'origin': 'created',
        'state': state,
        'transitions': ['INTENDED', 'ADMITTED'] + (
            ['CANCEL_REQUESTED'] if state == 'CANCEL_REQUESTED' else []),
    }


def status_body(item, *, terminal=False):
    return {
        'run_id': item['run_id'],
        'bot_uuid': item['target_id'],
        'from_bot_id': item['owner_id'],
        'session_id': item['session_id'],
        'state': 'cancelled' if terminal else 'running',
        'response': {'content': ''},
        'error_message': None,
        'created_at_ms': 100,
        'updated_at_ms': 120 if terminal else 110,
        'completed_at_ms': 120 if terminal else None,
        'expires_at_ms': 1000,
        'version': 3 if terminal else 2,
        'content_truncated': False,
        'is_terminal': terminal,
    }


def cancel_body(item):
    return {
        'run_id': item['run_id'],
        'cancelled': True,
        'state': 'cancelled',
        'response': {'content': ''},
        'error_message': None,
        'version': 3,
        'content_truncated': False,
    }


def bare_case(*, main=None, ping=None):
    case = object.__new__(OwnedLocalDirectChat)
    case.identities = identities()
    effects = {}
    if ping is not None:
        effects['ping'] = ping
    if main is not None:
        effects['main'] = main
    case.ledger = {
        'schema_version': 1,
        'contract': LEDGER_CONTRACT,
        'run_id': 'outer-run',
        'case_id': CASE_ID,
        'executor_digest': 'a' * 64,
        'binding_digest': 'b' * 64,
        'effects': effects,
        'status': 'ACTIVE',
    }
    case.state = {'operations': [], 'checks': [], 'assertions': []}
    case._active = lambda: None
    case._save = Mock()
    case._save_ledger = Mock()
    return case


class DirectChatSchemaTests(unittest.TestCase):
    def test_stable_full_operation_order_and_parent_credit(self):
        self.assertEqual(FULL_OPERATIONS, (
            'pm-visibility-before', 'pm-visibility-public',
            'pm-visibility-public-read', 'ping-submit', 'main-submit',
            'main-read', 'main-cancel', 'main-terminal', 'ping-cancel',
            'ping-terminal', 'pm-visibility-restore',
            'pm-visibility-restored-read'))
        self.assertEqual(REQUIREMENTS, (
            'DM.ping-submit', 'DM.main-submit', 'DM.main-read',
            'DM.main-cancel', 'DM.main-terminal'))
        self.assertEqual(len(FULL_OPERATIONS), 12)
        self.assertEqual(len(REQUIREMENTS), 5)

    def test_wire_response_field_contracts_are_finite(self):
        self.assertEqual(len(_STATUS_FIELDS), 14)
        self.assertEqual(_CANCEL_FIELDS, {
            'run_id', 'cancelled', 'state', 'response', 'error_message',
            'version', 'content_truncated'})

    def test_source_forbids_group_session_delete_retry_and_engine_claims(self):
        source = (ROOT / 'bcs_e2e/local_direct_chat.py').read_text()
        self.assertNotIn("method='DELETE'", source)
        self.assertNotIn("'DELETE'", source)
        self.assertNotIn('Cookie', source)
        self.assertNotIn('Botstop', source)
        self.assertIn("'engine': 'NOT_STARTED'", source)
        self.assertIn("'remote_engine_quiescence': 'NOT_CLAIMED'", source)
        self.assertIn('no retry or rediscovery is permitted', source)
        self.assertNotIn('_failure_restore', source)
        self.assertIn("'Authorization': 'Bearer ' + token", source)
        self.assertIn("'X-BCS-Bot-Token': token", source)
        self.assertNotIn('X-BCS-CHAT-VERSION', source)


class DirectChatStatusValidationTests(unittest.TestCase):
    def test_running_read_requires_exact_caller_target_session_and_all_fields(self):
        item = effect()
        case = bare_case(main=item)
        valid = status_body(item)
        self.assertEqual(case._status_projection(valid, item, terminal=False), {
            'run_id': item['run_id'], 'session_id': item['session_id'],
            'bot_uuid': PM, 'from_bot_id': CEO, 'state': 'running',
            'is_terminal': False, 'version': 2})
        mutations = {
            'missing-field': lambda value: value.pop('version'),
            'wrong-caller': lambda value: value.update(from_bot_id='borrowed'),
            'wrong-target': lambda value: value.update(bot_uuid='borrowed'),
            'wrong-session': lambda value: value.update(session_id='borrowed'),
            'premature-terminal': lambda value: value.update(is_terminal=True),
            'wrong-state': lambda value: value.update(state='submitted'),
            'fake-response': lambda value: value.update(response={'content': 'done'}),
            'meaningless-version': lambda value: value.update(version=0),
        }
        for name, mutate in mutations.items():
            value = copy.deepcopy(valid)
            mutate(value)
            with self.subTest(name=name), self.assertRaises(LocalDirectChatError):
                case._status_projection(value, item, terminal=False)

    def test_terminal_read_requires_cancelled_identity_bound_state(self):
        item = effect(state='CANCEL_REQUESTED')
        case = bare_case(main=item)
        valid = status_body(item, terminal=True)
        self.assertEqual(case._status_projection(valid, item, terminal=True)['state'], 'cancelled')
        for field, value in (
                ('is_terminal', False), ('state', 'running'),
                ('completed_at_ms', None), ('run_id', 'borrowed')):
            malformed = copy.deepcopy(valid)
            malformed[field] = value
            with self.subTest(field=field), self.assertRaises(LocalDirectChatError):
                case._status_projection(malformed, item, terminal=True)


class DirectChatLifecycleGuardTests(unittest.TestCase):
    def test_borrowed_or_ambiguous_run_is_refused_before_cancel_dispatch(self):
        for mutation in ('origin', 'case', 'owner', 'target', 'state', 'missing-id'):
            item = effect()
            if mutation == 'origin': item['origin'] = 'borrowed'
            elif mutation == 'case': item['case_id'] = 'foreign-case'
            elif mutation == 'owner': item['owner_id'] = 'foreign-owner'
            elif mutation == 'target': item['target_id'] = 'foreign-target'
            elif mutation == 'state': item['state'] = 'CANCEL_REQUESTED'
            else: item['run_id'] = None
            case = bare_case(main=item)
            case._begin = Mock(side_effect=AssertionError('must not dispatch'))
            with self.subTest(mutation=mutation), self.assertRaises(LocalDirectChatError):
                case._request_cancel('main-cancel', 'main')
            case._begin.assert_not_called()

    def test_cancel_acceptance_remains_pending_until_terminal_read(self):
        item = effect()
        case = bare_case(main=item)
        event = {'operation': 'main-cancel', 'path': '/chat/runs/run-main/cancel',
                 'status': 'INTENDED', 'transitions': ['INTENDED']}
        case._begin = Mock(return_value=event)
        case._wire = Mock(return_value=(200, cancel_body(item)))
        case._ack = Mock()
        observed = case._request_cancel('main-cancel', 'main')
        self.assertTrue(observed['cancelled'])
        self.assertEqual(item['state'], 'CANCEL_REQUESTED')
        self.assertEqual(item['transitions'], [
            'INTENDED', 'ADMITTED', 'CANCEL_REQUESTED'])
        case._wire.assert_called_once_with(event)
        case._save_ledger.assert_called_once()

    def test_nonterminal_read_never_promotes_cancel_pending_effect(self):
        item = effect(state='CANCEL_REQUESTED')
        case = bare_case(main=item)
        event = {'operation': 'main-terminal', 'status': 'INTENDED',
                 'transitions': ['INTENDED']}
        case._begin = Mock(return_value=event)
        case._wire = Mock(return_value=(200, status_body({**item, 'state': 'ADMITTED'})))
        case._unknown = Mock()
        with self.assertRaises(LocalDirectChatError):
            case._read_run('main-terminal', 'main', terminal=True)
        self.assertEqual(item['state'], 'CANCEL_REQUESTED')
        self.assertNotIn('CANCELLED', item['transitions'])
        case._save_ledger.assert_not_called()
        case._unknown.assert_called_once_with(event)

    def test_exact_terminal_read_is_only_transition_to_cancelled(self):
        item = effect(state='CANCEL_REQUESTED')
        case = bare_case(main=item)
        event = {'operation': 'main-terminal', 'status': 'INTENDED',
                 'transitions': ['INTENDED']}
        case._begin = Mock(return_value=event)
        case._wire = Mock(return_value=(200, status_body(item, terminal=True)))
        case._ack = Mock()
        observed = case._read_run('main-terminal', 'main', terminal=True)
        self.assertTrue(observed['is_terminal'])
        self.assertEqual(item['state'], 'CANCELLED')
        self.assertEqual(item['transitions'], [
            'INTENDED', 'ADMITTED', 'CANCEL_REQUESTED', 'CANCELLED'])
        case._save_ledger.assert_called_once()
        case._ack.assert_called_once()

    def test_lost_create_reply_is_one_attempt_no_receiver_no_rediscovery(self):
        case = bare_case()
        event = {'operation': 'ping-submit', 'status': 'INTENDED',
                 'transitions': ['INTENDED'], 'submitted_request_id': 'submit-ping'}
        case._begin = Mock(return_value=event)
        case._wire = Mock(side_effect=LocalDirectChatError('lost reply'))
        case._unknown = Mock()
        receiver = Mock()
        with self.assertRaises(LocalDirectChatError):
            case._submit('ping', receiver)
        case._wire.assert_called_once_with(event)
        receiver.receive_run.assert_not_called()
        case._unknown.assert_called_once_with(event)
        self.assertEqual(case.ledger['effects']['ping']['state'], 'UNKNOWN')
        self.assertIsNone(case.ledger['effects']['ping']['run_id'])
        self.assertEqual(case.ledger['effects']['ping']['transitions'], ['INTENDED', 'UNKNOWN'])


    def test_lost_cancel_reply_stays_cancel_requested_and_never_terminal(self):
        item = effect()
        case = bare_case(main=item)
        event = {'operation': 'main-cancel', 'path': '/chat/runs/run-main/cancel',
                 'status': 'INTENDED', 'transitions': ['INTENDED']}
        case._begin = Mock(return_value=event)
        case._wire = Mock(side_effect=LocalDirectChatError('lost cancel reply'))
        case._unknown = Mock()
        with self.assertRaises(LocalDirectChatError):
            case._request_cancel('main-cancel', 'main')
        self.assertEqual(item['state'], 'CANCEL_REQUESTED')
        self.assertEqual(item['cancel_outcome'], 'UNKNOWN')
        self.assertNotIn('CANCELLED', item['transitions'])
        case._wire.assert_called_once_with(event)
        case._unknown.assert_called_once_with(event)

    def test_read_requires_owned_effect_provenance_before_dispatch(self):
        for field, value in (('name', 'ping'), ('source', 'foreign'),
                             ('owner_id', 'foreign'), ('target_id', 'foreign'),
                             ('origin', 'borrowed'), ('request_id', '')):
            item = effect(state='CANCEL_REQUESTED')
            item[field] = value
            case = bare_case(main=item)
            case._begin = Mock(side_effect=AssertionError('must not dispatch'))
            with self.subTest(field=field), self.assertRaises(LocalDirectChatError):
                case._read_run('main-terminal', 'main', terminal=True)
            case._begin.assert_not_called()


    def test_lost_nominal_restore_is_not_retried(self):
        case = bare_case()
        case._public_confirmed = True
        case._restored_confirmed = False
        event = {'operation': 'pm-visibility-restore', 'status': 'INTENDED',
                 'transitions': ['INTENDED']}
        case._begin = Mock(return_value=event)
        case._wire = Mock(side_effect=LocalDirectChatError('lost restore reply'))
        case._unknown = Mock()
        with self.assertRaises(LocalDirectChatError):
            case._visibility('pm-visibility-restore', 'protected')
        case._wire.assert_called_once_with(event)
        case._unknown.assert_called_once_with(event)
        self.assertFalse(case._restored_confirmed)

    def test_failure_block_has_no_restore_or_other_http_mutation(self):
        source = (ROOT / 'bcs_e2e/local_direct_chat.py').read_text()
        failure = source[source.index('        except BaseException as error:'):]
        self.assertNotIn('_failure_restore', failure)
        self.assertNotIn('HTTPConnection(', failure)
        self.assertNotIn("_visibility(", failure)
        self.assertIn("self.state['visibility']['unrestored']", failure)
        self.assertIn('receiver.close()', failure)

    def test_wire_rejects_path_body_or_auth_drift_without_opening_socket(self):
        case = bare_case(main=effect())
        case.port = 23456
        case._claimed_request_ids = set()
        expected = ('POST', '/chat/runs/run-main/cancel', None, 'CEO', 200)
        case._spec = Mock(return_value=expected)
        base = {'operation': 'main-cancel', 'method': 'POST',
                'path': '/chat/runs/run-main/cancel', 'request_body': None,
                'auth_role': 'CEO', 'submitted_request_id': 'request-1',
                'status': 'INTENDED', 'transitions': ['INTENDED']}
        for field, wrong in (('method', 'GET'), ('path', '/chat/runs/borrowed/cancel'),
                             ('request_body', {}), ('auth_role', 'PM')):
            event = copy.deepcopy(base)
            event[field] = wrong
            case.state['operations'] = [event]
            with self.subTest(field=field), patch(
                    'bcs_e2e.local_direct_chat.HTTPConnection') as connection:
                with self.assertRaises(LocalDirectChatError):
                    case._wire(event)
                connection.assert_not_called()

    def test_duplicate_request_id_is_refused_without_second_dispatch(self):
        case = bare_case(main=effect())
        case.port = 23456
        case._claimed_request_ids = {'request-1'}
        case._spec = Mock(return_value=(
            'POST', '/chat/runs/run-main/cancel', None, 'CEO', 200))
        event = {'operation': 'main-cancel', 'method': 'POST',
                 'path': '/chat/runs/run-main/cancel', 'request_body': None,
                 'auth_role': 'CEO', 'submitted_request_id': 'request-1',
                 'status': 'INTENDED', 'transitions': ['INTENDED']}
        case.state['operations'] = [event]
        with patch('bcs_e2e.local_direct_chat.HTTPConnection') as connection:
            with self.assertRaises(LocalDirectChatError):
                case._wire(event)
            connection.assert_not_called()


class DirectChatEvidenceTests(unittest.TestCase):
    def test_ledger_intent_contains_only_owned_identity_and_no_credentials(self):
        case = bare_case()
        event = {'submitted_request_id': 'request-ping'}
        row = case._ledger_intent('ping', event)
        self.assertEqual(set(row), {
            'name', 'case_id', 'source', 'owner_id', 'target_id', 'request_id',
            'run_id', 'session_id', 'origin', 'state', 'transitions'})
        self.assertEqual(row['origin'], 'created')
        self.assertEqual(row['owner_id'], CEO)
        self.assertEqual(row['target_id'], PM)
        self.assertNotIn('token', repr(row).lower())
        case._save_ledger.assert_called_once()

    def test_requirement_assertions_cannot_credit_ping_cleanup(self):
        case = bare_case()
        case.state['assertions'] = [
            {'id': requirement, 'status': 'PASS'} for requirement in REQUIREMENTS]
        with self.assertRaises((IndexError, LocalDirectChatError)):
            case._append_assertion('DM.ping-cancel', {'terminal': True})
        self.assertEqual(len(case.state['assertions']), 5)

    def test_receiver_summary_must_be_closed_before_success_source_update(self):
        source = (ROOT / 'bcs_e2e/local_direct_chat.py').read_text()
        closed_check = "self.state['receiver'].get('state') == 'CLOSED'"
        success_update = "status='LOCAL_DIRECT_CHAT_PASS'"
        self.assertIn(closed_check, source)
        self.assertLess(source.index(closed_check), source.index(success_update))


if __name__ == '__main__':
    unittest.main()
