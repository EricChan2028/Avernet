"""Contract, adapter and command tests for the dual Provider publish case."""

from __future__ import annotations

from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
TEST_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(TEST_ROOT))
sys.path.insert(0, str(ROOT))
from bcs_e2e import provider_publishes_case_run as provider_case_run
from bcs_e2e.cli import main
from bcs_e2e.migration_inventory import migration_plan
from bcs_e2e.profile import ConfigError
from bcs_e2e.provider_publishes_case_run import (
    dual_provider_publishes_case_run,
    execute_provider_publishes_case,
    provider_publishes_credential_kind,
)
from bcs_e2e.provider_publishes_contract import (
    CASE_ID,
    DISCOVERY_REQUIREMENTS,
    PARENT,
    REQUIREMENTS,
    STEP_BY_OPERATION,
    STEP_ROWS,
    provider_publishes_source_binding,
)
from bcs_e2e.provider_publishes_profile import validate_provider_publishes_profile
from bcs_e2e.run_state import read

RUN_ID = "provider-publishes-run-1"
CASE_KEY = "provider-publishes"
HUMAN = "39464"
PREPROD_GATEWAY = "https://bcs-pre.alipay.com"
PREPROD_BROWSER_ORIGIN = "https://teamclaw-pre.alipay.com"
LOCAL_ORIGIN = "http://127.0.0.1:21001"
COOKIE_ENV = "BCS_E2E_HUMAN_COOKIE"
COOKIE_SECRET = "synthetic-human-cookie-secret"
ADMIN_TOKEN = "synthetic-provider-admin-token"
CALLBACK_TOKEN = "synthetic-bcs-callback-token"
RUNTIME_TOKEN = "synthetic-provider-runtime-token"


def provider_profile(*, preprod: bool = False) -> dict:
    tier = "preprod" if preprod else "local"
    value = {
        "schema_version": 1,
        "contract": "dual-provider-publishes-profile-v1",
        "environment": {
            "tier": tier,
            "id": "provider-publishes-preprod" if preprod else "provider-publishes-local",
            "expected_bcs_env": "pre" if preprod else "local",
        },
        "entrypoint": {
            "kind": "gateway" if preprod else "singlebox",
            "origin": PREPROD_GATEWAY if preprod else LOCAL_ORIGIN,
            "api_face": "legacy",
            "manifest_face": "legacy",
        },
        "human": ({"user_id": HUMAN} if preprod else
                  {"user_id": "001", "mock_nick_name": "admin"}),
        "auth": ({"kind": "preprod-human-cookie", "cookie_env": COOKIE_ENV}
                 if preprod else {"kind": "local-mock-human"}),
        "fixture": {
            "webhook_url": "https://provider.example.com/bcs/webhook",
            "takeover_bot_id": "qa-bot-fixture",
        },
        "policy": {
            "fixture_pool": "dual-provider-publishes-admin-http",
            "exclusive_use_confirmed": True,
            "timeout_seconds": 10,
            "max_response_bytes": 65536,
            "failure_retention_hours": 24,
        },
        "cli": {
            "transport": "product-http",
            "command_family": "provider-publishes",
            "api_face": "legacy",
        },
        "route_bindings": {
            row["operation"]: {
                "method": row["method"],
                "path": row["path"],
                "api_face": "legacy",
                "auth": row["caller"],
                "response": "legacy-json",
                "traceparent": "disabled",
            }
            for row in STEP_ROWS
        },
    }
    if preprod:
        value["browser_origin"] = PREPROD_BROWSER_ORIGIN
        value["credential_files"] = {
            COOKIE_ENV: "/Users/example/.codex/bcs-e2e/preprod-human-cookie.txt"
        }
    return value


class FakeTransport:
    """Scripted product responses; never opens a socket."""

    profile = None
    calls = []

    def __init__(self, profile, *, human_headers):
        self.profile = profile
        self.human_headers = human_headers
        self.organization_code = "e2e-org-fixture"
        type(self).profile = profile
        type(self).calls.append({"human_headers": dict(human_headers)})

    def call(self, operation, *, token, path_params, body, provider_header_id):
        row = STEP_BY_OPERATION[operation]
        type(self).calls.append({
            "operation": operation,
            "token": token,
            "path_params": dict(path_params),
            "body": body,
            "provider_header_id": provider_header_id,
        })
        provider_id = "provider-fixture-1"
        bot_uuid = "provider-bot-fixture-1"
        org = path_params.get("organization_code", self.organization_code)
        if operation == "provider.register":
            data = {
                "provider_id": provider_id,
                "provider_admin_token": ADMIN_TOKEN,
                "bcs_to_provider_token": CALLBACK_TOKEN,
            }
        elif operation == "provider.discovery_public_slug":
            data = {
                "auth_mode": "static_bearer",
                "created_at": 1,
                "enabled": True,
                "name": "E2E Provider",
                "protocol_version": "2.0",
                "provider_id": provider_id,
                "slug": path_params["provider_slug"],
                "updated_at": 1,
            }
        elif operation == "provider.discovery_collaboration_slug":
            data = {
                "auth_mode": "static_bearer",
                "created_at": 1,
                "enabled": True,
                "name": "E2E Provider",
                "protocol_version": "2.0",
                "provider_id": provider_id,
                "slug": path_params["provider_slug"],
                "updated_at": 1,
            }
        elif operation == "provider.read":
            data = {"provider_id": provider_id, "auth_mode": "static_bearer", "disabled": False}
        elif operation == "provider.update":
            data = {"provider_id": provider_id, "name": "E2E Provider Updated"}
        elif operation == "provider.discovery_new_slug_after_rename":
            data = {
                "provider_id": provider_id,
                "name": "E2E Provider Updated",
            }
        elif operation == "provider.bot_publish":
            data = {
                "bot_uuid": bot_uuid,
                "provider_bot_ref": body["provider_bot_ref"],
                "bot_runtime_token": RUNTIME_TOKEN,
            }
        elif operation == "provider.bot_update":
            data = {
                "bot_uuid": bot_uuid,
                "provider_bot_ref": path_params.get("provider_bot_ref", "reviewer-fixture"),
                "name": "Provider Review Agent (Senior)",
                "domains": ["release", "audit"],
                "scopes": [],
            }
        elif operation == "provider.org_create":
            self.organization_code = body["organization_code"]
            data = {"organization_code": body["organization_code"]}
        elif operation == "provider.org_list":
            data = {"organizations": [{"organization_code": org}]}
        elif operation == "provider.org_read":
            data = {"organization_code": org, "name": "E2E release organization"}
        elif operation == "provider.org_update":
            data = {"organization_code": org,
                    "name": "E2E release organization updated"}
        elif operation in {"provider.candidate_list_before", "provider.candidate_list_after"}:
            data = {"bots": [{"bot_uuid": bot_uuid}] if operation.endswith("before") else []}
        elif operation in {"provider.candidate_before", "provider.candidate_after"}:
            data = {"bot_uuid": bot_uuid, "is_member": operation.endswith("after")}
        elif operation == "provider.member_add":
            data = {"bot_uuid": bot_uuid, "role": "reviewer"}
        elif operation == "provider.member_profile":
            data = {"profile": {"name": "E2E organization bot"}}
        elif operation == "provider.member_read":
            data = {"bot_uuid": bot_uuid}
        elif operation == "provider.member_list":
            data = {"members": [{"bot_uuid": bot_uuid}]}
        elif operation == "provider.admin_start":
            data = {
                "code": 20000,
                "message": "accepted",
                "data": {
                    "run_id": "admin-run-fixture-1",
                    "organization_code": org,
                    "target_bot_uuid": bot_uuid,
                    "status": "delivery_failed",
                },
            }
        elif operation == "provider.admin_read":
            data = {
                "data": {
                    "run_id": "admin-run-fixture-1",
                    "target_bot_uuid": bot_uuid,
                    "status": "delivery_failed",
                }
            }
        elif operation in {"provider.bot_list", "provider.bot_list_empty"}:
            data = {"items": [{"bot_uuid": bot_uuid}] if operation == "provider.bot_list" else []}
        elif operation == "provider.roster_denied":
            data = {"error": "provider is not allowed to access the task-mode roster"}
        elif operation == "provider.agentpass_negative":
            data = {"agent_code": None, "provider_bot_binding": None}
        elif operation == "provider.unknown_run_callback":
            data = {"error": "run_not_found"}
        elif operation == "provider.takeover_denied":
            data = {"error": "provider is not allowed to switch bot delivery"}
        elif operation == "provider.disable":
            data = {"disabled": True}
        elif operation == "provider.discovery_disabled_slug":
            data = {"provider_id": provider_id, "enabled": False}
        elif operation == "provider.enable":
            data = {"disabled": False}
        elif operation == "provider.discovery_enabled_slug":
            data = {"provider_id": provider_id, "enabled": True}
        elif operation == "provider.bot_retire":
            data = {"deleted": True,
                    "provider_bot_ref": path_params.get("provider_bot_ref", "reviewer-fixture")}
        elif operation == "provider.member_remove":
            data = None
        else:
            data = {}
        record = {
            "operation": operation,
            "method": row["method"],
            "path": row["path"],
            "http_status": row["expected_status"],
            "caller_role": row["caller"],
            "expected_status": row["expected_status"],
            "authorization_sent": token is not None,
            "credentials_sent": "Cookie" in self.human_headers,
            "mock_human_sent": "X-Mock-User-Id" in self.human_headers,
            "provider_header_sent": provider_header_id is not None,
            "traceparent_mode": "disabled",
        }
        if token is not None:
            import hashlib
            record["authorization_sha256"] = hashlib.sha256(token.encode()).hexdigest()
        return json.loads(json.dumps(data)), record


class ProviderPublishesTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.root, ignore_errors=True))
        FakeTransport.calls = []

    def test_source_binding_keeps_32_core_and_10_discovery_obligations(self):
        binding = provider_publishes_source_binding()
        self.assertEqual(len(binding["requirements"]), 42)
        self.assertEqual(len(REQUIREMENTS), 42)
        self.assertEqual(len(DISCOVERY_REQUIREMENTS), 10)
        self.assertEqual(len(set(REQUIREMENTS)), 42)
        self.assertEqual(binding["parent"], PARENT)
        self.assertEqual(
            [row["id"] for row in binding["requirements"]], list(REQUIREMENTS)
        )

    def test_local_and_preprod_profiles_validate_all_42_routes(self):
        local = validate_provider_publishes_profile(provider_profile())
        preprod = validate_provider_publishes_profile(provider_profile(preprod=True))
        self.assertEqual(len(local["route_bindings"]), 42)
        self.assertEqual(len(preprod["route_bindings"]), 42)
        self.assertEqual(
            provider_publishes_credential_kind(local),
            "local_mock_human_with_minted_provider_credentials",
        )
        self.assertEqual(
            provider_publishes_credential_kind(preprod),
            "preprod_human_cookie_with_minted_provider_credentials",
        )

    def test_route_drift_is_rejected(self):
        profile = provider_profile()
        profile["route_bindings"]["provider.register"]["path"] = "/other"
        with self.assertRaisesRegex(ConfigError, "differs from the reviewed contract"):
            validate_provider_publishes_profile(profile)

    def test_local_adapter_executes_all_42_obligations(self):
        with patch.object(provider_case_run, "ProviderPublishesTransport", FakeTransport):
            adapter = provider_case_run.provider_publishes_adapter(
                validate_provider_publishes_profile(provider_profile()), run_id=RUN_ID
            )
            execution = execute_provider_publishes_case(adapter)
        self.assertEqual(execution.status, "PASS")
        self.assertEqual(execution.completed_count, 42)
        self.assertEqual(execution.total_count, 42)
        self.assertEqual(execution.case_id, CASE_ID)
        self.assertEqual(len(FakeTransport.calls), 43)  # constructor + 42 operations
        calls = FakeTransport.calls[1:]
        self.assertEqual([call["operation"] for call in calls],
                         [row["operation"] for row in STEP_ROWS])
        for call, row in zip(calls, STEP_ROWS):
            self.assertEqual(call["provider_header_id"] is not None,
                             row["provider_header"])
        self.assertEqual(set(adapter._credentials), set())
        raw = json.dumps(execution.snapshot(), sort_keys=True)
        for secret in (ADMIN_TOKEN, CALLBACK_TOKEN, RUNTIME_TOKEN):
            self.assertNotIn(secret, raw)

    def test_preprod_adapter_uses_cookie_and_origin_without_echoing_it(self):
        env = {COOKIE_ENV: COOKIE_SECRET}
        with patch.dict(os.environ, env), \
             patch.object(provider_case_run, "ProviderPublishesTransport", FakeTransport):
            adapter = provider_case_run.provider_publishes_adapter(
                validate_provider_publishes_profile(provider_profile(preprod=True)),
                run_id=RUN_ID,
            )
            headers = adapter._human_headers()
        self.assertEqual(headers["Cookie"], COOKIE_SECRET)
        self.assertEqual(headers["Origin"], PREPROD_BROWSER_ORIGIN)
        self.assertEqual(headers["Referer"], PREPROD_BROWSER_ORIGIN + "/")
        self.assertNotIn(COOKIE_SECRET, json.dumps(adapter.profile, sort_keys=True))

    def run_command(self, *, preprod: bool = False) -> tuple[dict, Path]:
        profile = provider_profile(preprod=preprod)
        profile_path = self.root / "profile.json"
        profile_path.write_text(json.dumps(profile), encoding="utf-8")
        state_dir = self.root / ("state-preprod" if preprod else "state-local")
        env = {COOKIE_ENV: COOKIE_SECRET} if preprod else {}
        with patch.dict(os.environ, env), \
             patch.object(provider_case_run, "ProviderPublishesTransport", FakeTransport):
            report = dual_provider_publishes_case_run(
                profile_path=profile_path,
                case_key=CASE_KEY,
                state_dir=state_dir,
                run_id=RUN_ID,
                allow_write=True,
                confirm_run_id=RUN_ID,
            )
        return report, state_dir

    def test_command_writes_sanitized_receipts(self):
        report, state_dir = self.run_command()
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["case_id"], CASE_ID)
        self.assertEqual(report["obligation_summary"],
                         {"total": 42, "frozen_core": 32,
                          "source_api_get_discovery": 10})
        self.assertEqual(report["remote_validation"], "NOT_RUN")
        self.assertFalse(report["grants_release_credit"])
        profile_file = state_dir / "provider-publishes-provider-publishes-profile.json"
        binding_file = state_dir / "provider-publishes-provider-publishes-binding.json"
        run_file = state_dir / "provider-publishes-provider-publishes-run.json"
        self.assertEqual(read(run_file), report)
        self.assertEqual(len(read(binding_file)["requirement_ids"]), 42)
        receipts = profile_file.read_text() + binding_file.read_text() + run_file.read_text()
        for secret in (COOKIE_SECRET, ADMIN_TOKEN, CALLBACK_TOKEN, RUNTIME_TOKEN):
            self.assertNotIn(secret, receipts)

    def test_preprod_command_keeps_cookie_kind_and_path_only(self):
        report, state_dir = self.run_command(preprod=True)
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["environment"]["tier"], "preprod")
        self.assertEqual(
            report["credential_kind"],
            "preprod_human_cookie_with_minted_provider_credentials",
        )
        receipts = "".join(path.read_text() for path in state_dir.glob("*.json"))
        self.assertNotIn(COOKIE_SECRET, receipts)
        self.assertIn(str(Path("/Users/example/.codex/bcs-e2e/preprod-human-cookie.txt")),
                      receipts)

    def test_permissions_and_fresh_state_are_enforced(self):
        profile_path = self.root / "profile.json"
        profile_path.write_text(json.dumps(provider_profile()), encoding="utf-8")
        state_dir = self.root / "state-permission"
        with self.assertRaisesRegex(ConfigError, "exact Run confirmation"):
            dual_provider_publishes_case_run(
                profile_path=profile_path,
                case_key=CASE_KEY,
                state_dir=state_dir,
                run_id=RUN_ID,
                allow_write=True,
                confirm_run_id="other",
            )
        self.assertFalse(state_dir.exists())
        state_dir.mkdir()
        with self.assertRaisesRegex(ConfigError, "fresh private state directory"):
            dual_provider_publishes_case_run(
                profile_path=profile_path,
                case_key=CASE_KEY,
                state_dir=state_dir,
                run_id=RUN_ID,
                allow_write=True,
                confirm_run_id=RUN_ID,
            )

    def test_case_is_registered_in_migration_and_cli(self):
        rows = {
            row["case_id"]
            for row in migration_plan()["cross_environment_product_cli_case_slices"]
        }
        self.assertIn(CASE_ID, rows)
        state_dir = self.root / "dispatch"
        profile_path = self.root / "dispatch-profile.json"
        profile_path.write_text(json.dumps(provider_profile()), encoding="utf-8")
        arguments = [
            "dual-provider-publishes-run",
            "--profile", str(profile_path),
            "--case", CASE_KEY,
            "--state-dir", str(state_dir),
            "--run-id", RUN_ID,
            "--confirm-run-id", RUN_ID,
            "--allow-write",
        ]
        with patch("bcs_e2e.cli.dual_provider_publishes_case_run",
                   return_value={"status": "PASS"}) as execute, \
             redirect_stdout(io.StringIO()) as stdout:
            rc = main(arguments)
        self.assertEqual(rc, 0)
        execute.assert_called_once_with(
            profile_path=profile_path,
            case_key=CASE_KEY,
            state_dir=state_dir,
            run_id=RUN_ID,
            allow_write=True,
            confirm_run_id=RUN_ID,
        )
        self.assertEqual(json.loads(stdout.getvalue())["status"], "PASS")


if __name__ == "__main__":
    unittest.main()
