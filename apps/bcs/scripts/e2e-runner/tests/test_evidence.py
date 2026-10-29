from __future__ import annotations

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bcs_e2e.evidence import AlisdDbEvidenceProvider, EvidenceError, LocalSemanticEvidenceProvider


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.calls = []

    def provider(self, *, confirmed=True, columns=None, query_result=None):
        columns = columns or ["group_id", "env", "driver_bot", "originator", "status"]
        def describe(db, table):
            self.calls.append(("describe", db, table))
            return {"success": True, "columns": columns}
        def query(plan):
            self.calls.append(("query", plan.public()))
            return query_result or {"success": True, "data": [{"group_id": "g-1", "env": "pre", "driver_bot": "bot-a", "originator": "owner-1", "status": "active"}]}
        return AlisdDbEvidenceProvider(database="agentclawdb", environment="pre",
                                       environment_binding_confirmed=confirmed,
                                       describe=describe, query=query)

    def test_group_plan_is_exact_and_small(self):
        result = self.provider().observe("group.persisted", "g-1")
        self.assertEqual(result["status"], "OBSERVED")
        plan = result["plan"]
        self.assertEqual(plan["filters"], {"env": "pre", "group_id": "g-1"})
        self.assertNotIn("token", plan["select"])
        self.assertEqual(plan["limit"], 2)
        self.assertEqual([call[0] for call in self.calls], ["describe", "query"])

    def test_unknown_assertion_and_unbound_id_are_refused(self):
        with self.assertRaises(EvidenceError):
            self.provider().make_plan("bcs_groups", "g-1")
        with self.assertRaises(EvidenceError):
            self.provider().make_plan("group.persisted", "g/other")

    def test_unconfirmed_environment_blocks_before_schema_query(self):
        provider = self.provider(confirmed=False)
        with self.assertRaises(EvidenceError):
            provider.observe("group.persisted", "g-1")
        self.assertEqual(self.calls, [])

    def test_schema_drift_blocks(self):
        provider = self.provider(columns=["group_id", "env"])
        with self.assertRaises(EvidenceError):
            provider.observe("group.persisted", "g-1")
        self.assertEqual(len(self.calls), 1)

    def test_query_error_empty_data_and_truncation_are_not_success(self):
        for result in ({"success": False, "message": "denied"},
                       {"success": True, "data": None},
                       {"success": True, "data": [{"id": 1}] * 3},
                       {"success": True, "data": [], "truncated": True}):
            with self.subTest(result=result), self.assertRaises(EvidenceError):
                self.provider(query_result=result).observe("group.persisted", "g-1")


    def test_local_semantic_provider_matches_observer_contract_without_mcp(self):
        calls = []
        provider = LocalSemanticEvidenceProvider(
            database="local", environment="local",
            query=lambda plan: (calls.append(plan.public()) or {
                "success": True,
                "data": [{"group_id": "g-1", "env": "local", "status": "active", "driver_bot": "bot-a", "originator": "owner-1"}],
            }),
        )
        result = provider.observe("group.persisted", "g-1")
        self.assertEqual(result["status"], "OBSERVED")
        self.assertEqual(result["source"], "local-semantic")
        self.assertEqual(result["plan"]["filters"], {"env": "local", "group_id": "g-1"})
        self.assertEqual(len(calls), 1)

    def test_local_semantic_provider_rejects_cross_environment_rows(self):
        provider = LocalSemanticEvidenceProvider(
            query=lambda _plan: {
                "success": True,
                "data": [{"group_id": "g-1", "env": "pre", "status": "active"}],
            },
        )
        with self.assertRaises(EvidenceError):
            provider.observe("group.persisted", "g-1")

    def test_filter_cannot_be_replaced_by_query_text(self):
        result = self.provider().make_plan("group.persisted", "g-1")
        self.assertIsInstance(result.filters, dict)
        self.assertNotIn("where", result.public())
        self.assertNotIn("sql", result.public())

    def test_admission_checks_schema_without_querying_invented_resources(self):
        result = self.provider().admit(("group.persisted",))
        self.assertEqual(result["status"], "READY")
        self.assertEqual([call[0] for call in self.calls], ["describe"])

    def test_admission_blocks_unconfirmed_environment_and_schema_drift(self):
        with self.assertRaises(EvidenceError):
            self.provider(confirmed=False).admit(("group.persisted",))
        self.assertEqual(self.calls, [])
        with self.assertRaises(EvidenceError):
            self.provider(columns=["group_id"]).admit(("group.persisted",))
        self.assertEqual([call[0] for call in self.calls], ["describe"])

    def test_missing_projection_duplicate_rows_and_extra_columns(self):
        row = {"group_id": "g-1", "env": "pre", "driver_bot": "bot-a",
               "originator": "owner-1", "status": "active"}
        for rows in ([{"group_id": "g-1", "env": "pre"}], [row, row]):
            with self.assertRaises(EvidenceError):
                self.provider(query_result={"success": True, "data": rows}).observe("group.persisted", "g-1")
        result = self.provider(query_result={"success": True, "data": [{**row, "secret": "must-not-leak"}]}).observe("group.persisted", "g-1")
        self.assertEqual(result["rows"], [row])


    def test_row_completeness_flags_are_strict_for_local_and_database_providers(self):
        row = {"group_id": "g-1", "env": "pre", "driver_bot": "bot-a",
               "originator": "owner-1", "status": "active"}
        for flag in (True, 1, 0, "true", "false", None, [], {}):
            for local in (False, True):
                with self.subTest(flag=flag, local=local):
                    payload = {"success": True, "data": [row], "truncated": flag}
                    provider = (LocalSemanticEvidenceProvider(environment="pre", query=lambda _: payload)
                                if local else self.provider(query_result=payload))
                    with self.assertRaises(EvidenceError):
                        provider.observe("group.persisted", "g-1")
        for complete in ({}, {"truncated": False}):
            self.assertEqual(self.provider(query_result={"success": True, "data": [row], **complete})
                             .observe("group.persisted", "g-1")["status"], "OBSERVED")

    def test_incomplete_schema_blocks_admission_and_observation_before_row_reads(self):
        fields = ["group_id", "env", "driver_bot", "originator", "status"]
        for flag in (True, 1, 0, "true", "false", None, [], {}):
            for operation in ("admit", "observe"):
                with self.subTest(flag=flag, operation=operation):
                    calls = []
                    provider = AlisdDbEvidenceProvider(database="testdb", environment="pre",
                        environment_binding_confirmed=True,
                        describe=lambda *_: {"success": True, "columns": fields, "truncated": flag},
                        query=lambda plan: calls.append(plan))
                    with self.assertRaises(EvidenceError):
                        if operation == "admit": provider.admit(("group.persisted",))
                        else: provider.observe("group.persisted", "g-1")
                    self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
