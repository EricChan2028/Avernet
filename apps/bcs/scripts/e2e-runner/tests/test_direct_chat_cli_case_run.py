"""Command and report tests for the dual Direct-chat product-CLI case run."""

from __future__ import annotations

from contextlib import nullcontext, redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
TEST_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(TEST_ROOT))
sys.path.insert(0, str(ROOT))

from bcs_e2e import direct_chat_cli_case_run as direct_chat_case_runner
from bcs_e2e.cli import main
from bcs_e2e.case_adapters import AdapterCall
from bcs_e2e.case_executor import CaseExecution, StepOutcome
from bcs_e2e.direct_chat_cli_case_run import (
    _CASES,
    _HTTP_PRODUCT_OPERATIONS,
    _require_step_evidence,
    DIRECT_CHAT_CLI_EFFECT,
    DIRECT_CHAT_CLI_OPERATIONS,
    DIRECT_CHAT_DECLARED_ROUTES,
    DIRECT_CHAT_MAIN_CANCEL_OPERATION,
    DIRECT_CHAT_MAIN_READ_OPERATION,
    DIRECT_CHAT_MAIN_SUBMIT_OPERATION,
    DIRECT_CHAT_MAIN_TERMINAL_OPERATION,
    DIRECT_CHAT_PING_CANCEL_OPERATION,
    DIRECT_CHAT_PING_SUBMIT_OPERATION,
    DIRECT_CHAT_PING_TERMINAL_OPERATION,
    DIRECT_CHAT_REQUIREMENTS,
    DIRECT_CHAT_REQUIREMENT_OPERATIONS,
    DUAL_DIRECT_CHAT_CASE_ID,
    DirectChatCliRunnerError,
    DualDirectChatAdapter,
    PARENT,
    PING_MESSAGE,
    MAIN_MESSAGE,
    MAIN_TAGS,
    MAIN_TIMEOUT_MS,
    RemoteLocalDirectChatAdapter,
    dual_direct_chat_case_run,
    dual_direct_chat_adaptability,
    direct_chat_cli_case_plan,
    direct_chat_cli_credential_kind,
    execute_direct_chat_case,
    expected_step_transport,
    load_direct_chat_cli_case_profile,
    validate_direct_chat_cli_profile,
)
from bcs_e2e.local_direct_chat_contract import REQUIREMENTS as DM_REQUIREMENTS
from bcs_e2e.migration_inventory import migration_plan
from bcs_e2e.profile import ConfigError, fingerprint
from bcs_e2e.run_state import RunStateError

RUN_ID = "direct-chat-run-1"
HUMAN = "39464"
DRIVER_BOT = "driver-direct-chat"
MEMBER_BOT = "member-direct-chat"
PREPROD_DRIVER = "20261026_xq102n2b:39464"
PREPROD_MEMBER = "20261010_gekng5hk:39464"
DRIVER_TOKEN_ENV = "BCS_E2E_DRIVER_BOT_TOKEN"
MEMBER_TOKEN_ENV = "BCS_E2E_MEMBER_BOT_TOKEN"
COOKIE_ENV = "BCS_E2E_HUMAN_COOKIE"
DRIVER_TOKEN = "secret-driver-token-value"
MEMBER_TOKEN = "secret-member-token-value"
HUMAN_COOKIE = "secret-human-cookie-value"
PING_RUN = "run-ping-e2e-0001"
PING_SESSION = "sess-ping-e2e-0001"
MAIN_RUN = "run-main-e2e-0002"
MAIN_SESSION = "sess-main-e2e-0002"
MAIN_CANCEL_VERSION = 9
MAIN_TERMINAL_VERSION = 10
PING_CANCEL_VERSION = 11
PING_TERMINAL_VERSION = 12
DM_METHODS = ["submit_ping_run", "submit_main_run", "read_main_run",
              "cancel_main_run", "read_main_terminal_run"]
OPERATIONS_BY_RUN = [
    DIRECT_CHAT_PING_SUBMIT_OPERATION,
    DIRECT_CHAT_MAIN_SUBMIT_OPERATION,
    DIRECT_CHAT_MAIN_READ_OPERATION,
    DIRECT_CHAT_MAIN_CANCEL_OPERATION,
    DIRECT_CHAT_MAIN_TERMINAL_OPERATION,
    DIRECT_CHAT_PING_CANCEL_OPERATION,
    DIRECT_CHAT_PING_TERMINAL_OPERATION,
]


_NULL = nullcontext()


def trio_auth() -> dict:
    return {"kind": "human-cookie-with-owned-bot-tokens",
            "driver_token_env": DRIVER_TOKEN_ENV,
            "member_token_env": MEMBER_TOKEN_ENV,
            "cookie_env": COOKIE_ENV}


def route_bindings() -> dict:
    return {
        operation: {"method": method, "path": path, "api_face": "legacy",
                    "auth": "case_owned_driver", "response": "legacy-json",
                    "traceparent": "disabled"}
        for operation, (method, path) in DIRECT_CHAT_DECLARED_ROUTES.items()
    }


def profile_value(*, preprod=False, mutate=None, auth=None) -> dict:
    driver = PREPROD_DRIVER if preprod else DRIVER_BOT
    member = PREPROD_MEMBER if preprod else MEMBER_BOT
    value = {
        "schema_version": 1,
        "contract": direct_chat_case_runner.DIRECT_CHAT_CLI_PROFILE_CONTRACT,
        "environment": {
            "tier": "preprod" if preprod else "local",
            "id": "direct-chat-cli-preprod" if preprod else "direct-chat-cli-local",
            "expected_bcs_env": "pre" if preprod else "local",
        },
        "entrypoint": {
            "kind": "gateway" if preprod else "singlebox",
            "origin": "https://bcs-pre.alipay.com" if preprod
            else "http://127.0.0.1:21000",
            "api_face": "legacy", "manifest_face": "legacy",
        },
        "human": {"user_id": HUMAN},
        "actors": {
            "driver": {"bot_id": driver, "owner_id": HUMAN, "dedicated": True,
                       "visibility": "public"},
            "member": {"bot_id": member, "owner_id": HUMAN, "dedicated": True,
                       "visibility": "public"},
        },
        "auth": (auth if auth is not None else
                 ({"kind": "human-cookie", "cookie_env": COOKIE_ENV} if preprod else
                  {"kind": "owned-local-bot-tokens",
                   "driver_token_env": DRIVER_TOKEN_ENV})),
        "relationships": dict(direct_chat_case_runner.DIRECT_CHAT_CLI_RELATIONSHIPS),
        "policy": {"fixture_pool": "direct-chat-cli-pool",
                   "exclusive_use_confirmed": True, "timeout_seconds": 10,
                   "max_response_bytes": 65536, "failure_retention_hours": 24},
        "cli": {"transport": "product-cli", "command_family": "chat",
                "api_face": "legacy"},
        "route_bindings": route_bindings(),
    }
    if preprod:
        value["browser_origin"] = "https://teamclaw-pre.alipay.com"
    if mutate is not None:
        mutate(value)
    return value


def local_profile() -> dict:
    return validate_direct_chat_cli_profile(profile_value())


def preprod_profile(auth=None) -> dict:
    return validate_direct_chat_cli_profile(profile_value(preprod=True, auth=auth))


def submit_projection(profile, run_id, session_id) -> dict:
    return {"delivered": True, "submitted": True,
            "bot_uuid": profile["actors"]["member"]["bot_id"], "run_id": run_id,
            "session_id": session_id, "state": "submitted",
            "delivery_status": "queued", "wait_reason": None, "error_message": None}


def status_projection(profile, run_id, session_id, *, state, version,
                      is_terminal, content: str = "") -> dict:
    return {"run_id": run_id,
            "bot_uuid": profile["actors"]["member"]["bot_id"],
            "from_bot_id": profile["actors"]["driver"]["bot_id"],
            "session_id": session_id, "state": state,
            "response": {"content": content},
            "error_message": None, "created_at_ms": 1735689600000,
            "updated_at_ms": 1735689600001, "expires_at_ms": 1735689600002,
            "version": version, "content_truncated": False,
            "is_terminal": is_terminal}


def cancel_projection(run_id, version) -> dict:
    return {"run_id": run_id, "cancelled": True, "state": "cancelled",
            "response": {"content": ""}, "error_message": None, "version": version,
            "content_truncated": False}


def bytes_json(value) -> bytes:
    return json.dumps(value).encode("utf-8")


def completed(returncode, stdout) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(["bcs-cli"], returncode, stdout, b"")


def read_receipt(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


class FakeChainRunner:
    """Drives the REAL dual adapter and execution spine offline, in order."""

    def __init__(self, profile, **kwargs):
        self.profile = profile
        self.calls = []
        self.fail_at = None
        self.duplicate_ping_identity = False
        self.remote_async = False
        self.ping_terminal_pending = False
        self.ping_cancel_state = "running"
        self.main_cancel_version = MAIN_CANCEL_VERSION
        self.main_terminal_version = MAIN_TERMINAL_VERSION
        self.ping_cancel_version = PING_CANCEL_VERSION
        self.ping_terminal_version = PING_TERMINAL_VERSION

    def execute(self, operation, **kwargs):
        self.calls.append((operation, kwargs))
        if operation == self.fail_at:
            raise RuntimeError("simulated product CLI failure")
        kind = direct_chat_cli_credential_kind(self.profile)
        method, path = DIRECT_CHAT_DECLARED_ROUTES[operation]
        evidence = {"transport": "PRODUCT_CLI", "operation": operation,
                    "caller_role": "driver", "command_arguments": [],
                    "http_method_declared": method, "route_declared": path,
                    "credential_kind": kind, "exit_status": 0, "elapsed_ms": 1.0,
                    "fixture_effect": DIRECT_CHAT_CLI_EFFECT,
                    "remote_validation": "NOT_RUN",
                    "grants_release_credit": False}
        if operation in (DIRECT_CHAT_PING_SUBMIT_OPERATION,
                         DIRECT_CHAT_MAIN_SUBMIT_OPERATION):
            if operation == DIRECT_CHAT_PING_SUBMIT_OPERATION:
                run_id, session_id = PING_RUN, PING_SESSION
            elif self.duplicate_ping_identity:
                run_id, session_id = PING_RUN, PING_SESSION
            else:
                run_id, session_id = MAIN_RUN, MAIN_SESSION
            return {"data": submit_projection(self.profile, run_id, session_id),
                    "evidence": evidence}
        if operation == DIRECT_CHAT_MAIN_READ_OPERATION:
            state_data = (status_projection(self.profile, MAIN_RUN, MAIN_SESSION,
                                            state="pending", version=4,
                                            is_terminal=False)
                          if self.remote_async else
                          status_projection(self.profile, MAIN_RUN, MAIN_SESSION,
                                            state="running", version=5,
                                            is_terminal=False))
            return {"data": state_data, "evidence": evidence}
        if operation == DIRECT_CHAT_MAIN_CANCEL_OPERATION:
            if self.remote_async:
                return {"data": {**cancel_projection(
                    MAIN_RUN, self.main_cancel_version), "cancelled": False,
                    "state": "running"}, "evidence": evidence}
            return {"data": cancel_projection(MAIN_RUN, self.main_cancel_version),
                    "evidence": evidence}
        if operation == DIRECT_CHAT_MAIN_TERMINAL_OPERATION:
            return {"data": status_projection(self.profile, MAIN_RUN, MAIN_SESSION,
                                              state="cancelled",
                                              version=self.main_terminal_version,
                                              is_terminal=True), "evidence": evidence}
        if operation == DIRECT_CHAT_PING_CANCEL_OPERATION:
            if self.remote_async:
                return {"data": {**cancel_projection(
                    PING_RUN, self.ping_cancel_version), "cancelled": False,
                    "state": self.ping_cancel_state}, "evidence": evidence}
            return {"data": cancel_projection(PING_RUN, self.ping_cancel_version),
                    "evidence": evidence}
        if operation == DIRECT_CHAT_PING_TERMINAL_OPERATION:
            if self.remote_async and self.ping_terminal_pending:
                return {"data": status_projection(
                    self.profile, PING_RUN, PING_SESSION, state="running",
                    version=self.ping_cancel_version, is_terminal=False),
                    "evidence": evidence}
            if self.remote_async:
                return {"data": status_projection(
                    self.profile, PING_RUN, PING_SESSION, state="completed",
                    version=self.ping_terminal_version, is_terminal=True,
                    content="pong"), "evidence": evidence}
            return {"data": status_projection(self.profile, PING_RUN, PING_SESSION,
                                              state="cancelled",
                                              version=self.ping_terminal_version,
                                              is_terminal=True), "evidence": evidence}
        raise AssertionError(operation)


class FakeRunner:
    def __init__(self, profile, *, binary, binary_sha256, state_dir):
        self.profile = profile
        self.state_dir = Path(state_dir).resolve()


class FakeAdapter:
    def __init__(self, profile, runner):
        self.profile = profile
        self.runner = runner


def fake_execution(*, status="PASS", error_type=None) -> CaseExecution:
    passed = status == "PASS"
    steps = tuple(
        StepOutcome(requirement_id=requirement, method=method,
                    status="PASS" if passed else "NOT_RUN",
                    data={"case_entry": "direct-chat"} if passed else None,
                    evidence={"credential_kind": "owned_local_bot_token"} if passed
                    else {},
                    error_type=None if passed else error_type)
        for requirement, method in zip(DIRECT_CHAT_REQUIREMENTS, DM_METHODS))
    execution = CaseExecution(
        case_id=DUAL_DIRECT_CHAT_CASE_ID, parent_id=PARENT, status=status,
        steps=steps, completed_count=5 if passed else 0, total_count=5,
        error_type=error_type, ready_to_execute=passed)
    execution.validate()
    return execution


class DirectChatCliCaseRunTests(unittest.TestCase):
    """The five dual-environment command entry tests (channel mirror)."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(self.root, ignore_errors=True))
        self.cli_binary = self.root / "bcs-cli"
        self.cli_binary.write_bytes(b"#!/bin/sh\nexit 0\n")
        self.cli_binary.chmod(0o700)
        self.cli_sha256 = hashlib.sha256(self.cli_binary.read_bytes()).hexdigest()

    def invoke(self, *, preprod=False, auth=None):
        profile = preprod_profile(auth) if preprod else local_profile()
        profile_path = self.root / f"profile-{'preprod' if preprod else 'local'}.json"
        profile_path.write_text(json.dumps(profile), encoding="utf-8")
        state_dir = self.root / f"state-{'preprod' if preprod else 'local'}"
        expected = fake_execution()
        secrets = {DRIVER_TOKEN_ENV: DRIVER_TOKEN, MEMBER_TOKEN_ENV: MEMBER_TOKEN,
                   COOKIE_ENV: HUMAN_COOKIE}
        with patch.dict(os.environ, secrets), \
             patch.object(direct_chat_case_runner, "DirectChatCliRunner", FakeRunner), \
             patch.dict(_CASES["direct-chat"],
                        {"adapter": FakeAdapter,
                         "execute": lambda adapter: expected}):
            report = dual_direct_chat_case_run(
                profile_path=profile_path, cli_binary=self.cli_binary,
                cli_binary_sha256=self.cli_sha256, case_key="direct-chat",
                state_dir=state_dir, run_id=RUN_ID, allow_write=True,
                confirm_run_id=RUN_ID)
        return report, profile, state_dir

    def test_report_is_registered_in_the_case_inventory(self):
        rows = {row["case_id"] for row in migration_plan()[
            "cross_environment_product_cli_case_slices"]}
        self.assertIn("DUAL-DIRECT-CHAT-01", rows)

    def test_local_run_writes_bound_sanitized_receipts(self):
        report, profile, state_dir = self.invoke()
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["case_id"], "DUAL-DIRECT-CHAT-01")
        self.assertEqual(report["parent_id"], PARENT)
        self.assertEqual(report["credential_kind"], "owned_local_bot_token")
        self.assertEqual(report["remote_validation"], "NOT_RUN")
        self.assertFalse(report["grants_release_credit"])
        self.assertEqual(report["resource_disposition"], DIRECT_CHAT_CLI_EFFECT)
        self.assertEqual(read_receipt(state_dir / "direct-chat-profile.json"), profile)
        binding = read_receipt(state_dir / "direct-chat-binding.json")
        self.assertEqual(binding["case_id"], "DUAL-DIRECT-CHAT-01")
        self.assertEqual(binding["parent_id"], PARENT)
        self.assertEqual(binding["run_id"], RUN_ID)
        self.assertEqual(binding["cli_binary_sha256"], self.cli_sha256)
        self.assertEqual(binding["profile_digest"], fingerprint(profile))
        self.assertTrue(binding["executor_digest"])
        self.assertEqual(read_receipt(state_dir / "direct-chat-run.json"), report)
        raw = json.dumps(report, sort_keys=True)
        self.assertNotIn("secret-driver-token-value", raw)
        self.assertNotIn("secret-member-token-value", raw)
        self.assertNotIn("secret-human-cookie-value", raw)

    def test_preprod_report_records_cookie_kind_without_secret(self):
        report, _, _ = self.invoke(preprod=True)
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["credential_kind"], "human_cookie")
        self.assertEqual(report["environment"]["tier"], "preprod")
        self.assertEqual(report["environment"]["entrypoint_kind"], "gateway")
        self.assertNotIn("secret-human-cookie-value", json.dumps(report, sort_keys=True))

    def test_permissions_and_fresh_state_are_enforced(self):
        state_dir = self.root / "state"
        profile_path = self.root / "profile.json"
        profile_path.write_text(json.dumps(local_profile()), encoding="utf-8")
        with self.assertRaisesRegex(ConfigError, "exact Run confirmation"):
            dual_direct_chat_case_run(
                profile_path=profile_path, cli_binary=self.cli_binary,
                cli_binary_sha256=self.cli_sha256, case_key="direct-chat",
                state_dir=state_dir, run_id=RUN_ID, allow_write=True,
                confirm_run_id="other")
        self.assertFalse(state_dir.exists())
        state_dir.mkdir()
        with self.assertRaisesRegex(ConfigError, "fresh private state directory"):
            dual_direct_chat_case_run(
                profile_path=profile_path, cli_binary=self.cli_binary,
                cli_binary_sha256=self.cli_sha256, case_key="direct-chat",
                state_dir=state_dir, run_id=RUN_ID, allow_write=True,
                confirm_run_id=RUN_ID)

    def test_cli_dispatch_uses_the_dual_direct_chat_runner(self):
        state_dir = self.root / "dispatch"
        profile_path = self.root / "dispatch-profile.json"
        profile_path.write_text(json.dumps(local_profile()), encoding="utf-8")
        arguments = [
            "dual-direct-chat-run", "--profile", str(profile_path),
            "--case", "direct-chat", "--cli-binary", str(self.cli_binary),
            "--cli-binary-sha256", self.cli_sha256, "--state-dir", str(state_dir),
            "--run-id", RUN_ID, "--confirm-run-id", RUN_ID, "--allow-write",
        ]
        with patch("bcs_e2e.cli.dual_direct_chat_case_run",
                   return_value={"status": "PASS"}) as execute, \
             redirect_stdout(io.StringIO()) as stdout:
            rc = main(arguments)
        self.assertEqual(rc, 0)
        execute.assert_called_once_with(
            profile_path=profile_path, cli_binary=self.cli_binary,
            cli_binary_sha256=self.cli_sha256, case_key="direct-chat",
            state_dir=state_dir, run_id=RUN_ID, allow_write=True,
            confirm_run_id=RUN_ID)
        self.assertEqual(json.loads(stdout.getvalue())["status"], "PASS")


class DirectChatCliCaseRunReceiptTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(self.root, ignore_errors=True))
        self.cli_binary = self.root / "bcs-cli"
        self.cli_binary.write_bytes(b"#!/bin/sh\nexit 0\n")
        self.cli_binary.chmod(0o700)
        self.cli_sha256 = hashlib.sha256(self.cli_binary.read_bytes()).hexdigest()

    def command(self, profile, state_dir, **flags):
        profile_path = self.root / "profile.json"
        profile_path.write_text(json.dumps(profile), encoding="utf-8")
        defaults = {"run_id": RUN_ID, "allow_write": True, "confirm_run_id": RUN_ID}
        defaults.update(flags)
        with patch.object(direct_chat_case_runner, "DirectChatCliRunner", FakeRunner), \
             patch.dict(_CASES["direct-chat"],
                        {"adapter": FakeAdapter,
                         "execute": lambda adapter: fake_execution()}):
            return dual_direct_chat_case_run(
                profile_path=profile_path, cli_binary=self.cli_binary,
                cli_binary_sha256=self.cli_sha256, case_key="direct-chat",
                state_dir=state_dir, **defaults)

    def test_blocked_execution_writes_receipts_without_release_credit(self):
        blocked = fake_execution(status="BLOCKED", error_type="RunStateError")
        profile_path = self.root / "profile.json"
        profile_path.write_text(json.dumps(local_profile()), encoding="utf-8")
        state_dir = self.root / "state"
        with patch.object(direct_chat_case_runner, "DirectChatCliRunner", FakeRunner), \
             patch.dict(_CASES["direct-chat"],
                        {"adapter": FakeAdapter,
                         "execute": lambda adapter: blocked}):
            report = dual_direct_chat_case_run(
                profile_path=profile_path, cli_binary=self.cli_binary,
                cli_binary_sha256=self.cli_sha256, case_key="direct-chat",
                state_dir=state_dir, run_id=RUN_ID, allow_write=True,
                confirm_run_id=RUN_ID)
        self.assertEqual(report["status"], "BLOCKED")
        self.assertEqual(report["execution"]["error_type"], "RunStateError")
        self.assertEqual(report["execution"]["completed_count"], 0)
        self.assertFalse(report["grants_release_credit"])
        self.assertEqual(read_receipt(state_dir / "direct-chat-run.json"), report)

    def test_runner_construction_failure_wraps_into_config_error(self):
        def exploding(profile, *, binary, binary_sha256, state_dir):
            raise DirectChatCliRunnerError("binary is not approved")

        profile_path = self.root / "profile.json"
        profile_path.write_text(json.dumps(local_profile()), encoding="utf-8")
        state_dir = self.root / "state"
        with patch.object(direct_chat_case_runner, "DirectChatCliRunner", exploding):
            with self.assertRaisesRegex(ConfigError, "case execution is unavailable"):
                dual_direct_chat_case_run(
                    profile_path=profile_path, cli_binary=self.cli_binary,
                    cli_binary_sha256=self.cli_sha256, case_key="direct-chat",
                    state_dir=state_dir, run_id=RUN_ID, allow_write=True,
                    confirm_run_id=RUN_ID)

    def test_placeholder_profile_is_rejected_before_state_creation(self):
        mutated = profile_value()
        mutated["policy"]["fixture_pool"] = "REPLACE_WITH_fixture_pool"
        profile_path = self.root / "profile.json"
        profile_path.write_text(json.dumps(mutated), encoding="utf-8")
        state_dir = self.root / "state"
        with patch.object(direct_chat_case_runner, "DirectChatCliRunner", FakeRunner):
            with self.assertRaisesRegex(ConfigError, "REPLACE_WITH_"):
                self.command(mutated, state_dir)
        self.assertFalse(state_dir.exists())

    def test_unreadable_or_non_object_profiles_fail_closed(self):
        bad_json = self.root / "bad.json"
        bad_json.write_text("{not json", encoding="utf-8")
        with self.assertRaisesRegex(ConfigError, "unreadable JSON"):
            load_direct_chat_cli_case_profile(bad_json, "direct-chat")
        bad_json.write_text("[1, 2, 3]", encoding="utf-8")
        with self.assertRaisesRegex(ConfigError, "must be an object"):
            load_direct_chat_cli_case_profile(bad_json, "direct-chat")
        duplicate = self.root / "duplicate.json"
        duplicate.write_text('{"schema_version": 1, "schema_version": 1}',
                             encoding="utf-8")
        with self.assertRaisesRegex(ConfigError, "duplicate JSON key"):
            load_direct_chat_cli_case_profile(duplicate, "direct-chat")
        with self.assertRaisesRegex(ConfigError, "must be direct-chat"):
            load_direct_chat_cli_case_profile(self.root / "missing.json", "other")

    def test_run_id_and_state_directory_gates_are_exact(self):
        with self.assertRaisesRegex(ConfigError, "direct-chat run ID"):
            self.command(local_profile(), self.root / "state", run_id="has space")
        profile_path = self.root / "profile.json"
        profile_path.write_text(json.dumps(preprod_profile()), encoding="utf-8")
        with patch.object(direct_chat_case_runner, "DirectChatCliRunner", FakeRunner), \
             patch.dict(_CASES["direct-chat"],
                        {"adapter": FakeAdapter,
                         "execute": lambda adapter: fake_execution()}):
            with self.assertRaisesRegex(ConfigError, "explicit write permission"):
                self.command(preprod_profile(), self.root / "s1", allow_write=False)
            linked = self.root / "linked"
            linked.symlink_to(self.root / "elsewhere", target_is_directory=True)
            with self.assertRaisesRegex(ConfigError, "fresh private state directory"):
                self.command(preprod_profile(), linked)
            with self.assertRaisesRegex(ConfigError, "fresh private state directory"):
                self.command(preprod_profile(), Path("relative/state"))


class DirectChatCliProfileTests(unittest.TestCase):
    def test_local_and_preprod_profiles_validate(self):
        local = validate_direct_chat_cli_profile(profile_value())
        self.assertEqual(local["environment"]["tier"], "local")
        self.assertNotIn("browser_origin", local)
        preprod = validate_direct_chat_cli_profile(profile_value(preprod=True))
        self.assertEqual(preprod["environment"]["tier"], "preprod")
        self.assertEqual(preprod["browser_origin"], "https://teamclaw-pre.alipay.com")
        trio = validate_direct_chat_cli_profile(
            profile_value(preprod=True, auth=trio_auth()))
        self.assertEqual(trio["auth"]["kind"], "human-cookie-with-owned-bot-tokens")

    def test_browser_origin_is_pinned_per_tier(self):
        with self.assertRaisesRegex(ConfigError, "missing or unknown fields"):
            validate_direct_chat_cli_profile(profile_value(preprod=True,
                                                           mutate=lambda v: v.pop(
                                                               "browser_origin")))
        local = profile_value()
        local["browser_origin"] = "https://teamclaw-pre.alipay.com"
        with self.assertRaisesRegex(ConfigError, "missing or unknown fields"):
            validate_direct_chat_cli_profile(local)

    def test_preprod_browser_origin_must_be_https_host_only(self):
        for bad in ("http://teamclaw-pre.alipay.com",
                    "https://teamclaw-pre.alipay.com/chat",
                    "https://user:pw@teamclaw-pre.alipay.com",
                    "https://teamclaw-pre.alipay.com\n"):
            with self.subTest(bad=bad):
                mutated = profile_value(preprod=True)
                mutated["browser_origin"] = bad
                with self.assertRaisesRegex(ConfigError, "origin"):
                    validate_direct_chat_cli_profile(mutated)

    def test_entrypoint_shapes_are_tier_bound(self):
        for mutate, where in (
                (lambda value: value["entrypoint"].update(
                    {"origin": "https://127.0.0.1:21000"}), "local https"),
                (lambda value: value["entrypoint"].update(
                    {"kind": "gateway"}), "local gateway"),
                (lambda value: value["entrypoint"].update(
                    {"origin": "http://example.com"}), "local non-loopback"),
                (lambda value: value["entrypoint"].update(
                    {"origin": "http://bcs-pre.alipay.com"}), "preprod http"),
                (lambda value: value["entrypoint"].update(
                    {"origin": "https://bcs.example.com"}), "preprod unlisted host"),
                (lambda value: value["entrypoint"].update(
                    {"kind": "singlebox"}), "preprod singlebox"),
                (lambda value: value["entrypoint"].update(
                    {"api_face": "public-v1"}), "api face"),
                (lambda value: value["entrypoint"].update(
                    {"manifest_face": "internal-v1"}), "manifest face")):
            with self.subTest(where=where):
                value = profile_value(preprod=where.startswith("preprod"))
                mutate(value)
                with self.assertRaisesRegex(ConfigError, "origin|entrypoint"):
                    validate_direct_chat_cli_profile(value)

    def test_actors_are_owned_dedicated_public_and_distinct(self):
        for mutate in (
                lambda value: value["actors"]["driver"].update({"dedicated": False}),
                lambda value: value["actors"]["member"].update(
                    {"visibility": "private"}),
                lambda value: value["actors"]["driver"].update(
                    {"owner_id": "someone-else"}),
                lambda value: value["actors"]["member"].update(
                    {"bot_id": value["actors"]["driver"]["bot_id"]}),
                lambda value: value["human"].update({"user_id": "not an id"})):
            with self.assertRaisesRegex(ConfigError, "actors|human"):
                validate_direct_chat_cli_profile(profile_value(mutate=mutate))

    def test_auth_family_is_tier_bound_and_exact(self):
        for mutate, where in (
                (lambda value: value["auth"].update({"kind": "human-cookie"}),
                 "local cookie face"),
                (lambda value: value["auth"].update(
                    {"kind": "owned-local-bot-tokens", "member_token_env": "X"}),
                 "local trio face"),
                (lambda value: value["auth"].update(
                    {"driver_token_env": "lowercase_env"}), "bad env ref")):
            with self.subTest(where=where):
                with self.assertRaisesRegex(ConfigError, "auth"):
                    validate_direct_chat_cli_profile(profile_value(mutate=mutate))
        cookie_short = profile_value(preprod=True)
        cookie_short["auth"] = {"kind": "human-cookie"}
        with self.assertRaisesRegex(ConfigError, "auth"):
            validate_direct_chat_cli_profile(cookie_short)
        trio_short = profile_value(preprod=True, auth=trio_auth())
        del trio_short["auth"]["member_token_env"]
        with self.assertRaisesRegex(ConfigError, "auth"):
            validate_direct_chat_cli_profile(trio_short)
        with self.assertRaisesRegex(ConfigError, "auth"):
            validate_direct_chat_cli_profile(profile_value(auth=trio_auth()))

    def test_expected_bcs_env_must_follow_the_tier(self):
        drifted = profile_value()
        drifted["environment"]["expected_bcs_env"] = "preprod"
        with self.assertRaisesRegex(ConfigError, "expected_bcs_env"):
            validate_direct_chat_cli_profile(drifted)
        pre = validate_direct_chat_cli_profile(profile_value(preprod=True))
        self.assertEqual(pre["environment"]["expected_bcs_env"], "pre")

    def test_relationships_policy_cli_and_top_level_shapes_are_exact(self):
        mutations = (
            (lambda value: value["relationships"].update(
                {"cleanup": "delete-group-and-session"}), "relationships"),
            (lambda value: value["policy"].update(
                {"exclusive_use_confirmed": False}), "policy"),
            (lambda value: value["policy"].update({"timeout_seconds": 0}), "policy"),
            (lambda value: value["policy"].update({"timeout_seconds": 31}), "policy"),
            (lambda value: value["policy"].update({"max_response_bytes": 0}), "policy"),
            (lambda value: value["policy"].update(
                {"max_response_bytes": 65537}), "policy"),
            (lambda value: value["policy"].update(
                {"failure_retention_hours": 169}), "policy"),
            (lambda value: value["policy"].update(
                {"fixture_pool": "bad pool"}), "policy"),
            (lambda value: value["cli"].update(
                {"command_family": "session"}), "cli"),
            (lambda value: value["cli"].update(
                {"transport": "product-http"}), "cli"),
            (lambda value: value["cli"].update(
                {"api_face": "public-v1"}), "cli"),
            (lambda value: value.update({"schema_version": 2}), "profile"),
            (lambda value: value.update({"contract": "other-contract"}), "profile"),
            (lambda value: value.update({"extra": True}), "profile"),
            (lambda value: value.pop("route_bindings"), "profile"),
        )
        for mutate, where in mutations:
            with self.subTest(where=where):
                with self.assertRaisesRegex(ConfigError, where):
                    validate_direct_chat_cli_profile(profile_value(mutate=mutate))

    def test_route_bindings_must_declare_the_seven_reviewed_leaves(self):
        mutations = (
            (lambda value: value["route_bindings"].pop(
                DIRECT_CHAT_PING_TERMINAL_OPERATION), "seven reviewed DM leaves"),
            (lambda value: value["route_bindings"].update(
                {"direct_chat.unknown.op": {"method": "GET", "path": "/x",
                                            "api_face": "legacy",
                                            "auth": "case_owned_driver",
                                            "response": "legacy-json",
                                            "traceparent": "disabled"}}),
             "seven reviewed DM leaves"),
            (lambda value: value["route_bindings"][
                DIRECT_CHAT_MAIN_READ_OPERATION].update({"method": "POST"}),
             "DM migration contract"),
            (lambda value: value["route_bindings"][
                DIRECT_CHAT_PING_SUBMIT_OPERATION].update(
                {"path": "/bots/{driver_actor_id}/chat-async"}),
             "DM migration contract"),
            (lambda value: value["route_bindings"][
                DIRECT_CHAT_MAIN_CANCEL_OPERATION].update(
                {"auth": "case_owned_member"}), "DM migration contract"),
            (lambda value: value["route_bindings"][
                DIRECT_CHAT_MAIN_READ_OPERATION].update(
                {"traceparent": "enabled"}), "DM migration contract"),
            (lambda value: value["route_bindings"][
                DIRECT_CHAT_MAIN_TERMINAL_OPERATION].update(
                {"response": "public-v1-json"}), "DM migration contract"),
        )
        for mutate, where in mutations:
            with self.subTest(where=where):
                with self.assertRaisesRegex(ConfigError, where):
                    validate_direct_chat_cli_profile(profile_value(mutate=mutate))

    def test_projection_fingerprint_covers_browser_origin(self):
        cookie = profile_value(preprod=True)
        other = profile_value(preprod=True)
        other["browser_origin"] = "https://teamclaw-pre.alipay.example.com"
        self.assertNotEqual(fingerprint(cookie), fingerprint(other))


    def test_preprod_actors_require_the_qualified_station_form(self):
        value = profile_value(preprod=True, mutate=lambda profile: (
            profile["actors"]["driver"].update({"bot_id": DRIVER_BOT}),
            profile["actors"]["member"].update({"bot_id": MEMBER_BOT}),
        ))
        with self.assertRaisesRegex(ConfigError, "actors.*qualified"):
            validate_direct_chat_cli_profile(value)

    def test_local_actors_require_the_bare_bot_form(self):
        value = profile_value(mutate=lambda profile: (
            profile["actors"]["driver"].update({"bot_id": PREPROD_DRIVER}),
            profile["actors"]["member"].update({"bot_id": PREPROD_MEMBER}),
        ))
        with self.assertRaisesRegex(ConfigError, "actors.*bare"):
            validate_direct_chat_cli_profile(value)

class DirectChatPlanAndRegistryTests(unittest.TestCase):
    def test_plan_freezes_the_five_dm_obligations_in_order(self):
        plan = direct_chat_cli_case_plan()
        self.assertEqual(plan["case_id"], "DUAL-DIRECT-CHAT-01")
        self.assertEqual(plan["parent_id"], PARENT)
        self.assertEqual(plan["requirement_ids"], list(DM_REQUIREMENTS))
        self.assertEqual(plan["adapter_methods"],
                         DM_METHODS + ["cancel_ping_run", "read_ping_terminal_run"])
        self.assertEqual(plan["adapter_methods"][:5], DM_METHODS)
        self.assertEqual(plan["cli_operations"], list(DIRECT_CHAT_CLI_OPERATIONS))
        self.assertEqual(len(plan["source_expectations"]), 5)
        self.assertTrue(all(isinstance(row, str) and row
                            for row in plan["source_expectations"]))

    def test_requirement_operations_map_every_dm_id(self):
        self.assertEqual(list(DIRECT_CHAT_REQUIREMENT_OPERATIONS), list(DM_REQUIREMENTS))
        for requirement, operations in DIRECT_CHAT_REQUIREMENT_OPERATIONS.items():
            for operation in operations:
                self.assertIn(operation, DIRECT_CHAT_CLI_OPERATIONS)
        self.assertEqual(list(DIRECT_CHAT_REQUIREMENT_OPERATIONS["DM.main-terminal"]),
                         [DIRECT_CHAT_MAIN_TERMINAL_OPERATION,
                          DIRECT_CHAT_PING_CANCEL_OPERATION,
                          DIRECT_CHAT_PING_TERMINAL_OPERATION])

    def test_registry_registers_the_single_dual_case(self):
        self.assertEqual(direct_chat_case_runner.CASE_KEYS, ("direct-chat",))
        self.assertEqual(set(_CASES), {"direct-chat"})
        item = _CASES["direct-chat"]
        self.assertEqual(item["case_id"], "DUAL-DIRECT-CHAT-01")
        self.assertIs(item["validator"], validate_direct_chat_cli_profile)
        self.assertIs(item["adapter"], DualDirectChatAdapter)
        self.assertIs(item["execute"], execute_direct_chat_case)
        self.assertEqual(item["fixture_effect"], DIRECT_CHAT_CLI_EFFECT)

    def test_every_reviewed_step_rides_product_cli(self):
        self.assertEqual(_HTTP_PRODUCT_OPERATIONS, frozenset())
        for operation in DIRECT_CHAT_CLI_OPERATIONS:
            self.assertEqual(expected_step_transport(operation), "PRODUCT_CLI")
            self.assertIn(operation, DIRECT_CHAT_DECLARED_ROUTES)

    def test_credential_kind_labels_are_tier_aware(self):
        self.assertEqual(direct_chat_cli_credential_kind(profile_value()),
                         "owned_local_bot_token")
        self.assertEqual(direct_chat_cli_credential_kind(profile_value(preprod=True)),
                         "human_cookie")
        self.assertEqual(direct_chat_cli_credential_kind(
            profile_value(preprod=True, auth=trio_auth())),
            "human_cookie_with_owned_bot_tokens")

    def test_adaptability_row_discloses_derived_profile_convergence(self):
        row = dual_direct_chat_adaptability()
        self.assertEqual(row["case_id"], "DUAL-DIRECT-CHAT-01")
        self.assertEqual(row["legacy_id"], PARENT)
        self.assertEqual(row["requirement_ids"], list(DM_REQUIREMENTS))
        self.assertEqual(row["environments"],
                         ["singlebox-product-cli", "preprod-product-cli"])
        self.assertEqual(row["cli_surface"], "product-bcs-cli-chat")
        self.assertEqual(row["local_executor_status"], "LOCAL_FULL_EXECUTOR")
        self.assertEqual(row["remote_validation"], "NOT_RUN")
        self.assertFalse(row["grants_release_credit"])
        self.assertIn("derived-profile WriteClient", row["blocker"])
        self.assertIn("bounded budgets", row["blocker"])
        self.assertIn("rate_limited", row["blocker"])
        self.assertIn("naturally completing ping", row["blocker"])


class DirectChatExecutionTests(unittest.TestCase):
    def adapter(self, *, preprod=False, auth=None):
        profile = preprod_profile(auth) if preprod else local_profile()
        runner = FakeChainRunner(profile)
        return DualDirectChatAdapter(profile, runner), runner

    def test_pass_chain_executes_the_dm_obligations_in_order(self):
        adapter, runner = self.adapter()
        execution = execute_direct_chat_case(adapter)
        self.assertEqual(execution.status, "PASS")
        self.assertEqual(execution.completed_count, 5)
        self.assertEqual(execution.total_count, 5)
        self.assertIsNone(execution.error_type)
        self.assertEqual([step.requirement_id for step in execution.steps],
                         list(DIRECT_CHAT_REQUIREMENTS))
        self.assertEqual([step.method for step in execution.steps], DM_METHODS)
        self.assertEqual([call[0] for call in runner.calls], OPERATIONS_BY_RUN)

    def test_chain_evidence_is_transport_and_role_annotated(self):
        adapter, _ = self.adapter()
        execution = execute_direct_chat_case(adapter)
        for step in execution.steps:
            self.assertEqual(step.status, "PASS")
            self.assertEqual(step.evidence["fixture_effect"], DIRECT_CHAT_CLI_EFFECT)
            self.assertEqual(step.evidence["remote_validation"], "NOT_RUN")
            self.assertFalse(step.evidence["grants_release_credit"])
            for record in step.evidence["requests"]:
                self.assertEqual(record["transport"], "PRODUCT_CLI")
                self.assertEqual(record["caller_role"], "driver")
                self.assertEqual(record["credential_kind"], "owned_local_bot_token")
                self.assertEqual(record["remote_validation"], "NOT_RUN")
                self.assertFalse(record["grants_release_credit"])
                self.assertEqual(record["fixture_effect"], DIRECT_CHAT_CLI_EFFECT)
                self.assertTrue(record["case_class"])
                method, path = DIRECT_CHAT_DECLARED_ROUTES[record["operation"]]
                self.assertEqual(record["http_method_declared"], method)
                self.assertEqual(record["route_declared"], path)

    def test_main_submit_reviewed_payload_and_distinct_run_identities(self):
        adapter, runner = self.adapter()
        execution = execute_direct_chat_case(adapter)
        steps = {step.requirement_id: step for step in execution.steps}
        ping_submit = steps["DM.ping-submit"].data
        main_submit = steps["DM.main-submit"].data
        self.assertEqual(ping_submit["run_id"], PING_RUN)
        self.assertEqual(ping_submit["session_id"], PING_SESSION)
        self.assertEqual(main_submit["run_id"], MAIN_RUN)
        self.assertEqual(main_submit["session_id"], MAIN_SESSION)
        self.assertEqual(main_submit["message"], MAIN_MESSAGE)
        self.assertEqual(main_submit["tags"], list(MAIN_TAGS))
        self.assertEqual(main_submit["timeout_ms"], MAIN_TIMEOUT_MS)
        self.assertTrue(main_submit["ids_distinct_from_ping"])
        ping_call = dict(runner.calls[0][1])
        self.assertEqual(ping_call["message"], PING_MESSAGE)
        self.assertEqual(ping_call["bot_uuid"], MEMBER_BOT)
        self.assertNotIn("tags", ping_call)
        self.assertNotIn("timeout_ms", ping_call)
        main_call = dict(runner.calls[1][1])
        self.assertEqual(main_call["message"], MAIN_MESSAGE)
        self.assertEqual(main_call["tags"], list(MAIN_TAGS))
        self.assertEqual(main_call["timeout_ms"], MAIN_TIMEOUT_MS)
        self.assertEqual(main_call["bot_uuid"], MEMBER_BOT)

    def test_terminal_step_carries_supplementary_ping_lifecycle(self):
        adapter, _ = self.adapter()
        execution = execute_direct_chat_case(adapter)
        terminal = execution.steps[4]
        self.assertTrue(terminal.data["terminal_version_gte_cancel_version"])
        supplementary = terminal.data["supplementary_ping_lifecycle"]
        self.assertEqual(supplementary["cancel"]["run_id"], PING_RUN)
        self.assertTrue(supplementary["cancel"]["cancelled"])
        self.assertTrue(supplementary["terminal"]["is_terminal"])
        self.assertFalse(supplementary["parent_requirement_credit"])
        self.assertEqual(len(terminal.evidence["requests"]), 3)

    def test_midchain_failure_blocks_every_dm_obligation(self):
        adapter, runner = self.adapter()
        runner.fail_at = DIRECT_CHAT_MAIN_READ_OPERATION
        execution = execute_direct_chat_case(adapter)
        self.assertEqual(execution.status, "BLOCKED")
        self.assertEqual(execution.completed_count, 0)
        self.assertEqual(execution.error_type, "RunStateError")
        for step in execution.steps:
            self.assertEqual(step.status, "NOT_RUN")
            self.assertEqual(step.error_type, "RunStateError")
            self.assertEqual(step.evidence, {})
            self.assertIsNone(step.data)

    def test_identity_and_lifecycle_guards_fail_closed(self):
        adapter, runner = self.adapter()
        runner.duplicate_ping_identity = True
        execution = execute_direct_chat_case(adapter)
        self.assertEqual(execution.status, "BLOCKED")
        self.assertEqual(execution.error_type, "RunStateError")
        adapter, runner = self.adapter()
        runner.main_terminal_version = MAIN_CANCEL_VERSION - 1
        execution = execute_direct_chat_case(adapter)
        self.assertEqual(execution.status, "BLOCKED")
        self.assertEqual(execution.error_type, "RunStateError")

    def test_non_adapter_call_results_project_blocked_not_raise(self):
        class BrokenAdapter(DualDirectChatAdapter):
            def submit_ping_run(self):
                return {"data": {}, "evidence": {}}

        adapter, _ = self.adapter()
        object.__setattr__(adapter, "__class__", BrokenAdapter)
        execution = execute_direct_chat_case(adapter)
        self.assertEqual(execution.status, "BLOCKED")
        self.assertEqual(execution.error_type, "RunStateError")
        self.assertEqual(execution.completed_count, 0)

    def test_dual_adapter_extends_the_remote_local_base(self):
        self.assertTrue(issubclass(DualDirectChatAdapter,
                                   RemoteLocalDirectChatAdapter))
        adapter, runner = self.adapter(preprod=True)
        self.assertIsInstance(adapter, DualDirectChatAdapter)
        self.assertEqual(adapter._credential_kind(), "human_cookie")
        base = RemoteLocalDirectChatAdapter(adapter.profile, runner)
        self.assertEqual(base._credential_kind(), "human_cookie")
        trio_adapter, trio_runner = self.adapter(preprod=True, auth=trio_auth())
        self.assertEqual(trio_adapter._credential_kind(),
                         "human_cookie_with_owned_bot_tokens")
        self.assertEqual(RemoteLocalDirectChatAdapter(trio_adapter.profile,
                                                      trio_runner)._credential_kind(),
                         "human_cookie")
        with self.assertRaisesRegex(RunStateError, "dual adapter"):
            execute_direct_chat_case(base)

    def test_step_evidence_gate_rejects_drifted_records(self):
        adapter, _ = self.adapter()

        def record(**overrides):
            base = {"transport": "PRODUCT_CLI", "caller_role": "driver",
                    "credential_kind": "owned_local_bot_token",
                    "remote_validation": "NOT_RUN",
                    "grants_release_credit": False,
                    "fixture_effect": DIRECT_CHAT_CLI_EFFECT}
            base.update(overrides)
            return AdapterCall(data={}, evidence=base)

        valid = _require_step_evidence(
            adapter, record(), DIRECT_CHAT_PING_SUBMIT_OPERATION)
        self.assertEqual(valid["transport"], "PRODUCT_CLI")
        for override in ({"transport": "PRODUCT_HTTP"}, {"caller_role": ""},
                         {"credential_kind": "human_cookie"},
                         {"remote_validation": "CLOSED"},
                         {"grants_release_credit": True},
                         {"fixture_effect": "OTHER_EFFECT"}):
            with self.subTest(override=override):
                with self.assertRaisesRegex(RunStateError, "evidence"):
                    _require_step_evidence(
                        adapter, record(**override),
                        DIRECT_CHAT_PING_SUBMIT_OPERATION)
        with self.assertRaisesRegex(RunStateError, "AdapterCall"):
            _require_step_evidence(adapter, {"evidence": {}},
                                   DIRECT_CHAT_PING_SUBMIT_OPERATION)


class FakeDmConvergenceWriteClient:
    """Offline WriteClient double issuing only the disclosed DM convergence GETs."""

    instances: list = []
    stage: dict = {}

    def __init__(self, profile: dict):
        self.profile = profile
        self.calls: list = []
        FakeDmConvergenceWriteClient.instances.append(self)

    def call(self, operation, *, expected=(200,), **kwargs):
        path = self.profile["route_bindings"][operation]["path"]
        header_env = self.profile["auth"].get("header_env", {})
        record = {
            "operation": operation, "method": "GET", "path": path,
            "http_status": 200, "elapsed_ms": 0.4,
            "credentials_sent": "Cookie" in header_env,
            "authorization_sent": "Authorization" in header_env,
            "traceparent_mode": "disabled",
        }
        self.calls.append({"operation": operation, "expected": expected,
                           "path": path, "record": record})
        queue = FakeDmConvergenceWriteClient.stage.get(operation, [])
        if not queue:
            raise AssertionError("unexpected convergence GET: " + operation)
        return queue.pop(0), record


class ExplodingDmConvergenceClient:
    """Guards the local Singlebox tier: it must never build an HTTP client."""

    def __init__(self, profile: dict):
        raise AssertionError(
            "local Singlebox tier must not build a convergence HTTP client")


class RemoteDirectChatConvergenceTests(unittest.TestCase):
    """Preprod DM async convergence through derived-profile WriteClient GETs."""

    CONVERGENCE = {
        "main_running_deadline_seconds": 5.0,
        "main_cancel_deadline_seconds": 5.0,
        "main_terminal_deadline_seconds": 5.0,
        "ping_terminal_deadline_seconds": 5.0,
        "poll_interval_seconds": 0.001,
    }

    def setUp(self):
        FakeDmConvergenceWriteClient.instances = []
        FakeDmConvergenceWriteClient.stage = {}

    def remote_adapter(self, *, convergence=None, auth=None):
        profile = preprod_profile(trio_auth() if auth is None else auth)
        runner = FakeChainRunner(profile)
        adapter = DualDirectChatAdapter(
            profile, runner,
            convergence=self.CONVERGENCE if convergence is None else convergence)
        return adapter, runner

    def stage(self, operation, payload):
        FakeDmConvergenceWriteClient.stage.setdefault(operation, []).append(
            payload)

    def patched(self):
        return patch.object(direct_chat_case_runner, "WriteClient",
                            FakeDmConvergenceWriteClient)

    def test_remote_async_chain_passes_with_disclosed_convergence(self):
        adapter, runner = self.remote_adapter()
        runner.remote_async = True
        profile = runner.profile
        self.stage(DIRECT_CHAT_MAIN_READ_OPERATION,
                   status_projection(profile, MAIN_RUN, MAIN_SESSION,
                                     state="running", version=8,
                                     is_terminal=False))
        self.stage(DIRECT_CHAT_MAIN_TERMINAL_OPERATION,
                   status_projection(profile, MAIN_RUN, MAIN_SESSION,
                                     state="running", version=10,
                                     is_terminal=False))
        self.stage(DIRECT_CHAT_MAIN_TERMINAL_OPERATION,
                   status_projection(profile, MAIN_RUN, MAIN_SESSION,
                                     state="cancelled", version=13,
                                     is_terminal=True))
        with self.patched():
            execution = execute_direct_chat_case(adapter)
        self.assertEqual(execution.status, "PASS")
        self.assertEqual(execution.completed_count, 5)
        self.assertIsNone(execution.error_type)
        self.assertEqual([call[0] for call in runner.calls], OPERATIONS_BY_RUN)
        for step in execution.steps:
            self.assertEqual(step.status, "PASS")
            for record in step.evidence["requests"]:
                self.assertEqual(record["transport"], "PRODUCT_CLI")
        self.assertEqual(len(FakeDmConvergenceWriteClient.instances), 1)
        client = FakeDmConvergenceWriteClient.instances[0]
        self.assertEqual(
            [recorded["operation"] for recorded in client.calls],
            [DIRECT_CHAT_MAIN_READ_OPERATION,
             DIRECT_CHAT_MAIN_TERMINAL_OPERATION,
             DIRECT_CHAT_MAIN_TERMINAL_OPERATION])
        for recorded in client.calls:
            self.assertEqual(recorded["expected"], (200,))
            self.assertEqual(recorded["record"]["method"], "GET")
            self.assertEqual(recorded["record"]["http_status"], 200)
            self.assertTrue(recorded["record"]["credentials_sent"])
            self.assertTrue(recorded["record"]["authorization_sent"])
            self.assertEqual(recorded["record"]["traceparent_mode"],
                             "disabled")
        self.assertEqual(
            client.profile["route_bindings"][DIRECT_CHAT_MAIN_READ_OPERATION]["path"],
            "/chat/runs/" + MAIN_RUN)
        self.assertEqual(
            client.profile["route_bindings"][DIRECT_CHAT_MAIN_TERMINAL_OPERATION]["path"],
            "/chat/runs/" + MAIN_RUN)
        self.assertEqual(
            client.profile["route_bindings"][DIRECT_CHAT_MAIN_CANCEL_OPERATION]["path"],
            "/chat/runs/" + MAIN_RUN + "/cancel")
        auth = client.profile["auth"]
        self.assertEqual(auth["kind"], "human-cookie-with-owned-bot-tokens")
        self.assertEqual(auth["header_env"],
                         {"Cookie": COOKIE_ENV, "Authorization": DRIVER_TOKEN_ENV})
        self.assertEqual(auth["authorization_scheme"], "bearer")
        self.assertEqual(client.profile["actors"], profile["actors"])
        self.assertEqual(client.profile["entrypoint"], profile["entrypoint"])
        running = adapter.main_running_convergence
        self.assertEqual(running["polls"], 1)
        self.assertEqual(running["version_first"], 4)
        self.assertEqual(running["version_last"], 8)
        self.assertEqual(running["ack_state"], "pending")
        self.assertEqual(running["transport"], "PRODUCT_HTTP")
        self.assertEqual(running["method"], "GET")
        self.assertEqual(running["path_template"], "/chat/runs/{run_id}")
        self.assertEqual(running["deadline_seconds"], 5.0)
        self.assertEqual(running["poll_interval_seconds"], 0.001)
        self.assertEqual(running["version_regressions"], 0)
        self.assertEqual(running["http_status_sequence"], [200])
        self.assertEqual(running["http_records"][0]["path"],
                         "/chat/runs/" + MAIN_RUN)
        self.assertEqual(running["final_state"], "running")
        self.assertFalse(running["final_is_terminal"])
        cancelling = adapter.main_cancel_convergence
        self.assertEqual(cancelling["polls"], 2)
        self.assertEqual(cancelling["version_first"], MAIN_CANCEL_VERSION)
        self.assertEqual(cancelling["version_last"], 13)
        self.assertEqual(cancelling["final_state"], "cancelled")
        self.assertEqual(cancelling["http_status_sequence"], [200, 200])
        self.assertEqual(adapter.main_terminal_convergence["polls"], 0)
        self.assertEqual(adapter.ping_terminal_convergence["polls"], 0)
        self.assertEqual(adapter.main_cancel_version, MAIN_CANCEL_VERSION)
        self.assertEqual(adapter.ping_cancel_version, PING_CANCEL_VERSION)
        self.assertFalse(adapter.ping_ack_cancelled)
        self.assertEqual(adapter.ping_ack_state, "running")
        self.assertEqual(adapter.ping_terminal_state, "completed")
        self.assertTrue(adapter.ping_natural_completion)
        steps = {step.method: step for step in execution.steps}
        read_data = steps["read_main_run"].data
        self.assertEqual(read_data["main_running_convergence"]["polls"], 1)
        cancel_data = steps["cancel_main_run"].data
        self.assertFalse(cancel_data["main_cancel_ack_cancelled"])
        self.assertEqual(cancel_data["main_cancel_ack_state"], "running")
        self.assertEqual(cancel_data["state"], "cancelled")
        self.assertEqual(cancel_data["version"], 13)
        self.assertEqual(cancel_data["main_cancel_convergence"]["polls"], 2)
        terminal = steps["read_main_terminal_run"]
        self.assertEqual(terminal.data["main_terminal_convergence"]["polls"], 0)
        self.assertEqual(terminal.data["main_terminal_convergence"]["ack_state"],
                         "cancelled")
        self.assertEqual(len(terminal.evidence["requests"]), 3)
        supplementary = terminal.data["supplementary_ping_lifecycle"]
        self.assertEqual(supplementary["cancel"]["run_id"], PING_RUN)
        self.assertFalse(supplementary["cancel"]["cancelled"])
        self.assertEqual(supplementary["cancel"]["state"], "running")
        self.assertEqual(supplementary["terminal"]["state"], "completed")
        self.assertEqual(supplementary["terminal"]["response"]["content"],
                         "pong")
        self.assertEqual(supplementary["terminal"]["ping_terminal_state"],
                         "completed")
        self.assertTrue(supplementary["terminal"]["ping_natural_completion"])
        self.assertTrue(
            supplementary["terminal"]["ping_cancel_preceeded_by_completion"])
        self.assertFalse(supplementary["parent_requirement_credit"])
        lifecycle = adapter.ping_lifecycle()
        self.assertEqual(lifecycle["main_running_convergence_polls"], 1)
        self.assertEqual(lifecycle["main_cancel_convergence_polls"], 2)
        self.assertEqual(lifecycle["main_cancel_terminal_state"], "cancelled")
        self.assertEqual(lifecycle["main_terminal_convergence_polls"], 0)
        self.assertEqual(lifecycle["ping_terminal_convergence_polls"], 0)
        self.assertFalse(lifecycle["ping_ack_cancelled"])
        self.assertEqual(lifecycle["ping_ack_state"], "running")
        self.assertEqual(lifecycle["ping_terminal_state"], "completed")
        self.assertTrue(lifecycle["ping_natural_completion"])

    def test_remote_ping_terminal_lag_converges_on_its_own_client(self):
        adapter, runner = self.remote_adapter()
        runner.remote_async = True
        runner.ping_terminal_pending = True
        profile = runner.profile
        self.stage(DIRECT_CHAT_MAIN_READ_OPERATION,
                   status_projection(profile, MAIN_RUN, MAIN_SESSION,
                                     state="running", version=8,
                                     is_terminal=False))
        self.stage(DIRECT_CHAT_MAIN_TERMINAL_OPERATION,
                   status_projection(profile, MAIN_RUN, MAIN_SESSION,
                                     state="cancelled", version=13,
                                     is_terminal=True))
        self.stage(DIRECT_CHAT_PING_TERMINAL_OPERATION,
                   status_projection(profile, PING_RUN, PING_SESSION,
                                     state="completed",
                                     version=PING_TERMINAL_VERSION,
                                     is_terminal=True, content="pong"))
        with self.patched():
            execution = execute_direct_chat_case(adapter)
        self.assertEqual(execution.status, "PASS")
        self.assertEqual(len(FakeDmConvergenceWriteClient.instances), 2)
        main_client, ping_client = FakeDmConvergenceWriteClient.instances
        self.assertEqual([recorded["operation"] for recorded in main_client.calls],
                         [DIRECT_CHAT_MAIN_READ_OPERATION,
                          DIRECT_CHAT_MAIN_TERMINAL_OPERATION])
        self.assertEqual([recorded["operation"] for recorded in ping_client.calls],
                         [DIRECT_CHAT_PING_TERMINAL_OPERATION])
        self.assertEqual(
            ping_client.profile["route_bindings"][
                DIRECT_CHAT_PING_TERMINAL_OPERATION]["path"],
            "/chat/runs/" + PING_RUN)
        convergence = adapter.ping_terminal_convergence
        self.assertGreaterEqual(convergence["polls"], 1)
        self.assertEqual(convergence["version_first"], PING_CANCEL_VERSION)
        self.assertEqual(convergence["version_last"], PING_TERMINAL_VERSION)
        self.assertEqual(convergence["final_state"], "completed")
        self.assertEqual(adapter.ping_terminal_state, "completed")
        self.assertTrue(adapter.ping_natural_completion)
        lifecycle = adapter.ping_lifecycle()
        self.assertGreaterEqual(lifecycle["ping_terminal_convergence_polls"], 1)

    def test_remote_running_budget_exhaustion_blocks_the_case(self):
        convergence = {"main_running_deadline_seconds": 0.05,
                       "poll_interval_seconds": 0.001}
        adapter, runner = self.remote_adapter(convergence=convergence)
        runner.remote_async = True
        profile = runner.profile
        for _ in range(400):
            self.stage(DIRECT_CHAT_MAIN_READ_OPERATION,
                       status_projection(profile, MAIN_RUN, MAIN_SESSION,
                                         state="pending", version=4,
                                         is_terminal=False))
        with self.patched():
            execution = execute_direct_chat_case(adapter)
        self.assertEqual(execution.status, "BLOCKED")
        self.assertEqual(execution.error_type, "RunStateError")
        self.assertEqual(execution.completed_count, 0)
        for step in execution.steps:
            self.assertEqual(step.status, "NOT_RUN")
            self.assertEqual(step.evidence, {})
            self.assertIsNone(step.data)
        self.assertEqual(len(FakeDmConvergenceWriteClient.instances), 1)
        polls = len(FakeDmConvergenceWriteClient.instances[0].calls)
        self.assertGreater(polls, 10)
        adapter, runner = self.remote_adapter(convergence=convergence)
        runner.remote_async = True
        adapter.ping_run_id = PING_RUN
        adapter.ping_session_id = PING_SESSION
        adapter.main_run_id = MAIN_RUN
        adapter.main_session_id = MAIN_SESSION
        with self.patched():
            with self.assertRaisesRegex(
                    RunStateError, "did not reach the running state"):
                adapter.read_main_run()

    def test_remote_cancel_converging_to_completed_blocks(self):
        adapter, runner = self.remote_adapter()
        runner.remote_async = True
        with self.patched():
            adapter.submit_ping_run()
            adapter.submit_main_run()
            self.stage(DIRECT_CHAT_MAIN_TERMINAL_OPERATION,
                       status_projection(runner.profile, MAIN_RUN, MAIN_SESSION,
                                         state="completed", version=13,
                                         is_terminal=True))
            with self.assertRaisesRegex(
                    RunStateError, "non-cancelled terminal"):
                adapter.cancel_main_run()

    def test_remote_ping_terminal_wrong_pong_content_blocks(self):
        adapter, runner = self.remote_adapter()
        runner.remote_async = True
        runner.ping_terminal_pending = True
        profile = runner.profile
        self.stage(DIRECT_CHAT_MAIN_READ_OPERATION,
                   status_projection(profile, MAIN_RUN, MAIN_SESSION,
                                     state="running", version=8,
                                     is_terminal=False))
        self.stage(DIRECT_CHAT_MAIN_TERMINAL_OPERATION,
                   status_projection(profile, MAIN_RUN, MAIN_SESSION,
                                     state="cancelled", version=13,
                                     is_terminal=True))
        self.stage(DIRECT_CHAT_PING_TERMINAL_OPERATION,
                   status_projection(profile, PING_RUN, PING_SESSION,
                                     state="completed",
                                     version=PING_TERMINAL_VERSION,
                                     is_terminal=True, content="not-pong"))
        with self.patched():
            adapter.submit_ping_run()
            adapter.submit_main_run()
            adapter.read_main_run()
            adapter.cancel_main_run()
            adapter.read_main_terminal_run()
            adapter.cancel_ping_run()
            with self.assertRaisesRegex(
                    RunStateError, "pong terminal projection"):
                adapter.read_ping_terminal_run()

    def test_local_tier_never_builds_a_convergence_http_client(self):
        profile = local_profile()
        runner = FakeChainRunner(profile)
        adapter = DualDirectChatAdapter(
            profile, runner, convergence=self.CONVERGENCE)
        with patch.object(direct_chat_case_runner, "WriteClient",
                          ExplodingDmConvergenceClient):
            execution = execute_direct_chat_case(adapter)
        self.assertEqual(execution.status, "PASS")
        self.assertEqual([call[0] for call in runner.calls], OPERATIONS_BY_RUN)
        self.assertIsNone(adapter.ping_terminal_state)
        self.assertFalse(adapter.ping_natural_completion)
        lifecycle = adapter.ping_lifecycle()
        self.assertEqual(lifecycle["ping_run_id"], PING_RUN)
        self.assertEqual(lifecycle["main_run_id"], MAIN_RUN)
        self.assertNotIn("main_running_convergence_polls", lifecycle)
        self.assertNotIn("ping_terminal_convergence_polls", lifecycle)
        self.assertNotIn("ping_terminal_state", lifecycle)

    def test_cookie_only_remote_profile_derives_cookie_header_only(self):
        adapter, _ = self.remote_adapter(
            auth={"kind": "human-cookie", "cookie_env": COOKIE_ENV})
        derived = adapter._dm_convergence_profile(MAIN_RUN)
        self.assertEqual(derived["auth"]["kind"], "human-cookie")
        self.assertEqual(derived["auth"]["header_env"],
                         {"Cookie": COOKIE_ENV})
        self.assertNotIn("Authorization", derived["auth"]["header_env"])
        self.assertNotIn("authorization_scheme", derived["auth"])
        for operation in (DIRECT_CHAT_MAIN_READ_OPERATION,
                          DIRECT_CHAT_MAIN_TERMINAL_OPERATION,
                          DIRECT_CHAT_PING_TERMINAL_OPERATION):
            self.assertEqual(
                derived["route_bindings"][operation]["path"],
                "/chat/runs/" + MAIN_RUN)
        for operation in (DIRECT_CHAT_MAIN_CANCEL_OPERATION,
                          DIRECT_CHAT_PING_CANCEL_OPERATION):
            self.assertEqual(
                derived["route_bindings"][operation]["path"],
                "/chat/runs/" + MAIN_RUN + "/cancel")
        self.assertEqual(derived["entrypoint"], adapter.profile["entrypoint"])
        self.assertEqual(derived["actors"], adapter.profile["actors"])

    def test_invalid_convergence_budget_fails_closed(self):
        adapter, runner = self.remote_adapter(
            convergence={"main_running_deadline_seconds": 5.0,
                         "main_cancel_deadline_seconds": 5.0,
                         "main_terminal_deadline_seconds": 5.0,
                         "ping_terminal_deadline_seconds": 5.0,
                         "poll_interval_seconds": 0})
        runner.remote_async = True
        with self.patched():
            adapter.submit_ping_run()
            adapter.submit_main_run()
            with self.assertRaisesRegex(
                    RunStateError, "convergence budget is invalid"):
                adapter.read_main_run()


if __name__ == "__main__":
    unittest.main()
