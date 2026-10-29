from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bcs_e2e.alisddbquery import AlisdDbQueryObserver
from bcs_e2e.cli import main
from bcs_e2e.evidence import ASSERTIONS
from bcs_e2e.pilot import PilotError, run_case, run_pre01
from bcs_e2e.profile import load


ROOT = Path(__file__).resolve().parents[1]


class FakeRemoteObserverClient:
    def __init__(self):
        self.groups = {}
        self.deleted = set()
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
        group_id = plan.filters["group_id"]
        value = self.groups.get(group_id)
        if value is None or group_id in self.deleted:
            return {"success": True, "data": []}
        row = {
            "group_id": group_id,
            "env": "pre",
            "driver_bot": value["driver_bot_uuid"],
            "originator": "human_owner-1",
            "status": value["status"],
        }
        return {"success": True, "data": [row]}


class FakeRemotePilotAdapter:
    termination_mode = "LOCAL_CONTROLLED"

    def __init__(self, profile, observer_client, physical_binding_probe):
        self.profile = profile
        self.client = observer_client
        self.physical_binding_probe = physical_binding_probe
        self.groups = observer_client.groups
        self.deleted = observer_client.deleted

    def preflight(self):
        return {"status": "DISCOVERY_PASSED", "ready_to_execute": False,
                "checks": [], "requests": []}

    def semantic_observer(self):
        return AlisdDbQueryObserver(
            client=self.client,
            database=self.profile["observer"]["database"],
            environment=self.profile["environment"]["expected_bcs_env"],
            environment_binding_confirmed=True,
            physical_binding_probe=self.physical_binding_probe,
        )

    def _record(self, operation, method, path, status):
        return {"operation": operation, "method": method, "path": path,
                "submitted_request_id": f"fake-remote-{len(self.groups)}-{operation}",
                "request_id_echoed": True, "http_status": status, "elapsed_ms": 0.0,
                "source": "fake-remote-composition"}

    def group_create(self, body):
        group_id = "pre-group-1"
        value = {**body, "group_id": group_id, "status": "active", "initial_session_id": "initial-session", "originator_actor_id": "human_owner-1"}
        self.groups[group_id] = value
        return value, self._record("group.create", "POST", "/groups", 201)

    def session_read(self, session_id):
        return {"session_id": session_id, "group_id": "pre-group-1"}, self._record("session.read", "GET", "/sessions/initial-session", 200)

    def session_delete(self, session_id):
        self.deleted.add(session_id)
        return {"deleted": True}, self._record("session.delete", "DELETE", "/sessions/initial-session", 200)

    def session_verify_deleted(self, session_id):
        if session_id not in self.deleted:
            raise RuntimeError("initial session was not deleted")
        return None, self._record("session.verify_deleted", "GET", "/sessions/initial-session", 404)

    def group_read(self, group_id):
        return self.groups[group_id], self._record("group.read", "GET", f"/groups/{group_id}", 200)

    def group_update(self, group_id, body):
        self.groups[group_id].update(body)
        return self.groups[group_id], self._record("group.update", "PATCH", f"/groups/{group_id}", 200)

    def group_delete(self, group_id):
        self.deleted.add(group_id)
        return {"group_id": group_id, "deleted": True}, self._record(
            "group.delete", "DELETE", f"/groups/{group_id}", 200)

    def group_verify_deleted(self, group_id):
        if group_id not in self.deleted:
            raise RuntimeError("fake remote group was not deleted")
        return None, self._record("group.verify_deleted", "GET", f"/groups/{group_id}", 404)


class RemotePilotCompositionTests(unittest.TestCase):
    def profile(self):
        value = load(ROOT / "profiles/preprod.example.json")
        value["environment"].update(id="pre-test", expected_bcs_env="pre")
        value["human"]["user_id"] = "owner-1"
        for role, bot in (("driver", "bot-a"), ("member", "bot-b")):
            value["actors"][role].update(bot_id=bot, owner_id="owner-1")
        value["observer"].update(server="alisddbquery", database="agentclawdb",
                                  environment_binding_confirmed=True)
        value["policy"].update(fixture_pool="remote-composition-pool", exclusive_use_confirmed=True)
        return value

    def test_run_case_dispatches_shared_story_and_rejects_unknown_case(self):
        profile = self.profile()
        with self.assertRaises(PilotError):
            run_case(profile, Path("/tmp"), "run", "UNKNOWN")

    def test_same_pre01_story_can_use_explicit_remote_observer_composition(self):
        profile = self.profile()
        client = FakeRemoteObserverClient()
        with tempfile.TemporaryDirectory(dir="/tmp") as tmp:
            tmp = Path(tmp)
            profile_path = tmp / "profile.json"
            profile_path.write_text(json.dumps(profile))
            state = tmp / "state"
            run_id = "remote-composition-run"
            with patch("bcs_e2e.pool.pool_root", return_value=tmp / "pools"):
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(main(["prepare", "--profile", str(profile_path),
                                           "--run-id", run_id, "--state-dir", str(state)]), 0)
                created = {}
                def factory(current, *, state_dir, case_id, observer_client, physical_binding_probe):
                    created["client"] = observer_client
                    created["case_id"] = case_id
                    return FakeRemotePilotAdapter(current, observer_client, physical_binding_probe)
                def binding(database, environment):
                    return {"status": "BOUND", "database": database, "environment": environment}
                report = run_pre01(profile, state, run_id, observer_client=client,
                                   physical_binding_probe=binding, adapter_factory=factory)
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["evidence_status"], "PASS")
        self.assertEqual(report["cleanup"], "PASS")
        self.assertEqual(created["client"], client)
        self.assertEqual(created["case_id"], "PRE-01")
        self.assertIn("db_list", client.calls)
        self.assertTrue(any(call[0] == "db_query" for call in client.calls if isinstance(call, tuple)))


if __name__ == "__main__":
    unittest.main()
