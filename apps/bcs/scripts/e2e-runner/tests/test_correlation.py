from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bcs_e2e.correlation import new_traceparent, valid_traceparent
from bcs_e2e.profile import ConfigError, validate
from bcs_e2e.write_transport import WriteClient, WriteTransportError
from test_write_transport import Reply, profile


class CorrelationTests(unittest.TestCase):
    def test_traceparent_generator_is_w3c_shape_and_rejects_bad_values(self):
        value = new_traceparent()
        self.assertTrue(valid_traceparent(value))
        for bad in ("", "00-short-1-01", "01-" + "0" * 32 + "-" + "0" * 16 + "-01",
                    "00-" + "0" * 32 + "-" + "0" * 16 + "-zz"):
            self.assertFalse(valid_traceparent(bad))

    def test_zero_trace_or_parent_is_invalid_even_with_correct_hex_shape(self):
        for trace, parent in (('0'*32, '1'*16), ('1'*32, '0'*16), ('0'*32, '0'*16)):
            with self.subTest(trace=trace, parent=parent):
                self.assertFalse(valid_traceparent(f'00-{trace}-{parent}-01'))
        for flags in ('00', '01', 'ff'):
            self.assertTrue(valid_traceparent(f"00-{'0'*31}1-{'0'*15}1-{flags}"))
        for value in (None, {}, 1, '00-'+'A'*32+'-'+'b'*16+'-01'):
            self.assertFalse(valid_traceparent(value))

    def test_generator_remains_nonzero_at_random_range_boundaries(self):
        with patch('bcs_e2e.correlation.secrets.randbelow', return_value=0) as random:
            value = new_traceparent()
        self.assertEqual(value, f"00-{'0'*31}1-{'0'*15}1-01")
        self.assertTrue(valid_traceparent(value))
        self.assertEqual([c.args[0] for c in random.call_args_list], [(1<<128)-1, (1<<64)-1])
        with patch('bcs_e2e.correlation.secrets.randbelow', side_effect=lambda bound: bound-1):
            self.assertEqual(new_traceparent(), f"00-{'f'*32}-{'f'*16}-01")

    def test_zero_response_trace_cannot_become_effective_trace_or_ledger_correlation(self):
        from bcs_e2e.ledger import Ledger, LedgerError
        p = profile(); p['route_bindings']['group.read']['traceparent'] = 'w3c'
        for trace, parent in (('0'*32, '1'*16), ('1'*32, '0'*16)):
            invalid = f'00-{trace}-{parent}-01'
            response = Reply({'code': 20000, 'message': 'OK', 'request_id': 'r', 'data': {}},
                             headers={'traceparent': invalid, 'x-request-id': 'actual-server'})
            with patch.dict(os.environ, {'BCS_E2E_COOKIE': 'test-cookie'}), \
                 patch('bcs_e2e.write_transport.build_opener') as opener:
                opener.return_value.open.return_value = response
                with self.assertRaises(WriteTransportError) as caught:
                    WriteClient(p).call('group.read', group_id='g')
            record = caught.exception.request_records[0]
            self.assertEqual(record['response_request_id'], 'actual-server')
            self.assertNotIn('effective_traceparent', record)
            self.assertTrue(valid_traceparent(record['submitted_traceparent']))
            with tempfile.TemporaryDirectory() as tmp, Ledger(Path(tmp)/'run.sqlite', 'run', 'a'*64, create=True) as ledger:
                for field in ('submitted_traceparent', 'effective_traceparent'):
                    with self.assertRaises(LedgerError):
                        ledger.record_correlation('PRE-01', 1, {'operation': 'group.read',
                            'submitted_request_id': 'r', field: invalid})
                self.assertEqual(ledger.correlations(), [])

    def test_zero_trace_queries_stop_before_backend_and_zero_returned_span_is_rejected(self):
        from unittest.mock import Mock
        from bcs_e2e.log_observer import BoundedLogObserver, LogObservationError, LogQuery
        backend = Mock()
        observer = BoundedLogObserver(client=backend, environment='pre', environment_binding_confirmed=True)
        for trace, parent in (('0'*32, '1'*16), ('1'*32, '0'*16)):
            with self.assertRaises(LogObservationError):
                observer.observe(LogQuery('pre', traceparent=f'00-{trace}-{parent}-01', start_epoch=10, end_epoch=20))
        backend.query.assert_not_called()
        valid = f"00-{'1'*32}-{'2'*16}-01"
        backend.query.return_value = {'success': True, 'truncated': False,
            'records': [{'environment': 'pre', 'timestamp': 15, 'traceparent': f"00-{'1'*32}-{'0'*16}-01"}]}
        with self.assertRaises(LogObservationError):
            observer.observe(LogQuery('pre', traceparent=valid, start_epoch=10, end_epoch=20))
        backend.query.assert_called_once()

    def test_disabled_binding_does_not_send_or_claim_traceparent(self):
        p = profile()
        with patch.dict(os.environ, {"BCS_E2E_COOKIE": "cookie"}), patch("bcs_e2e.write_transport.build_opener") as factory:
            factory.return_value.open.return_value = Reply({"code": 20000, "message": "OK", "request_id": "r", "data": {"group_id": "g"}})
            _, record = WriteClient(p).call("group.read", group_id="g")
            request = factory.return_value.open.call_args.args[0]
        self.assertNotIn("traceparent", request.headers)
        self.assertEqual(record["traceparent_mode"], "disabled")
        self.assertNotIn("submitted_traceparent", record)

    def test_w3c_binding_records_submitted_and_effective_traceparent(self):
        p = profile()
        p["route_bindings"]["group.read"]["traceparent"] = "w3c"
        response_trace = "00-" + "1" * 32 + "-" + "2" * 16 + "-01"
        reply = Reply({"code": 20000, "message": "OK", "request_id": "r", "data": {"group_id": "g"}},
                      headers={"traceparent": response_trace})
        with patch.dict(os.environ, {"BCS_E2E_COOKIE": "cookie"}), patch("bcs_e2e.write_transport.build_opener") as factory:
            factory.return_value.open.return_value = reply
            _, record = WriteClient(p).call("group.read", group_id="g")
            request = factory.return_value.open.call_args.args[0]
        self.assertTrue(valid_traceparent(request.headers["Traceparent"]))
        self.assertEqual(record["submitted_traceparent"], request.headers["Traceparent"])
        self.assertEqual(record["effective_traceparent"], response_trace)

    def test_invalid_effective_traceparent_fails_closed(self):
        p = profile(); p["route_bindings"]["group.read"]["traceparent"] = "w3c"
        reply = Reply({"code": 20000, "message": "OK", "request_id": "r", "data": {}},
                      headers={"traceparent": "not-a-trace"})
        with patch.dict(os.environ, {"BCS_E2E_COOKIE": "cookie"}), patch("bcs_e2e.write_transport.build_opener") as factory:
            factory.return_value.open.return_value = reply
            with self.assertRaises(WriteTransportError):
                WriteClient(p).call("group.read", group_id="g")

    def test_route_binding_rejects_unknown_trace_mode(self):
        p = profile(); p["route_bindings"]["group.read"]["traceparent"] = "always"
        with self.assertRaises(ConfigError):
            validate(p)


if __name__ == "__main__":
    unittest.main()
