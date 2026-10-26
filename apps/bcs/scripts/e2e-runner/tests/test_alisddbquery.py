from __future__ import annotations

from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bcs_e2e.adapters import RemotePilotAdapter
from bcs_e2e.alisddbquery import AlisdDbQueryObserver
from bcs_e2e.evidence import ASSERTIONS, EvidenceError
from bcs_e2e.profile import load, validate


class FakeClient:
    def __init__(self):
        self.calls = []
        self.tables = sorted({value[0] for value in ASSERTIONS.values()})

    def db_list(self):
        self.calls.append("db_list")
        return {"success": True, "data": ["agentclawdb"]}

    def table_list(self, database):
        self.calls.append(("table_list", database))
        return {"success": True, "data": self.tables}

    def table_describe(self, database, table):
        self.calls.append(("table_describe", database, table))
        fields = ASSERTIONS[next(key for key, value in ASSERTIONS.items() if value[0] == table)][1]
        return {"success": True, "columns": list(fields)}

    def db_query(self, plan):
        self.calls.append(("db_query", plan.public()))
        field, value = next((name, value) for name, value in plan.filters.items() if name != "env")
        row = {name: "fixture-value" for name in plan.select}
        row.update({field: value, "env": plan.filters["env"]})
        return {"success": True, "data": [row]}


class AlisdDbQueryTests(unittest.TestCase):
    @staticmethod
    def binding(database, environment):
        return {"status": "BOUND", "database": database, "environment": environment}

    def test_discovery_and_provider_share_bounded_semantic_contract(self):
        client = FakeClient()
        observer = AlisdDbQueryObserver(client=client, database="agentclawdb", environment="pre",
                                         environment_binding_confirmed=True, physical_binding_probe=self.binding)
        result = observer.discover()
        self.assertEqual(result["status"], "DISCOVERY_PASSED")
        observed = observer.provider().observe("group.persisted", "g-1")
        self.assertEqual(observed["source"], "alisddbquery")
        self.assertEqual(observed["plan"]["filters"], {"env": "pre", "group_id": "g-1"})
        self.assertEqual(client.calls.count("db_list"), 1)
        self.assertEqual(len([call for call in client.calls if isinstance(call, tuple) and call[0] == "db_query"]), 1)



    def test_remote_adapter_uses_only_an_explicitly_injected_observer_client(self):
        profile = load(Path(__file__).resolve().parents[1] / "profiles/preprod.example.json")
        profile["environment"].update(id="pre-test", expected_bcs_env="pre")
        profile["human"]["user_id"] = "owner-1"
        for role, bot in (("driver", "bot-a"), ("member", "bot-b")):
            profile["actors"][role].update(bot_id=bot, owner_id="owner-1")
        profile["observer"].update(server="alisddbquery", database="agentclawdb",
                                    environment_binding_confirmed=True)
        profile["policy"]["exclusive_use_confirmed"] = True
        profile = validate(profile)
        client = FakeClient()
        with patch("bcs_e2e.adapters.ReadClient"), \
             patch("bcs_e2e.adapters.WriteClient"), \
             patch("bcs_e2e.adapters.SessionWebSocketClient"), \
             patch("bcs_e2e.adapters.RemoteFileClient"):
            adapter = RemotePilotAdapter(profile, observer_client=client,
                                          physical_binding_probe=self.binding)
        observer = adapter.semantic_observer()
        self.assertIsInstance(observer, AlisdDbQueryObserver)
        self.assertEqual(observer.admit(("group.persisted",))["status"], "READY")
        self.assertEqual(client.calls.count("db_list"), 1)

    def test_remote_discovery_requires_independent_physical_binding_probe(self):
        client = FakeClient()
        observer = AlisdDbQueryObserver(client=client, database="agentclawdb", environment="pre",
                                         environment_binding_confirmed=True)
        with self.assertRaises(EvidenceError):
            observer.discover()
        self.assertEqual(client.calls, [])
        mismatch = AlisdDbQueryObserver(
            client=client, database="agentclawdb", environment="pre",
            environment_binding_confirmed=True,
            physical_binding_probe=lambda database, environment: {
                "status": "BOUND", "database": database, "environment": "prod"})
        with self.assertRaises(EvidenceError):
            mismatch.discover()
        self.assertEqual(client.calls, [])

    def test_unconfirmed_binding_blocks_before_client_calls(self):
        client = FakeClient()
        observer = AlisdDbQueryObserver(client=client, database="agentclawdb", environment="pre",
                                         environment_binding_confirmed=False)
        with self.assertRaises(EvidenceError):
            observer.discover()
        self.assertEqual(client.calls, [])

    def test_missing_required_table_blocks_discovery(self):
        client = FakeClient()
        client.tables = ["bcs_groups"]
        observer = AlisdDbQueryObserver(client=client, database="agentclawdb", environment="pre",
                                         environment_binding_confirmed=True, physical_binding_probe=self.binding)
        with self.assertRaises(EvidenceError):
            observer.discover()

    def test_discovery_rejects_invalid_or_failed_normalized_responses(self):
        for method in ("db_list", "table_list"):
            for response in (None, {}, {"success": False, "data": []},
                             {"success": True, "data": "agentclawdb"},
                             {"success": True, "data": ["agentclawdb", {}]}):
                with self.subTest(method=method, response=response):
                    client = FakeClient()
                    observer = AlisdDbQueryObserver(
                        client=client, database="agentclawdb", environment="pre",
                        environment_binding_confirmed=True, physical_binding_probe=self.binding)
                    with patch.object(client, method, return_value=response):
                        with self.assertRaises(EvidenceError):
                            observer.provider()
                    self.assertFalse(any(isinstance(call, tuple) and call[0] == "db_query"
                                         for call in client.calls))

    def test_failed_rediscovery_invalidates_cached_discovery(self):
        client = FakeClient()
        observer = AlisdDbQueryObserver(
            client=client, database="agentclawdb", environment="pre",
            environment_binding_confirmed=True, physical_binding_probe=self.binding)
        observer.discover()
        client.tables = []
        with self.assertRaises(EvidenceError):
            observer.discover()
        with self.assertRaises(EvidenceError):
            observer.provider()


    def test_normalized_discovery_rejects_incomplete_metadata_before_row_access(self):
        for method in ("db_list", "table_list"):
            for flag in (True, 1, 0, "true", "false", None, [], {}):
                with self.subTest(method=method, flag=flag):
                    client = FakeClient()
                    original = getattr(client, method)
                    def incomplete(*args):
                        return {**original(*args), "truncated": flag}
                    setattr(client, method, incomplete)
                    observer = AlisdDbQueryObserver(client=client, database="agentclawdb", environment="pre",
                        environment_binding_confirmed=True, physical_binding_probe=self.binding)
                    with self.assertRaises(EvidenceError): observer.admit(("group.persisted",))
                    self.assertIsNone(observer._discovery)
                    self.assertFalse(any(isinstance(call, tuple) and call[0] in ("db_query", "table_describe")
                                         for call in client.calls))
                    self.assertEqual(len(client.calls), 1 if method == "db_list" else 2)

    def test_explicit_complete_normalized_metadata_remains_supported(self):
        client = FakeClient()
        for method in ("db_list", "table_list", "table_describe"):
            original = getattr(client, method)
            def complete(*args, original=original):
                return {**original(*args), "truncated": False}
            setattr(client, method, complete)
        observer = AlisdDbQueryObserver(client=client, database="agentclawdb", environment="pre",
            environment_binding_confirmed=True, physical_binding_probe=self.binding)
        self.assertEqual(observer.admit(("group.persisted",))["status"], "READY")


if __name__ == "__main__":
    unittest.main()
