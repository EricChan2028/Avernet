"""Offline transport matching never queries logs, renews authority or releases."""
import copy
from pathlib import Path
import sys
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bcs_e2e.profile import ConfigError, fingerprint
from bcs_e2e.run_state import read, write
from bcs_e2e.transport_plan import transport_plan, MAX_SENDS
from bcs_e2e.transport_projection import CONTRACT, match_transport
import test_mapping_recheck as mapping
import test_retained_recheck as legacy


class TransportEvidenceTests(unittest.TestCase):
    command = mapping.MappingRecheckTests.command
    boundaries = mapping.MappingRecheckTests.boundaries
    process = mapping.MappingRecheckTests.process
    execute = mapping.MappingRecheckTests.execute
    args = mapping.MappingRecheckTests.args
    assert_original_unchanged = mapping.MappingRecheckTests.assert_original_unchanged

    def setUp(self):
        mapping.MappingRecheckTests.setUp(self)
        rc, report = self.execute()
        self.assertEqual(rc, 2)
        self.assertEqual(report['execution_observation']['status'], 'OBSERVED')
        self.observation_id = 'read-plan-1'
        self.report_path = self.state/'observations'/self.observation_id/'report.json'
        self.saved = read(self.report_path)
        self.source_bytes = {p: p.read_bytes() for p in self.state.rglob('*') if p.is_file()}

    def plan(self):
        return transport_plan(self.profile, self.state, self.run_id, 'RETAIN-GROUP-01',
                              self.observation_id, 10, 20)

    def projection(self, plan, records):
        return {'schema_version': 1, 'contract': CONTRACT, 'plan_digest': plan['plan_digest'],
                'source_reference': 'test-projected-events', 'environment': plan['environment'],
                'status': 'OBSERVED', 'error_code': None, 'truncated': False, 'records': records}

    def ws(self, plan, **extra):
        d = plan['deliveries'][0]
        return {'event': 'WS_ACK', 'timestamp_ms': 15000, 'run_id': d['request_id'],
                'bot_id': d['target_bot_id'], 'group_id': d['group_id'], 'session_id': d['session_id'],
                'accepted_run_id': d['downstream_run_id'], **extra}

    def http_record(self, plan, event='HTTP_ACK', **extra):
        d = plan['deliveries'][0]
        record = {'event': event, 'timestamp_ms': 15000, 'run_id': d['run_id'],
                  'bot_id': d['target_bot_id'], 'provider_id': 'test-provider'}
        if event != 'HTTP_SSE_ACCEPTED': record['method'] = 'chat.send'
        if event == 'HTTP_START': record['provider_bot_ref'] = 'test-reference'
        return {**record, **extra}

    def transport_args(self):
        return ['retained-transport', '--profile', str(self.profile_path), '--state-dir', str(self.state),
                '--run-id', self.run_id, '--case', 'RETAIN-GROUP-01', '--observation-id', self.observation_id,
                '--start-epoch', '10', '--end-epoch', '20']

    def test_real_cli_plan_and_matching_are_offline_and_leave_all_source_bytes_unchanged(self):
        with patch('bcs_e2e.transport.ReadClient.__init__', side_effect=AssertionError('no credentials or HTTP')), \
             patch('subprocess.run', side_effect=AssertionError('no CLI or MCP')), \
             patch('bcs_e2e.pool.PoolLease.from_file', side_effect=AssertionError('no pool acquisition')):
            rc, plan = self.command(self.transport_args())
            self.assertEqual(rc, 0, plan)
            self.assertEqual(plan['status'], 'PLANNED_OFFLINE')
            self.assertEqual(plan['query_permission'], 'NONE')
            projection = self.root/'projection.json'
            write(projection, self.projection(plan, [self.ws(plan)]))
            rc, result = self.command(self.transport_args()+['--projection', str(projection)])
        self.assertEqual(rc, 0, result)
        self.assertEqual(result['matching_result'], 'MATCHED')
        self.assertEqual(result['source_verification'], 'NOT_VERIFIED')
        self.assertEqual(result['evidence_origin'], 'OFFLINE_SUPPLIED')
        self.assertEqual(result['scope_status'], 'UNCONFIRMED')
        self.assertFalse(result['pool_release_authorized'])
        self.assertFalse(result['ready_to_execute'])
        self.assertEqual({p: p.read_bytes() for p in self.state.rglob('*') if p.is_file()}, self.source_bytes)
        self.assert_original_unchanged()

    def test_ws_uses_attempt_not_canonical_or_payload_alias(self):
        plan = self.plan()
        for wrong in (plan['deliveries'][0]['run_id'], 'another-alias'):
            with self.subTest(wrong=wrong), self.assertRaises(ConfigError):
                match_transport(plan, self.projection(plan, [self.ws(plan, run_id=wrong)]))
        matched = match_transport(plan, self.projection(plan, [self.ws(plan, accepted_run_id='unbound-alias')]))
        self.assertEqual(matched['matching_result'], 'MATCHED')  # alias is not the match key

    def test_http_uses_canonical_not_attempt_and_requires_send_method(self):
        plan = self.plan()
        for extra in ({'run_id': plan['deliveries'][0]['request_id']}, {'method': 'chat.inject'}):
            with self.subTest(extra=extra), self.assertRaises(ConfigError):
                match_transport(plan, self.projection(plan, [self.http_record(plan, **extra)]))
        for event in ('HTTP_ACK', 'HTTP_CALLBACK_ACK'):
            result = match_transport(plan, self.projection(plan, [self.http_record(plan, event)]))
            self.assertEqual(result['matching_result'], 'MATCHED')
            self.assertEqual(result['deliveries'][0]['engine_implementation'], 'NOT_VERIFIED')

    def test_sse_needs_same_provider_start_not_start_alone(self):
        plan = self.plan()
        for records in ([self.http_record(plan, 'HTTP_START')], [self.http_record(plan, 'HTTP_SSE_ACCEPTED')]):
            self.assertEqual(match_transport(plan, self.projection(plan, records))['matching_result'], 'INCOMPLETE')
        records = [self.http_record(plan, 'HTTP_START'), self.http_record(plan, 'HTTP_SSE_ACCEPTED')]
        self.assertEqual(match_transport(plan, self.projection(plan, records))['matching_result'], 'MATCHED')
        records[0]['provider_id'] = 'other-provider'
        self.assertEqual(match_transport(plan, self.projection(plan, records))['matching_result'], 'CONFLICT')

    def test_full_ws_tuple_and_single_http_bot_are_enforced(self):
        plan = self.plan()
        for field in ('bot_id', 'group_id', 'session_id'):
            with self.subTest(field=field), self.assertRaises(ConfigError):
                match_transport(plan, self.projection(plan, [self.ws(plan, **{field: 'foreign'})]))
        with self.assertRaises(ConfigError):
            match_transport(plan, self.projection(plan, [self.http_record(plan, bot_id='foreign')]))

    def test_two_deliveries_cannot_mix_identity_sets_or_drop_unmatched_member(self):
        value = copy.deepcopy(self.saved)
        session = value['execution_observation']['sessions'][0]
        member = self.profile['actors']['member']['bot_id']
        session['rows'].append({**session['rows'][0], 'delivery_id': 'second-delivery',
                               'run_id': 'second-run', 'request_id': 'second-request', 'target_bot_id': member})
        session['row_count'] = 2
        write(self.report_path, value)
        plan = self.plan()
        with self.assertRaises(ConfigError):
            match_transport(plan, self.projection(plan, [self.ws(plan, bot_id=member)]))
        partial = match_transport(plan, self.projection(plan, [self.ws(plan)]))
        self.assertEqual(partial['matching_result'], 'INCOMPLETE')
        self.assertEqual(partial['known_send_count'], 2)
        second = self.ws(plan, bot_id=member, run_id='second-request')
        complete = match_transport(plan, self.projection(plan, [self.ws(plan), second]))
        self.assertEqual(complete['matching_result'], 'MATCHED')

    def test_fast_final_before_ack_is_valid_but_window_and_type_are_strict(self):
        plan = self.plan()
        self.assertLess(plan['deliveries'][0]['terminal_at_ms'], 15000)
        self.assertEqual(match_transport(plan, self.projection(plan, [self.ws(plan)]))['matching_result'], 'MATCHED')
        for timestamp in (9999, 20000, True, 15000.0, '15000'):
            with self.subTest(timestamp=timestamp), self.assertRaises(ConfigError):
                match_transport(plan, self.projection(plan, [self.ws(plan, timestamp_ms=timestamp)]))

    def test_conflicting_transports_are_not_folded_to_http_or_preferred_ws(self):
        plan = self.plan()
        for records in ([self.ws(plan), self.http_record(plan)],
                        [self.http_record(plan), self.http_record(plan, 'HTTP_CALLBACK_ACK')],
                        [self.http_record(plan, 'HTTP_CALLBACK_ACK'), self.http_record(plan, 'HTTP_SSE_ACCEPTED')]):
            self.assertEqual(match_transport(plan, self.projection(plan, records))['matching_result'], 'CONFLICT')

    def test_multi_attempt_http_stays_run_level_even_with_final_ws_ack(self):
        value = copy.deepcopy(self.saved)
        value['execution_observation']['sessions'][0]['rows'][0]['attempt_no'] = 2
        write(self.report_path, value)
        plan = self.plan()
        for records in ([self.http_record(plan)], [self.ws(plan), self.http_record(plan)]):
            result = match_transport(plan, self.projection(plan, records))
            self.assertEqual(result['matching_result'], 'INCOMPLETE')
            self.assertEqual(result['deliveries'][0]['run_level_http_evidence'], ['HTTP_ACK'])
        self.assertEqual(match_transport(plan, self.projection(plan, [self.ws(plan)]))['matching_result'], 'MATCHED')

    def test_known_accepted_alias_conflict_is_not_silently_rebound(self):
        value = copy.deepcopy(self.saved)
        value['execution_observation']['sessions'][0]['rows'][0]['downstream_run_id'] = 'known-alias'
        write(self.report_path, value)
        plan = self.plan()
        result = match_transport(plan, self.projection(plan, [self.ws(plan, accepted_run_id='other-alias')]))
        self.assertEqual(result['matching_result'], 'CONFLICT')

    def test_unbound_aliases_cannot_conflict_with_each_other(self):
        plan = self.plan()
        self.assertIsNone(plan['deliveries'][0]['downstream_run_id'])
        result = match_transport(plan, self.projection(plan, [
            self.ws(plan, accepted_run_id='alias-a'), self.ws(plan, accepted_run_id='alias-b')]))
        self.assertEqual(result['matching_result'], 'CONFLICT')
        self.assertEqual(result['duplicate_count'], 0)
        self.assertIn('ACCEPTED_ALIAS_CONFLICT', result['deliveries'][0]['reasons'])

    def test_non_scalar_source_fields_return_blocked_json_without_payload_or_traceback(self):
        for key in ('kind', 'status', 'target_bot_id'):
            for bad in (['private-sentinel'], {'private-sentinel': 'untrusted'}):
                value = copy.deepcopy(self.saved)
                value['execution_observation']['sessions'][0]['rows'][0][key] = bad
                write(self.report_path, value)
                rc, report = self.command(self.transport_args())
                self.assertEqual(rc, 2, report)
                self.assertEqual(report['status'], 'BLOCKED')
                self.assertNotIn('private-sentinel', str(report))

    def test_empty_denied_truncated_duplicate_and_oversize_never_match_complete(self):
        plan = self.plan(); empty = self.projection(plan, [])
        self.assertEqual(match_transport(plan, empty)['matching_result'], 'INCOMPLETE')
        for code in ('PERMISSION_DENIED', 'AUTHENTICATION_REQUIRED', 'TIMEOUT', 'QUERY_ERROR', 'SCHEMA_ERROR'):
            result = match_transport(plan, {**empty, 'status': 'ERROR', 'error_code': code})
            self.assertEqual(result['matching_result'], 'UNAVAILABLE')
            self.assertEqual(result['error_code'], code)
        truncated = {**self.projection(plan, [self.ws(plan)]), 'truncated': True}
        self.assertEqual(match_transport(plan, truncated)['matching_result'], 'INCOMPLETE')
        duplicate = self.projection(plan, [self.ws(plan)]*2)
        self.assertEqual(match_transport(plan, duplicate)['duplicate_count'], 1)
        self.assertEqual(match_transport(plan, duplicate)['matching_result'], 'INCOMPLETE')
        with self.assertRaises(ConfigError): match_transport(plan, self.projection(plan, [self.ws(plan)]*31))

    def test_strict_projection_contract_does_not_echo_sensitive_payload(self):
        plan = self.plan(); base = self.projection(plan, [self.ws(plan)])
        edits = [{'environment': 'prod'}, {'plan_digest': '0'*64}, {'schema_version': True},
                 {'contract': 'raw-log'}, {'truncated': 1}, {'error_code': 'QUERY_ERROR'},
                 {'status': 'ERROR', 'error_code': 'private raw platform error'},
                 {'content': 'private raw message'}, {'records': [self.ws(plan, headers='private-token')]},
                 {'records': [self.ws(plan, event='POSTING_WEBHOOK')]}]
        for edit in edits:
            with self.subTest(edit=edit), self.assertRaises(ConfigError) as error:
                match_transport(plan, {**base, **edit})
            self.assertNotIn('private', str(error.exception))

    def test_source_drift_invalid_snapshots_and_completed_send_shape_block_planning(self):
        patches = [{'contract': 'unknown-observation'}, {'status': 'TIMED_OUT'}, {'status': 'INCOMPLETE'}, {'stop_reason': 'INCONSISTENT_HISTORY'},
                   {'errors': [{'error_type': 'TimeoutError'}]}, {'sessions': []}]
        for edit in patches:
            value = copy.deepcopy(self.saved); value['execution_observation'].update(edit)
            write(self.report_path, value)
            with self.subTest(edit=edit), self.assertRaises(ConfigError): self.plan()
        for edit in ({'request_id': None}, {'run_id': None}, {'status': 'expired'},
                     {'may_have_been_sent': False}, {'attempt_no': 0}, {'state_version': 0}):
            value = copy.deepcopy(self.saved); value['execution_observation']['sessions'][0]['rows'][0].update(edit)
            write(self.report_path, value)
            with self.subTest(edit=edit), self.assertRaises(ConfigError): self.plan()
        write(self.report_path, self.saved)
        state = read(self.state/'run.json'); state['note'] = 'external change'; write(self.state/'run.json', state)
        with self.assertRaises(ConfigError): self.plan()

    def test_complete_inventory_budget_and_ambiguous_identities_never_select_a_prefix(self):
        value = copy.deepcopy(self.saved); session = value['execution_observation']['sessions'][0]
        row = session['rows'][0]
        session['rows'] = [{**row, 'delivery_id': f'd-{i}', 'run_id': f'r-{i}', 'request_id': f'q-{i}'}
                           for i in range(MAX_SENDS+1)]
        session['row_count'] = len(session['rows']); write(self.report_path, value)
        with self.assertRaises(ConfigError): self.plan()
        for key in ('run_id', 'request_id'):
            value = copy.deepcopy(self.saved); session = value['execution_observation']['sessions'][0]
            session['rows'].append({**row, 'delivery_id': 'd-other', 'run_id': 'r-other', 'request_id': 'q-other', key: row[key]})
            session['row_count'] = 2; write(self.report_path, value)
            with self.subTest(key=key), self.assertRaises(ConfigError): self.plan()

    def test_missing_foreign_symlinked_or_changed_receipt_is_not_accepted(self):
        for observation in ('../escape', '/tmp/foreign', 'missing'):
            with self.subTest(observation=observation), self.assertRaises(ConfigError):
                transport_plan(self.profile, self.state, self.run_id, 'RETAIN-GROUP-01', observation, 10, 20)
        receipt = self.report_path.parent/'read-plan.json'
        old = read(receipt); write(receipt, {**old, 'observation_id': 'changed'})
        with self.assertRaises(ConfigError): self.plan()
        write(receipt, old)
        self.report_path.unlink(); self.report_path.symlink_to(self.root/'foreign.json')
        write(self.root/'foreign.json', self.saved)
        with self.assertRaises(RuntimeError): self.plan()

    def test_time_window_invalid_types_and_bound_are_refused(self):
        for start, end in ((True, 20), (10.0, 20), (10, 10), (10, 911), (-1, 20)):
            with self.subTest(start=start, end=end), self.assertRaises(ConfigError):
                transport_plan(self.profile, self.state, self.run_id, 'RETAIN-GROUP-01', self.observation_id, start, end)

    def test_changed_source_report_invalidates_previously_projected_log_bundle(self):
        old = self.plan(); projection = self.projection(old, [self.ws(old)])
        value = copy.deepcopy(self.saved); value['execution_observation']['sessions'][0]['rows'][0]['state_version'] += 1
        write(self.report_path, value)
        with self.assertRaises(ConfigError): match_transport(self.plan(), projection)

    def test_late_source_mutation_during_offline_read_is_detected(self):
        from bcs_e2e import transport_plan as module
        real_read = module.read
        def changed(path):
            result = real_read(path)
            if path.name == 'read-plan.json':
                value = read(self.report_path); value['finished_at'] += 1; write(self.report_path, value)
            return result
        with patch.object(module, 'read', side_effect=changed), self.assertRaises(ConfigError): self.plan()


class LegacyTransportEvidenceTests(unittest.TestCase):
    setUp = legacy.RetainedRecheckTests.setUp
    command = legacy.RetainedRecheckTests.command
    boundaries = legacy.RetainedRecheckTests.boundaries
    process = legacy.RetainedRecheckTests.process
    execute = legacy.RetainedRecheckTests.execute
    args = legacy.RetainedRecheckTests.args

    def test_expired_historical_review_is_readable_but_never_renewed(self):
        self.execute()
        path = self.state/'observations/observe-1/recheck-review.json'
        original = path.read_bytes()
        with patch('time.time', return_value=int(time.time())+86400):
            plan = transport_plan(self.profile, self.state, self.run_id, 'RETAIN-GROUP-01', 'observe-1', 10, 20)
        self.assertEqual(plan['query_permission'], 'NONE')
        self.assertEqual(path.read_bytes(), original)


class TwoSessionTransportTests(unittest.TestCase):
    import test_delivery_observation as fixtures
    setUp = fixtures.RetainedDeliveryCompositionTests.setUp
    command = fixtures.RetainedDeliveryCompositionTests.command
    boundaries = fixtures.RetainedDeliveryCompositionTests.boundaries
    args = fixtures.RetainedDeliveryCompositionTests.args

    def process(self, command, **kwargs):
        import json
        response = self.fixtures.RetainedDeliveryCompositionTests.process(self, command, **kwargs)
        args = json.loads(command[4]) if '--args' in command else {}
        if args.get('table') == 'bcs_message_deliveries' and command[2].endswith('.db_query'):
            payload = json.loads(response.stdout)
            for row in payload['data']:
                row.update(delivery_id='d-'+row['session_id'], run_id='r-'+row['session_id'],
                           request_id='q-'+row['session_id'])
            response.stdout = json.dumps(payload)
        return response

    def test_real_two_session_composition_requires_both_owned_snapshots(self):
        from bcs_e2e.recheck_review import recheck_plan
        self.fixtures.RetainedDeliveryCompositionTests.enable(self, 'RETAIN-SESSION-01')
        with self.boundaries(): rc, report = self.command(self.args())
        self.assertEqual(rc, 2); self.assertEqual(report['functional'], 'PASS')
        mapping_path = self.root/'mapping.json'; write(mapping_path, mapping.confirmation(self.profile))
        value = recheck_plan(self.profile, self.state, self.run_id, 'RETAIN-SESSION-01',
                             mapping_confirmation=mapping_path)['read_plan']
        value['observation_id'] = 'both-sessions'
        review_path = self.root/'plan.json'; write(review_path, value)
        with self.boundaries():
            rc, report = self.command(['retained-recheck', '--profile', str(self.profile_path),
                '--state-dir', str(self.state), '--run-id', self.run_id, '--case', 'RETAIN-SESSION-01',
                '--read-plan', str(review_path), '--mapping-confirmation', str(mapping_path),
                '--mcporter-binary', str(self.executable), '--allow-observer-read', '--confirm-run-id', self.run_id])
        self.assertEqual(rc, 2); self.assertEqual(report['execution_observation']['status'], 'OBSERVED')
        plan = transport_plan(self.profile, self.state, self.run_id, 'RETAIN-SESSION-01', 'both-sessions', 10, 20)
        self.assertEqual(len(plan['deliveries']), 2)
        self.assertEqual(len({d['session_id'] for d in plan['deliveries']}), 2)
        report_path = self.state/'observations/both-sessions/report.json'
        value = read(report_path); value['execution_observation']['sessions'].pop(); write(report_path, value)
        with self.assertRaises(ConfigError):
            transport_plan(self.profile, self.state, self.run_id, 'RETAIN-SESSION-01', 'both-sessions', 10, 20)
