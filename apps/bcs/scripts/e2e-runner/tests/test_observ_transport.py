"""Real CLI/source artifacts with a fake bounded Observ process, never the network."""
import copy
from datetime import datetime, timezone
import html
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bcs_e2e import observ_wire as wire
from bcs_e2e.bounded_process import ProcessCaptureError, ProcessCaptureTimeout
from bcs_e2e.run_state import read, write
import test_transport_evidence as fixtures


class ObservTransportTests(unittest.TestCase):
    command = fixtures.TransportEvidenceTests.command
    boundaries = fixtures.TransportEvidenceTests.boundaries
    process = fixtures.TransportEvidenceTests.process
    execute = fixtures.TransportEvidenceTests.execute
    args = fixtures.TransportEvidenceTests.args
    assert_original_unchanged = fixtures.TransportEvidenceTests.assert_original_unchanged

    def setUp(self):
        fixtures.TransportEvidenceTests.setUp(self)
        self.root = self.root.resolve()  # macOS /var is a symlink; supply the canonical output path.
        delivery = self.saved['execution_observation']['sessions'][0]['rows'][0]
        delivery['downstream_run_id'] = delivery['run_id']
        write(self.report_path, self.saved)
        self.source_bytes = {p: p.read_bytes() for p in self.state.rglob('*') if p.is_file()}
        self.binding_path = self.root/'log-binding.json'
        self.binding = {'schema_version': 1, 'contract': wire.CONTRACT,
            'environment': self.profile['environment']['expected_bcs_env'], 'source_environment': 'PRE',
            'app_name': 'test-app', 'from_uri': 'amodel://test.app/test-app',
            'to_uri': 'amodel://test.server', 'log_directory': '/home/admin/logs/bcs',
            'log_file': 'bcs-messages.log', 'timezone': 'UTC'}
        write(self.binding_path, self.binding)
        self.output = self.root/'acquisition-1'
        self.observ_calls = []
        self.instance = {'app': 'test-app', 'env': 'PRE', 'status': 'online',
                         'ip': '192.0.2.1', 'hostname': 'test-pre-1', 'uri': 'amodel://test.server/test-pre-1'}
        self.edit_response = lambda command, response: response

    def cli_args(self):
        return ['retained-observ-ws', '--profile', str(self.profile_path), '--state-dir', str(self.state),
            '--run-id', self.run_id, '--case', 'RETAIN-GROUP-01', '--observation-id', self.observation_id,
            '--start-epoch', '0', '--end-epoch', '60', '--log-binding', str(self.binding_path),
            '--observ-binary', str(self.executable), '--output-dir', str(self.output),
            '--allow-observer-read', '--confirm-run-id', self.run_id]

    def edge(self, row):
        return {'entityData': row, 'sourceUri': self.binding['from_uri'],
                'targetUri': row['uri'], 'uri': row['uri']}

    def capture(self, command, **kwargs):
        self.observ_calls.append(command)
        self.assertGreater(kwargs['timeout_seconds'], 0)
        self.assertLessEqual(kwargs['timeout_seconds'], 15)
        self.assertEqual(kwargs['max_output_bytes'], 2_097_152)
        if command[1:4] == ['entity', 'relation', 'list']:
            response = {'data': [self.edge(self.instance), self.edge({**self.instance, 'env': 'PROD', 'ip': '192.0.2.9'})],
                        'meta': {'returned': 2, 'hasMore': False, 'truncated': False}}
        elif command[1:3] == ['log', 'list']:
            response = {'success': True, 'data': {'ip': self.instance['ip'], 'dirTrees': [
                {'dir': False, 'fullPath': '/home/admin/logs/bcs/bcs-messages.log'}]},
                'ip': '192.0.2.250'}  # observation backend, not the target
        elif command[1:3] == ['log', 'grep']:
            def flag(name): return command[command.index(name)+1]
            echo = {'ip': flag('--ip'), 'fullPath': flag('--full-path'), 'time': int(flag('--time')),
                    'limit': int(flag('--limit')), 'keyword': flag('--keyword').split(',')}
            d = self.saved['execution_observation']['sessions'][0]['rows'][0]
            event = {'timestamp': '1970-01-01 00:00:15', 'target': 'bcs_message', 'schema_version': 1,
                     'event_type': 'bot_accept', 'status': 'accepted', 'run_id': d['request_id'],
                     'bot_id': d['target_bot_id'], 'group_id': d['group_id'], 'session_id': d['session_id'],
                     'accepted_run_id': d['downstream_run_id'], 'message': 'PRIVATE-PROMPT-SENTINEL',
                     'headers': {'Cookie': 'PRIVATE-COOKIE-SENTINEL'}}
            row = {'__source__': self.instance['ip'], '__tag__:__hostname__': self.instance['hostname'],
                   '__time__': '15', 'content': html.escape(json.dumps(event))}
            response = {'success': True, 'data': {'matched': 1, 'logs': [row], 'query': echo}}
        else:
            self.fail('unexpected command: '+repr(command[1:4]))
        response = self.edit_response(command, response)
        return subprocess.CompletedProcess(command, 0, json.dumps(response).encode(), b'')

    def run_cli(self, args=None):
        with patch('bcs_e2e.observ_transport.capture', side_effect=self.capture), \
             patch('bcs_e2e.observ_transport.time.time', return_value=120):
            return self.command(self.cli_args() if args is None else args)

    def assert_sources(self):
        self.assertEqual({p: p.read_bytes() for p in self.state.rglob('*') if p.is_file()}, self.source_bytes)
        self.assert_original_unchanged()

    def test_cli_collects_real_schema_and_leaves_original_run_and_pool_unchanged(self):
        with patch('bcs_e2e.transport.ReadClient.__init__', side_effect=AssertionError('no Cookie/HTTP')):
            rc, result = self.run_cli()
        self.assertEqual(rc, 0, result)
        self.assertEqual(result['status'], 'TRANSPORT_OBSERVED')
        self.assertEqual(result['matching']['matching_result'], 'MATCHED')
        self.assertEqual(result['matching']['source_verification'], 'NOT_VERIFIED')
        self.assertEqual(result['scope_status'], 'UNCONFIRMED')
        self.assertFalse(result['pool_release_authorized'])
        self.assertFalse(result['ready_to_execute'])
        self.assertEqual(len(self.observ_calls), 3)
        self.assertEqual(read(self.output/'acquisition.json'), result)
        self.assertEqual(self.output.stat().st_mode & 0o777, 0o700)
        self.assertEqual((self.output/'acquisition.json').stat().st_mode & 0o777, 0o600)
        encoded=json.dumps(result)
        self.assertNotIn('PRIVATE-PROMPT-SENTINEL', encoded)
        self.assertNotIn('PRIVATE-COOKIE-SENTINEL', encoded)
        self.assert_sources()

    def test_each_new_round_rediscovers_changed_instances(self):
        self.run_cli()
        self.instance.update(ip='192.0.2.2', hostname='test-pre-2', uri='amodel://test.server/test-pre-2')
        self.output = self.root/'acquisition-2'
        self.observ_calls.clear()
        rc, report = self.run_cli()
        self.assertEqual(rc, 0, report)
        self.assertEqual(self.observ_calls[0][1:4], ['entity', 'relation', 'list'])
        self.assertEqual(report['query_plan'][0]['ip'], '192.0.2.2')
        self.assertNotIn('192.0.2.1', json.dumps(report))

    def test_failed_rediscovery_does_not_reuse_previous_successful_inventory(self):
        rc, previous = self.run_cli()
        self.assertEqual(rc, 0, previous)
        self.assertEqual(previous['discovery']['historical_coverage'], 'NOT_VERIFIED')
        receipt = (self.output/'acquisition.json').read_bytes()
        previous_output = self.output
        self.output = self.root/'failed-rediscovery'
        self.observ_calls.clear()
        self.edit_response = lambda command, response: {
            'data': [], 'meta': {'returned': 0, 'hasMore': False, 'truncated': False}}
        rc, report = self.run_cli()
        self.assertEqual(rc, 2, report)
        self.assertEqual(report['status'], 'BLOCKED')
        self.assertEqual(len(self.observ_calls), 1)
        self.assertEqual(self.observ_calls[0][1:4], ['entity', 'relation', 'list'])
        self.assertNotIn('query_plan', report)
        self.assertNotIn('192.0.2.1', json.dumps(report))
        self.assertFalse(report['pool_release_authorized'])
        self.assertEqual(receipt, (previous_output/'acquisition.json').read_bytes())
        self.assert_sources()

    def test_no_read_permission_or_wrong_run_never_starts_process(self):
        for args in ([a for a in self.cli_args() if a != '--allow-observer-read'], self.cli_args()[:-1]+['wrong']):
            rc, _ = self.run_cli(args)
            self.assertEqual(rc, 2); self.assertEqual(self.observ_calls, [])
        self.assertFalse(self.output.exists())

    def test_output_cannot_replay_or_live_under_original_run(self):
        self.run_cli(); self.observ_calls.clear()
        rc, _ = self.run_cli()
        self.assertEqual(rc, 2); self.assertEqual(self.observ_calls, [])
        self.output = self.state/'new-acquisition'
        rc, _ = self.run_cli()
        self.assertEqual(rc, 2); self.assertEqual(self.observ_calls, [])
        self.assert_sources()

    def test_minute_window_not_silently_expanded_or_old_logs_queried(self):
        for start, end in (('1','60'), ('0','61'), ('-60','0'), ('0','180')):
            args=self.cli_args(); args[args.index('--start-epoch')+1]=start; args[args.index('--end-epoch')+1]=end
            rc, _ = self.run_cli(args)
            self.assertEqual(rc, 2); self.assertEqual(self.observ_calls, [])

    def test_binding_extra_fields_environment_and_paths_fail_before_reads(self):
        for edit in ({'ip':'192.0.2.1'}, {'environment':'prod'}, {'source_environment':'PROD'}, {'log_directory':'/home/admin/logs/../secrets'},
                     {'log_file':'../cookie'}, {'timezone':'Unknown/Zone'}, {'from_uri':'amodel://test.app/other'}):
            write(self.binding_path, {**self.binding, **edit})
            rc, _ = self.run_cli(); self.assertEqual(rc, 2); self.assertEqual(self.observ_calls, [])

    def test_default_production_log_target_rejected_before_grep(self):
        def edit(command, response):
            if command[1:3] == ['log','list']: response['data']['ip']='192.0.2.9'
            return response
        self.edit_response=edit
        rc, result=self.run_cli()
        self.assertEqual(rc, 2); self.assertEqual(result['status'],'BLOCKED')
        self.assertEqual(len(self.observ_calls),2)
        self.assertNotIn('projection',result)
        self.assert_sources()

    def test_incomplete_discovery_never_queries_logs(self):
        for index, meta in enumerate(({'truncated':True}, {'hasMore':True}, {'returned':3})):
            self.output=self.root/f'incomplete-{index}'; self.observ_calls.clear()
            self.edit_response=lambda command,response: {**response,'meta':{**response['meta'],**meta}}
            rc,result=self.run_cli();self.assertEqual(rc,2);self.assertEqual(len(self.observ_calls),1)
            self.assertEqual(result['status'],'BLOCKED')

    def test_pages_consumed_before_logs_and_opaque_cursor_not_echoed(self):
        def edit(command,response):
            if command[1:4]==['entity','relation','list']:
                if '--cursor' not in command:
                    return {'data':[], 'meta':{'returned':0,'hasMore':True,'truncated':False,'nextCursor':'opaque-secret-cursor'}}
                self.assertEqual(command[command.index('--cursor')+1],'opaque-secret-cursor')
            return response
        self.edit_response=edit
        rc,result=self.run_cli();self.assertEqual(rc,0,result)
        self.assertEqual(len(self.observ_calls),4)
        self.assertNotIn('opaque-secret-cursor',json.dumps(result))

    def test_repeated_cursor_and_too_many_instances_fail_closed(self):
        self.edit_response=lambda command,response: {'data':[], 'meta':{'returned':0,'hasMore':True,'truncated':False,'nextCursor':'repeated'}}
        rc,result=self.run_cli();self.assertEqual(rc,2);self.assertEqual(len(self.observ_calls),2)
        self.output=self.root/'too-many';self.observ_calls.clear()
        instances=[{**self.instance,'ip':f'192.0.2.{i+1}','uri':f'amodel://test.server/instance-{i}'} for i in range(9)]
        self.edit_response=lambda command,response: {'data':[self.edge(row) for row in instances],'meta':{'returned':9,'hasMore':False,'truncated':False}}
        rc,result=self.run_cli();self.assertEqual(rc,2);self.assertEqual(len(self.observ_calls),1)

    def test_query_echo_or_record_identity_mismatch_is_not_empty_success(self):
        edits=[lambda d:d['query'].update(ip='192.0.2.9'),
               lambda d:d['query'].update(keyword=['other']),
               lambda d:d['logs'][0].update(__source__='192.0.2.9'),
               lambda d:d['logs'][0].update(__time__='75'),
               lambda d:d.update(matched=6, logs=d['logs']*6)]
        for i, edit in enumerate(edits):
            self.output=self.root/f'bad-row-{i}'
            def change(command,response):
                if command[1:3]==['log','grep']:edit(response['data'])
                return response
            self.edit_response=change
            rc,result=self.run_cli();self.assertEqual(rc,2);self.assertEqual(result['status'],'BLOCKED')
            self.assertEqual(result['partial_records'],[])

    def test_empty_and_duplicate_records_are_incomplete_not_pass(self):
        for i, count in enumerate((0,2)):
            self.output=self.root/f'count-{i}'
            def edit(command,response):
                if command[1:3]==['log','grep']:
                    response['data']['matched']=count;response['data']['logs']*=count
                return response
            self.edit_response=edit
            rc,result=self.run_cli();self.assertEqual(rc,2,result)
            self.assertEqual(result['status'],'INCOMPLETE')
            self.assertEqual(result['matching']['matching_result'],'INCOMPLETE')

    def test_html_decoded_schema_alias_and_timestamp_are_checked(self):
        edits=({'bot_id':'other'}, {'accepted_run_id':'conflict'}, {'event_type':'bot_reject'},
               {'schema_version':True}, {'timestamp':'1970-01-01 00:00:16'})
        for i, changes in enumerate(edits):
            self.output=self.root/f'bad-event-{i}'
            def edit(command,response):
                if command[1:3]==['log','grep']:
                    row=response['data']['logs'][0];event=json.loads(html.unescape(row['content']))
                    event.update(changes);row['content']=html.escape(json.dumps(event))
                return response
            self.edit_response=edit
            rc,result=self.run_cli();self.assertEqual(rc,2,result)
            self.assertIn(result['status'],('BLOCKED','INCOMPLETE'))

    def test_process_error_timeout_and_cancellation_keep_safe_receipt(self):
        for i,error in enumerate((ProcessCaptureError('PRIVATE-ERROR'), ProcessCaptureTimeout('PRIVATE-TIMEOUT'), KeyboardInterrupt())):
            self.output=self.root/f'failed-{i}'
            with patch('bcs_e2e.observ_transport.capture',side_effect=error), \
                 patch('bcs_e2e.observ_transport.time.time',return_value=120):
                rc,result=self.command(self.cli_args())
            self.assertEqual(rc,130 if isinstance(error,KeyboardInterrupt) else 2)
            self.assertNotIn('PRIVATE-',json.dumps(result))
            self.assertEqual(read(self.output/'acquisition.json'),result)
            self.assertFalse(result['pool_release_authorized'])

    def test_binding_drift_during_read_stops_before_next_call(self):
        def edit(command,response):
            write(self.binding_path,{**self.binding,'app_name':'changed'})
            return response
        self.edit_response=edit
        rc,result=self.run_cli();self.assertEqual(rc,2);self.assertEqual(len(self.observ_calls),1)
        self.assertEqual(result['status'],'BLOCKED')
        self.assert_sources()

    def test_budget_exhaustion_never_starts_unplanned_log_queries(self):
        with patch('bcs_e2e.observ_transport.MAX_CALLS',2):
            rc,result=self.run_cli()
        self.assertEqual(rc,2);self.assertEqual(len(self.observ_calls),1)
        self.assertNotIn('projection',result)

    def test_discovery_page_budget_and_empty_environment_have_no_log_reads(self):
        for i, more in enumerate((True, False)):
            self.output=self.root/f'pages-{i}'; self.observ_calls.clear()
            self.edit_response=lambda command,response: {'data':[], 'meta':{'returned':0,
                'hasMore':more,'truncated':False, **({'nextCursor':f'cursor-{len(self.observ_calls)}'} if more else {})}}
            rc,result=self.run_cli();self.assertEqual(rc,2)
            self.assertEqual(len(self.observ_calls),3 if more else 1)
            self.assertNotIn('query_plan',result)

    def test_deadline_exhaustion_has_no_unbounded_retry(self):
        with patch('bcs_e2e.observ_transport.DEADLINE_SECONDS',0):
            rc,result=self.run_cli()
        self.assertEqual(rc,2);self.assertEqual(self.observ_calls,[])
        self.assertEqual(result['status'],'BLOCKED')

    def test_raw_json_preserves_entities_without_decoding_twice(self):
        def edit(command,response):
            if command[1:3]==['log','grep']:
                row=response['data']['logs'][0]
                event=json.loads(html.unescape(row['content']))
                event['message']='PRIVATE &quot; PROMPT'
                row['content']=json.dumps(event)
            return response
        self.edit_response=edit
        rc,result=self.run_cli();self.assertEqual(rc,0,result)
        self.assertNotIn('PRIVATE',json.dumps(result))

    def test_source_corruption_during_read_is_saved_as_blocked(self):
        original=self.report_path.read_bytes()
        def edit(command,response):
            self.report_path.write_text('invalid')
            return response
        self.edit_response=edit
        try:
            rc,result=self.run_cli()
            self.assertEqual(rc,2);self.assertEqual(result['status'],'BLOCKED')
            self.assertEqual(len(self.observ_calls),1)
            self.assertEqual(read(self.output/'acquisition.json'),result)
        finally:
            self.report_path.write_bytes(original)
        self.assert_sources()

    def test_duplicate_json_key_and_invalid_shape_never_leak_raw_errors(self):
        for i,content in enumerate(('RAW-PRIVATE-TEXT', '{"target":"PRIVATE","target":"bcs_message"}', '[]', '\ud800', '{"ignored":'+'['*1500+'0'+']'*1500+'}')):
            self.output=self.root/f'invalid-json-{i}'
            def edit(command,response):
                if command[1:3]==['log','grep']:response['data']['logs'][0]['content']=content
                return response
            self.edit_response=edit
            rc,result=self.run_cli();self.assertEqual(rc,2)
            self.assertEqual(result['partial_records'],[])
            self.assertEqual(result['status'],'BLOCKED')
            self.assertEqual(read(self.output/'acquisition.json'),result)
            self.assertNotIn('PRIVATE',json.dumps(result))

    def test_production_profile_has_no_process_or_output_creation(self):
        write(self.profile_path,{**self.profile,'environment':{**self.profile['environment'],'tier':'production'}})
        rc,result=self.run_cli();self.assertEqual(rc,2)
        self.assertEqual(self.observ_calls,[]);self.assertFalse(self.output.exists())

    def test_deep_cli_json_becomes_redacted_blocked_receipt(self):
        raw=b'{"data":'+b'['*1500+b'0'+b']'*1500+b'}'
        with patch('bcs_e2e.observ_transport.capture',return_value=subprocess.CompletedProcess([],0,raw,b'')),              patch('bcs_e2e.observ_transport.time.time',return_value=120):
            rc,result=self.command(self.cli_args())
        self.assertEqual(rc,2);self.assertEqual(result['status'],'BLOCKED')
        self.assertEqual(read(self.output/'acquisition.json'),result)

    def test_wrong_relation_parent_or_entity_target_is_rejected(self):
        for i, changes in enumerate(({'sourceUri':'amodel://test.app/foreign'},
                                     {'targetUri':'amodel://test.server/foreign'}, {'entityData':None})):
            self.output=self.root/f'bad-edge-{i}';self.observ_calls.clear()
            def edit(command,response):
                response['data'][0].update(changes)
                return response
            self.edit_response=edit
            rc,result=self.run_cli();self.assertEqual(rc,2)
            self.assertEqual(len(self.observ_calls),1)
            self.assertNotIn('discovery',result)


if __name__ == '__main__':
    unittest.main()
