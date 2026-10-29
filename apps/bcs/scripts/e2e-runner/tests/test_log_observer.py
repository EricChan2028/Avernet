from __future__ import annotations

from pathlib import Path
import sys
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bcs_e2e.log_observer import BoundedLogObserver, LogObservationError, LogQuery


class LogObserverTests(unittest.TestCase):
    def query(self, **kwargs):
        value = {"success": True, "records": [{"environment": "pre", "timestamp": 15, "request_id": "r-1", "token": "secret",
                                                   "message": "private user message"}], "truncated": False}
        value.update(kwargs)
        client = Mock()
        client.query.return_value = value
        return client

    def test_exact_environment_and_request_selector_are_required(self):
        client = self.query()
        observer = BoundedLogObserver(client=client, environment="pre",
                                       environment_binding_confirmed=True)
        result = observer.observe(LogQuery("pre", request_id="r-1", start_epoch=10, end_epoch=20))
        self.assertEqual(result["status"], "OBSERVED")
        self.assertEqual(result["record_count"], 1)
        self.assertNotIn("secret", str(result))
        self.assertNotIn("private user message", str(result))
        client.query.assert_called_once()
        for query in (
            LogQuery("prod", request_id="r-1", start_epoch=10, end_epoch=20),
            LogQuery("pre", start_epoch=10, end_epoch=20),
            LogQuery("pre", request_id="r-1", traceparent="00-" + "a" * 32 + "-" + "b" * 16 + "-01",
                     start_epoch=10, end_epoch=20),
        ):
            with self.subTest(query=query), self.assertRaises(LogObservationError):
                observer.observe(query)
        self.assertEqual(client.query.call_count, 1)

    def test_returned_records_must_match_environment_request_and_window(self):
        valid = {"environment": "pre", "timestamp": 15, "request_id": "r-1"}
        for field, invalid in (("environment", "prod"), ("request_id", "other"),
                               ("timestamp", 21), ("timestamp", True)):
            with self.subTest(field=field):
                observer = BoundedLogObserver(client=self.query(records=[{**valid, field: invalid}]),
                    environment="pre", environment_binding_confirmed=True)
                with self.assertRaises(LogObservationError):
                    observer.observe(LogQuery("pre", request_id="r-1", start_epoch=10, end_epoch=20))

    def test_empty_logs_are_incomplete_not_evidence_of_success(self):
        client = self.query(records=[])
        observer = BoundedLogObserver(client=client, environment="pre", environment_binding_confirmed=True)
        query = LogQuery("pre", request_id="r-1", start_epoch=10, end_epoch=20)
        result = observer.observe(query, timeout_seconds=1.5)
        self.assertEqual(result["status"], "INCOMPLETE")
        client.query.assert_called_once_with(query, timeout_seconds=1.5)

    def test_trace_query_accepts_same_trace_different_span_not_other_trace(self):
        submitted = "00-" + "a" * 32 + "-" + "b" * 16 + "-01"
        effective = "00-" + "a" * 32 + "-" + "c" * 16 + "-01"
        client = self.query(records=[{"environment": "pre", "timestamp": 15, "traceparent": effective}])
        observer = BoundedLogObserver(client=client, environment="pre", environment_binding_confirmed=True)
        query = LogQuery("pre", traceparent=submitted, start_epoch=10, end_epoch=20)
        self.assertEqual(observer.observe(query)["status"], "OBSERVED")
        client.query.return_value["records"][0]["traceparent"] = effective.replace("a", "d")
        with self.assertRaises(LogObservationError):
            observer.observe(query)

    def test_window_limit_and_traceparent_are_bounded(self):
        observer = BoundedLogObserver(client=self.query(), environment="pre",
                                       environment_binding_confirmed=True, max_window_seconds=60)
        valid = "00-" + "a" * 32 + "-" + "b" * 16 + "-01"
        for query in (
            LogQuery("pre", traceparent=valid, start_epoch=100, end_epoch=161),
            LogQuery("pre", request_id="r-1", start_epoch=100, end_epoch=101, limit=101),
            LogQuery("pre", request_id="r-1", start_epoch=100, end_epoch=99),
            LogQuery("pre", request_id="r-1", start_epoch=100, end_epoch=200),
        ):
            with self.subTest(query=query), self.assertRaises(LogObservationError):
                observer.observe(query)

    def test_required_binding_and_tool_errors_fail_closed(self):
        with self.assertRaises(LogObservationError):
            BoundedLogObserver(client=self.query(), environment="pre",
                               environment_binding_confirmed=False).admit()
        client = self.query(success=False, records=[])
        observer = BoundedLogObserver(client=client, environment="pre",
                                       environment_binding_confirmed=True)
        with self.assertRaises(LogObservationError):
            observer.observe(LogQuery("pre", request_id="r-1", start_epoch=1, end_epoch=2))
        client = self.query(records=[], truncated=True)
        result = BoundedLogObserver(client=client, environment="pre",
                                    environment_binding_confirmed=True).observe(
                                        LogQuery("pre", request_id="r-1", start_epoch=1, end_epoch=2))
        self.assertEqual(result["status"], "INCOMPLETE")
        self.assertTrue(result["truncated"])


if __name__ == "__main__":
    unittest.main()
