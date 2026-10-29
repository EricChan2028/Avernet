"""Bounded message regression on the real owned server, plus failure evidence."""
import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bcs_e2e.cli import main
from bcs_e2e.local_workspace_messages import OwnedLocalWorkspaceMessages, OPERATIONS
from bcs_e2e.run_state import RunStateError, read

BINARY = os.environ.get('BCS_E2E_TEST_BINARY', '')
DIGEST = os.environ.get('BCS_E2E_TEST_BINARY_SHA256', '')
CLI = os.environ.get('BCS_E2E_TEST_CLI_BINARY', '')
CLI_DIGEST = os.environ.get('BCS_E2E_TEST_CLI_BINARY_SHA256', '')


class WorkspaceMessagesGuardTests(unittest.TestCase):
    def test_only_original_three_ingress_operations(self):
        self.assertEqual(OPERATIONS, ('persistent-message', 'live-chat', 'agent-callback'))

    def test_delivery_target_type_and_success_must_all_match(self):
        case = object.__new__(OwnedLocalWorkspaceMessages)
        case.group_id = 'owned-group'
        case.scope = Mock()
        case.scope._id.side_effect = lambda role: 'bot-'+role
        good = {'group_id': 'owned-group', 'delivered': True, 'delivered_count': 1,
                'failed_count': 0, 'delivery_results': [
                    {'bot_uuid': 'bot-PM', 'delivery_type': 'send', 'success': True}]}
        case._deliveries(good, (('PM', 'chat.send'),))
        for key, value in [('group_id', 'foreign'), ('failed_count', 1), ('delivered', False),
                           ('delivery_results', [{'bot_uuid': 'bot-PM', 'delivery_type': 'send', 'success': False}]),
                           ('delivery_results', [{'bot_uuid': 'bot-PM', 'delivery_type': 'inject', 'success': True}])]:
            with self.subTest(key=key, value=value), self.assertRaises(RunStateError):
                case._deliveries(dict(good, **{key: value}), (('PM', 'chat.send'),))

    def test_missing_wire_receipt_does_not_pass(self):
        case = object.__new__(OwnedLocalWorkspaceMessages)
        case._active = Mock()
        case.scope = Mock()
        case.scope._id.return_value = 'bot-PM'
        case.group_id, case.session_id = 'group-owned', 'group-owned:session'
        case.state = {'wire_receipts': []}
        receiver = Mock()
        receiver.receive_delivery.return_value = {}
        with self.assertRaisesRegex(RunStateError, 'receipt is incomplete'):
            case._receive({'PM': receiver}, 'live-chat', 'expected', (('PM','chat.send'),))
        self.assertEqual(case.state['wire_receipts'], [])

    def test_extra_storage_row_and_changed_baseline_are_rejected(self):
        baseline = {'message_id': 'system-one', 'content': 'original'}
        case = object.__new__(OwnedLocalWorkspaceMessages)
        case._baseline = [baseline]
        for rows in ([dict(baseline, content='changed')],
                     [baseline]+[{'message_id': 'extra-'+str(n)} for n in range(4)]):
            case._read_storage = Mock(return_value=rows)
            with self.subTest(rows=rows), self.assertRaises(RunStateError):
                case._storage()

    def test_duplicate_operation_is_rejected_before_auth_or_http(self):
        case = object.__new__(OwnedLocalWorkspaceMessages)
        case._active = Mock()
        case.state = {'operations': [{'operation': OPERATIONS[0]}]}
        case.scope = Mock()
        with self.assertRaisesRegex(RunStateError, 'ordered plan'):
            case._post(OPERATIONS[0])
        case.scope._actor.assert_not_called()


@unittest.skipUnless(BINARY and DIGEST and CLI and CLI_DIGEST and shutil.which('lsof'),
                     'actual approved BCS and CLI required')
class WorkspaceMessagesLiveTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)/'runtime'

    def execute(self):
        args = ['local-workspace-run', '--with-messages', '--state-dir', str(self.root),
                '--run-id', 'workspace-messages', '--confirm-run-id', 'workspace-messages',
                '--binary', BINARY, '--binary-sha256', DIGEST, '--cli-binary', CLI,
                '--cli-binary-sha256', CLI_DIGEST, '--allow-local-processes', '--allow-fixture-write', '--allow-write']
        with contextlib.redirect_stdout(io.StringIO()) as output:
            rc = main(args)
        return rc, json.loads(output.getvalue())

    def test_actual_three_ingress_calls_and_exact_bot_wire_observations(self):
        rc, result = self.execute()
        self.assertEqual(rc, 0, result.get('message_case'))
        self.assertEqual(result['status'], 'LOCAL_WORKSPACE_MESSAGING_PASS')
        self.assertEqual(result['selected_requirement_count'], 16)
        self.assertEqual(result['parent_requirement_count'], 17)
        self.assertEqual([r['status'] for r in result['requirements']], ['PASS']*16+['NOT_RUN'])
        self.assertEqual(result['message_case']['receiver_state'], 'CLOSED')
        self.assertEqual(len(result['message_case']['wire_receipts']), 8)
        self.assertTrue(all(r['status'] == 'TRANSPORT_RECEIVED_ONLY' and not r['success_ack_sent']
                            for r in result['message_case']['wire_receipts']))
        self.assertEqual(result['message_case']['storage_verification']['status'], 'PASS')
        self.assertEqual(result['message_case']['storage_verification']['new_rows'], 3)
        self.assertTrue(result['message_case']['storage_verification']['baseline_unchanged'])
        self.assertEqual(result['shutdown']['status'], 'STOPPED')
        self.assertEqual(result['credentials']['credential_memory'], 'CLEARED')
        self.assertFalse(result['automatic_delete'])

    def test_failed_delivery_is_not_success_and_callback_is_not_triggered(self):
        original = OwnedLocalWorkspaceMessages._post
        def fail_delivery(case, operation):
            result = original(case, operation)
            if operation == 'live-chat':
                result['delivery_results'][0]['success'] = False
            return result
        with patch.object(OwnedLocalWorkspaceMessages, '_post', fail_delivery):
            rc, result = self.execute()
        self.assertEqual(rc, 2)
        journal = read(self.root/'local-workspace-messages.json')
        self.assertEqual(journal['status'], 'BLOCKED')
        self.assertEqual([r['operation'] for r in journal['operations']], list(OPERATIONS[:2]))
        self.assertEqual(result['shutdown']['status'], 'STOPPED')

    def test_cancel_after_first_message_preserves_first_write_and_stops_child(self):
        original = OwnedLocalWorkspaceMessages._post
        def cancel(case, operation):
            original(case, operation)
            raise KeyboardInterrupt()
        with patch.object(OwnedLocalWorkspaceMessages, '_post', cancel):
            rc, result = self.execute()
        self.assertEqual(rc, 130)
        self.assertEqual(result['status'], 'CANCELLED')
        self.assertEqual(len(result['message_case']['operations']), 1)
        self.assertEqual(result['shutdown']['status'], 'STOPPED')


    def test_missing_wire_receipt_after_real_delivery_blocks_parent(self):
        from bcs_e2e.local_bot_receiver import OwnedLocalBotReceiver
        original = OwnedLocalBotReceiver.receive_delivery
        def missing(receiver, **kwargs):
            receipt = original(receiver, **kwargs)
            receipt.pop('request_id')
            return receipt
        with patch.object(OwnedLocalBotReceiver, 'receive_delivery', missing):
            rc, result = self.execute()
        self.assertEqual(rc, 2)
        self.assertEqual(result['message_case']['status'], 'BLOCKED')
        self.assertEqual(len(result['message_case']['operations']), 1)
        self.assertEqual(result['shutdown']['status'], 'STOPPED')

    def test_post_response_loss_never_replays_or_starts_chat(self):
        original = OwnedLocalWorkspaceMessages._expected
        installed = []
        def install(case, operation):
            plan = original(case, operation)
            if operation == 'persistent-message' and not installed:
                installed.append(True)
                real_open = case.identities.opener.open
                def lose(request, *args, **kwargs):
                    response = real_open(request, *args, **kwargs)
                    if request.get_method() == 'POST' and request.full_url.endswith('/messages'):
                        response.close()
                        raise OSError('synthetic response loss after actual message write')
                    return response
                case.identities.opener.open = lose
            return plan
        with patch.object(OwnedLocalWorkspaceMessages, '_expected', install):
            rc, result = self.execute()
        self.assertEqual(rc, 2)
        journal = result['message_case']
        self.assertEqual(len(journal['operations']), 1)
        self.assertEqual(journal['operations'][0]['status'], 'UNKNOWN')
        self.assertEqual(journal['assertions'], [{'id': 'WORKSPACE.messages', 'status': 'FAIL'}])
        self.assertEqual(result['shutdown']['status'], 'STOPPED')


if __name__ == '__main__':
    unittest.main()
