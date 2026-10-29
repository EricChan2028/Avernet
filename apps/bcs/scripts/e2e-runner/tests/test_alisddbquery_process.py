from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bcs_e2e.alisddbquery import AlisdDbQueryError, McporterAlisdDbQueryClient, AlisdDbQueryObserver
from bcs_e2e.evidence import ASSERTIONS, EvidenceError, EvidencePlan


class McporterClientTests(unittest.TestCase):
    def response(self, value, code=0):
        return subprocess.CompletedProcess([], code, json.dumps(value), "private stderr")

    def test_discovery_calls_use_argument_list_without_shell(self):
        runner = Mock(side_effect=[self.response({"success": True, "data": ["agentclawdb"]}),
            self.response({"success": True, "data": [{"Tables_in_agentclawdb": "bcs_groups"}]}),
            self.response({"success": True, "data": [{"Table": "bcs_groups", "Create Table":
                "CREATE TABLE `bcs_groups` (`group_id` varchar(128), `env` varchar(16))"}]})])
        client = McporterAlisdDbQueryClient(server="alisd-db-query", runner=runner)
        client.db_list(); client.table_list("agentclawdb"); client.table_describe("agentclawdb", "bcs_groups")
        self.assertEqual(runner.call_count, 3)
        for call in runner.call_args_list:
            command = call.args[0]
            self.assertIn("--output", command)
            self.assertFalse(call.kwargs["shell"])
            self.assertNotIn(";", " ".join(command))

    def test_wire_metadata_admission_requires_independent_binding_and_never_queries_rows(self):
        tables = {value[0]: value[1] for value in ASSERTIONS.values()}
        def command(args, **kwargs):
            tool = args[2].split(".")[-1]
            if tool == "db_list": return self.response({"success": True, "data": ["testdb"]})
            if tool == "table_list":
                return self.response({"success": True, "data": [{"Tables_in_testdb": t} for t in tables]})
            if tool == "table_describe":
                table = json.loads(args[args.index("--args") + 1])["table"]
                ddl = "CREATE TABLE `" + table + "` (" + ", ".join("`" + f + "` varchar(128)" for f in tables[table]) + ")"
                return self.response({"success": True, "data": [{"Table": table, "Create Table": ddl}]})
            raise AssertionError("metadata admission must not query business rows")
        runner = Mock(side_effect=command)
        client = McporterAlisdDbQueryClient(server="test-observer", runner=runner)
        observer = AlisdDbQueryObserver(client=client, database="testdb", environment="pre",
                                        environment_binding_confirmed=True)
        with self.assertRaises(EvidenceError): observer.admit(tuple(ASSERTIONS))
        runner.assert_not_called()
        observer.physical_binding_probe = lambda database, environment: {
            "status": "BOUND", "database": database, "environment": environment}
        admitted = observer.admit(tuple(ASSERTIONS))
        self.assertEqual(admitted["status"], "READY")
        self.assertEqual(runner.call_count, 5)
        self.assertFalse(any("db_query" in call.args[0][2] for call in runner.call_args_list))

    def test_wire_schema_errors_remain_sanitized_client_errors(self):
        runner = Mock(return_value=self.response({"success": False, "message": "private-backend-error", "data": []}))
        client = McporterAlisdDbQueryClient(server="test-observer", runner=runner)
        for operation in (client.db_list, lambda: client.table_list("testdb"), lambda: client.table_describe("testdb", "bcs_groups")):
            with self.assertRaises(AlisdDbQueryError) as error: operation()
            self.assertNotIn("private-backend-error", str(error.exception))

    def test_db_query_is_bounded_and_generated_from_fixed_plan(self):
        runner = Mock(return_value=self.response({"success": True, "data": []}))
        client = McporterAlisdDbQueryClient(server="alisd-db-query", runner=runner)
        plan = EvidencePlan("group.persisted", "agentclawdb", "bcs_groups",
                            ("group_id", "env"), {"env": "pre", "group_id": "g-1"}, limit=2)
        client.db_query(plan)
        command = runner.call_args.args[0]
        args = json.loads(command[command.index("--args") + 1])
        self.assertEqual(args["db"], "agentclawdb")
        self.assertEqual(args["where"], "env = 'pre' AND group_id = 'g-1'")
        self.assertEqual(args["limit"], "2")
        self.assertNotIn("sql", args)

    def test_nonzero_invalid_oversized_and_malformed_responses_fail_without_leaking_stderr(self):
        cases = (
            self.response({}, code=1),
            subprocess.CompletedProcess([], 0, "x" * 2_000_000, "secret"),
            self.response([], code=0),
            subprocess.CompletedProcess([], 0, "not-json", "secret"),
        )
        for result in cases:
            with self.subTest(result=result):
                runner = Mock(return_value=result)
                client = McporterAlisdDbQueryClient(server="alisd-db-query", runner=runner,
                                                     max_output_bytes=1_000_000)
                with self.assertRaises(AlisdDbQueryError) as error:
                    client.db_list()
                self.assertNotIn("secret", str(error.exception))

    def test_ambiguous_json_is_rejected_before_metadata_normalization(self):
        for raw in ('{"success":false,"success":true,"data":["testdb"]}',
                    '{"success":true,"data":[],"data":["testdb"]}',
                    '{"success":true,"data":[{"Table":"wrong","Table":"bcs_groups"}]}',
                    '{"success":true,"data":[]} {}'):
            client = McporterAlisdDbQueryClient(server='test-observer', runner=Mock(
                return_value=subprocess.CompletedProcess([], 0, raw, 'private-stderr')))
            with self.assertRaises(AlisdDbQueryError) as caught:
                client.db_list()
            self.assertNotIn('private', str(caught.exception))

    def test_shell_like_identifiers_and_invalid_tool_binding_are_rejected(self):
        runner = Mock(return_value=self.response({"success": True, "data": []}))
        client = McporterAlisdDbQueryClient(server="alisd-db-query", runner=runner)
        with self.assertRaises(AlisdDbQueryError):
            client.table_list("db;drop")
        with self.assertRaises(AlisdDbQueryError):
            McporterAlisdDbQueryClient(server="server;drop", runner=runner)
        with self.assertRaises(AlisdDbQueryError):
            McporterAlisdDbQueryClient(server="server", executable=())

    def test_db_query_rejects_unapproved_projection_and_filter_values(self):
        runner = Mock(return_value=self.response({"success": True, "data": []}))
        client = McporterAlisdDbQueryClient(server="alisd-db-query", runner=runner)
        bad_filter = EvidencePlan("group.persisted", "agentclawdb", "bcs_groups",
                                  ("group_id", "env"),
                                  {"env": "pre;drop", "group_id": "g-1"})
        with self.assertRaises(AlisdDbQueryError):
            client.db_query(bad_filter)
        bad_projection = EvidencePlan("group.persisted", "agentclawdb", "bcs_groups",
                                      ("message",), {"env": "pre", "group_id": "g-1"})
        with self.assertRaises(AlisdDbQueryError):
            client.db_query(bad_projection)


if __name__ == "__main__":
    unittest.main()
