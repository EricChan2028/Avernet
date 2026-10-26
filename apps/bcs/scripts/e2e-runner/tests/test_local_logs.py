"""Owned-source and finite projection tests using a synthetic access-log file."""
from pathlib import Path
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bcs_e2e.ledger import Ledger
from bcs_e2e.local_config import prepare_local_bcs_inputs
from bcs_e2e.local_logs import OwnedLocalLogClient, OwnedLocalLogObserver, _event
from bcs_e2e.log_observer import LogObservationError, LogQuery
from bcs_e2e.owned_bcs import OwnedBcsProcess


def event(request_id='req-1', **changes):
    return {'timestamp': '1970-01-01 00:00:15', 'level': 'INFO', 'target': 'bcs_http_access',
            'message': 'http.request.response_ready', 'request_id': request_id,
            'route': '/test/{id}', 'method': 'GET', 'status': 200, 'duration_ms': 1.2,
            'outcome': 'success', **changes}


class LocalLogTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        inputs = prepare_local_bcs_inputs(self.root / 'runtime', run_id='log-run', port=45000)
        self.runtime = Mock(spec=OwnedBcsProcess)
        self.runtime.inputs, self.runtime.root = inputs, inputs.root
        self.runtime.state, self.runtime.ready, self.runtime.run_id = 'RUNNING', True, 'log-run'
        self.runtime.child = Mock(); self.runtime.child.poll.return_value = None
        self.runtime._owns_listener.return_value = True
        self.path = inputs.root / 'logs/e2e-access.jsonl'
        self.path.write_text(json.dumps(event()) + '\n')
        info = self.path.stat(); self.runtime.access_log_identity = (info.st_dev, info.st_ino)
        self.ledger_path = self.root / 'run.sqlite'
        self.ledger = Ledger(self.ledger_path, 'log-run', 'a'*64, create=True); self.addCleanup(self.ledger.close)
        self.ledger.record_correlation('PRE-01', 1, {'submitted_request_id':'req-1','operation':'group.read'})
        self.client = self.make_client()
        self.query = LogQuery('local', request_id='req-1', start_epoch=10, end_epoch=20)

    def make_client(self, **kwargs):
        return OwnedLocalLogClient(self.runtime, ledger_path=self.ledger_path, profile_digest='a'*64,
                                   case_id='PRE-01', **kwargs)

    def test_exact_request_window_utc_and_private_field_projection(self):
        rows = [event('req-1-extra'), event('req-1', timestamp='1970-01-01 00:00:30'),
                event('req-1', private_token='private-secret', route='/private/query?token=private-value')]
        self.path.write_text(''.join(json.dumps(row)+'\n' for row in rows))
        before = self.path.read_bytes(), self.path.stat().st_mtime_ns
        result = self.client.query(self.query, timeout_seconds=2)
        self.assertEqual(result, {'success':True,'records':[{'environment':'local','timestamp':15,'request_id':'req-1'}],
                                  'truncated':False})
        self.assertEqual((self.path.read_bytes(), self.path.stat().st_mtime_ns), before)
        self.assertNotIn('private', json.dumps(result))
        self.client.query(self.query, timeout_seconds=2)
        self.assertEqual(self.client.scan_count, 1)

    def test_unbound_other_case_attempt_and_trace_queries_never_open_log(self):
        self.ledger.record_correlation('PRE-02', 1, {'submitted_request_id':'other-case','operation':'session.read'})
        self.ledger.record_correlation('PRE-01', 2, {'submitted_request_id':'other-attempt','operation':'group.read'})
        queries = [LogQuery('local', request_id=rid, start_epoch=10,end_epoch=20) for rid in ('unknown','other-case','other-attempt')]
        queries += [LogQuery('prod',request_id='req-1',start_epoch=10,end_epoch=20),
                    LogQuery('local',traceparent='00-'+'a'*32+'-'+'b'*16+'-01',start_epoch=10,end_epoch=20)]
        with patch.object(self.client, '_scan') as scan:
            for query in queries:
                with self.assertRaises(LogObservationError): self.client.query(query, timeout_seconds=2)
            scan.assert_not_called()

    def test_timezone_runtime_and_config_drift_block(self):
        for field, bad, restored in (('state','STOPPED','RUNNING'), ('ready',False,True), ('run_id','other','log-run')):
            setattr(self.runtime,field,bad)
            with self.assertRaises(LogObservationError): self.client.admit()
            setattr(self.runtime,field,restored)
        self.runtime.inputs.environment['TZ'] = 'Asia/Shanghai'
        with self.assertRaises(LogObservationError): self.client.admit()
        self.runtime.inputs.environment['TZ'] = 'UTC'
        config=json.loads(self.runtime.inputs.config_path.read_text()); config['logging']['outputs']=[]
        self.runtime.inputs.config_path.write_text(json.dumps(config))
        with self.assertRaises(LogObservationError): self.client.admit()

    def test_replacement_symlink_hardlink_and_truncation_are_not_adopted(self):
        self.client.query(self.query, timeout_seconds=2)
        self.path.write_bytes(b'')
        with self.assertRaises(LogObservationError): self.client.query(self.query, timeout_seconds=2)
        retained=self.path.with_name('retained'); self.path.rename(retained)
        self.path.symlink_to(retained)
        with self.assertRaises(LogObservationError): self.client.admit()
        self.path.unlink(); self.path.write_text(json.dumps(event())+'\n')
        with self.assertRaises(LogObservationError): self.client.admit()
        info=self.path.stat(); self.runtime.access_log_identity=(info.st_dev,info.st_ino)
        os.link(self.path,self.path.with_name('hardlink'))
        with self.assertRaises(LogObservationError): self.make_client().admit()

    def test_incomplete_line_byte_budget_and_result_limit_are_explicit(self):
        self.path.write_text(json.dumps(event())+'\n'+json.dumps(event())[:40])
        self.assertTrue(self.client.query(self.query, timeout_seconds=2)['truncated'])
        self.path.write_text((json.dumps(event())+'\n')*20)
        limited=self.make_client(max_scan_bytes=1024)
        result=limited.query(self.query, timeout_seconds=2)
        self.assertTrue(result['truncated']); self.assertGreater(len(result['records']),0)
        result=self.make_client().query(LogQuery('local',request_id='req-1',start_epoch=10,end_epoch=20,limit=1), timeout_seconds=2)
        self.assertTrue(result['truncated']); self.assertEqual(len(result['records']),1)

    def test_malformed_or_unbounded_lines_block_instead_of_matching_substrings(self):
        for raw in (b'private req-1 text\n', b'\xff\n', b'{"request_id":"x","request_id":"req-1"}\n',
                    (json.dumps(event(message='private req-1'))+'\n').encode(),
                    (json.dumps(event(private='x'*1000))+'\n').encode()):
            self.path.write_bytes(raw)
            with self.assertRaises(LogObservationError) as caught:
                self.make_client(max_line_bytes=256).query(self.query,timeout_seconds=2)
            self.assertNotIn('private',str(caught.exception))

    def test_empty_logs_are_incomplete_and_provider_is_optional_semantic_source(self):
        self.path.write_bytes(b'')
        observer=OwnedLocalLogObserver(self.runtime,ledger_path=self.ledger_path,profile_digest='a'*64,case_id='PRE-01')
        self.assertEqual(observer.admit()['backend'],'owned-local-access-json')
        result=observer.observe(self.query)
        self.assertEqual(result['status'],'INCOMPLETE'); self.assertEqual(result['record_count'],0)

    def test_access_event_types_and_status_are_not_business_verdicts(self):
        for value in (event(status=404,outcome='http_error'), event(status=101,outcome='upgraded'),
                      event(message='http.request.started'), event(message='http.request.cancelled',outcome='cancelled',level='WARN')):
            self.assertEqual(_event(json.dumps(value).encode())['request_id'],'req-1')
        for key,value in (('duration_ms',float('nan')),('status',True),('timestamp','1970-01-01T00:00:15Z'),
                          ('target','other'),('outcome','wrong')):
            with self.assertRaises(LogObservationError): _event(json.dumps(event(**{key:value})).encode())

    def test_deadline_blocks_before_log_scan(self):
        with patch('bcs_e2e.local_logs.time.monotonic',side_effect=[0,3]),patch.object(self.client,'_scan') as scan:
            with self.assertRaises(LogObservationError): self.client.query(self.query,timeout_seconds=2)
            scan.assert_not_called()
