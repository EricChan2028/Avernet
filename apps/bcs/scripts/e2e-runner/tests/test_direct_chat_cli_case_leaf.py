"""Offline launcher-envelope tests for the Direct-chat CLI leaf runner."""

from __future__ import annotations

from contextlib import contextmanager, nullcontext
import hashlib
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from bcs_e2e import direct_chat_cli_case_leaf as direct_chat_cli_leaf
from bcs_e2e.direct_chat_cli_case_leaf import (
    DIRECT_CHAT_MAIN_CANCEL_OPERATION,
    DIRECT_CHAT_MAIN_READ_OPERATION,
    DIRECT_CHAT_MAIN_SUBMIT_OPERATION,
    DIRECT_CHAT_MAIN_TERMINAL_OPERATION,
    DIRECT_CHAT_PING_CANCEL_OPERATION,
    DIRECT_CHAT_PING_SUBMIT_OPERATION,
    DIRECT_CHAT_PING_TERMINAL_OPERATION,
    DirectChatCliRunner,
    DirectChatCliRunnerError,
)

ROOT = Path(__file__).resolve().parents[1]
TEST_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(TEST_ROOT))
sys.path.insert(0, str(ROOT))

from test_direct_chat_cli_case_run import (
    COOKIE_ENV,
    DRIVER_BOT,
    DRIVER_TOKEN,
    DRIVER_TOKEN_ENV,
    HUMAN_COOKIE,
    MAIN_CANCEL_VERSION,
    MAIN_MESSAGE,
    MAIN_RUN,
    MAIN_SESSION,
    MAIN_TAGS,
    MAIN_TIMEOUT_MS,
    MAIN_TERMINAL_VERSION,
    MEMBER_BOT,
    MEMBER_TOKEN,
    MEMBER_TOKEN_ENV,
    PING_CANCEL_VERSION,
    PING_MESSAGE,
    PING_RUN,
    PING_SESSION,
    PING_TERMINAL_VERSION,
    PREPROD_DRIVER,
    PREPROD_MEMBER,
    bytes_json,
    cancel_projection,
    completed,
    local_profile,
    preprod_profile,
    status_projection,
    submit_projection,
    trio_auth,
)

_NULL = nullcontext()


class DirectChatCliRunnerTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(self.root, ignore_errors=True))
        self.cli_binary = self.root / "bcs-cli"
        self.cli_binary.write_bytes(b"#!/bin/sh\nexit 0\n")
        self.cli_binary.chmod(0o700)
        self.cli_sha256 = hashlib.sha256(self.cli_binary.read_bytes()).hexdigest()
        self.state_dir = self.root / "state"
        self.state_dir.mkdir()
        self.launcher_base = self.root / "launcher-home"
        self.launcher_base.mkdir()
        self.profile = local_profile()
        self.runner_profile = preprod_profile()

    def fresh(self, profile=None):
        return DirectChatCliRunner(
            self.profile if profile is None else profile,
            binary=self.cli_binary, binary_sha256=self.cli_sha256,
            state_dir=self.state_dir)

    @contextmanager
    def leaf(self, *, profile=None, token=DRIVER_TOKEN, cookie=None,
             member_token=None, side_effect=None, returncode=0, stdout=b"",
             keep_root=False, docker_path=None, docker_host=False,
             clear_env=False):
        """Patch launcher base, capture, secrets and docker discovery together."""
        profile = self.profile if profile is None else profile
        if clear_env:
            environment = {}
        else:
            environment = {}
            if token is not None:
                environment[DRIVER_TOKEN_ENV] = token
            if cookie is not None:
                environment[COOKIE_ENV] = cookie
            if member_token is not None:
                environment[MEMBER_TOKEN_ENV] = member_token
            if docker_host:
                environment["DOCKER_HOST"] = "tcp://docker.invalid:2375"
        with patch.object(DirectChatCliRunner, "_LAUNCHER_ROOT_BASE",
                          self.launcher_base), \
             patch.dict(os.environ, environment, clear=clear_env), \
             patch("bcs_e2e.direct_chat_cli_case_leaf.shutil.which",
                   return_value=docker_path), \
             patch("bcs_e2e.direct_chat_cli_case_leaf.shutil.rmtree",
                   lambda *args, **kwargs: None) if keep_root else _NULL, \
             patch("bcs_e2e.direct_chat_cli_case_leaf.capture") as mock_capture:
            if side_effect is None:
                mock_capture.return_value = completed(returncode, stdout)
            else:
                mock_capture.side_effect = side_effect
            yield mock_capture

    def test_submit_leaf_argv_is_the_reviewed_chat_form(self):
        with self.leaf(token=DRIVER_TOKEN, side_effect=[
                completed(0, bytes_json(submit_projection(self.profile, PING_RUN,
                                                          PING_SESSION))),
                completed(0, bytes_json(submit_projection(self.profile, MAIN_RUN,
                                                          MAIN_SESSION)))]) as capture:
            runner = self.fresh()
            runner.execute(DIRECT_CHAT_PING_SUBMIT_OPERATION, message=PING_MESSAGE,
                           bot_uuid=MEMBER_BOT)
            runner.execute(DIRECT_CHAT_MAIN_SUBMIT_OPERATION, message=MAIN_MESSAGE,
                           tags=list(MAIN_TAGS), timeout_ms=MAIN_TIMEOUT_MS,
                           bot_uuid=MEMBER_BOT)
        ping_argv = list(capture.call_args_list[0][0][0])
        main_argv = list(capture.call_args_list[1][0][0])
        resolved = str(self.cli_binary.resolve())
        self.assertEqual(ping_argv, [
            resolved, "--url", "http://127.0.0.1:21000", "--json",
            "--log-level", "error", "chat", "--bot-uuid", MEMBER_BOT,
            "--message", PING_MESSAGE, "--detach"])
        self.assertEqual(main_argv, [
            resolved, "--url", "http://127.0.0.1:21000", "--json",
            "--log-level", "error", "chat", "--bot-uuid", MEMBER_BOT,
            "--message", MAIN_MESSAGE, "--timeout-ms", "60000", "--tag", "e2e",
            "--tag", "release", "--detach"])
        self.assertEqual(capture.call_count, 2)

    def test_status_and_cancel_leaf_argvs(self):
        with self.leaf(token=DRIVER_TOKEN, side_effect=[
                completed(0, bytes_json(submit_projection(self.profile, PING_RUN,
                                                          PING_SESSION))),
                completed(0, bytes_json(submit_projection(self.profile, MAIN_RUN,
                                                          MAIN_SESSION))),
                completed(0, bytes_json(status_projection(
                    self.profile, MAIN_RUN, MAIN_SESSION, state="running",
                    version=5, is_terminal=False))),
                completed(0, bytes_json(cancel_projection(
                    MAIN_RUN, MAIN_CANCEL_VERSION)))]) as capture:
            runner = self.fresh()
            runner.execute(DIRECT_CHAT_PING_SUBMIT_OPERATION, message=PING_MESSAGE,
                           bot_uuid=MEMBER_BOT)
            runner.execute(DIRECT_CHAT_MAIN_SUBMIT_OPERATION, message=MAIN_MESSAGE,
                           tags=list(MAIN_TAGS), timeout_ms=MAIN_TIMEOUT_MS,
                           bot_uuid=MEMBER_BOT)
            runner.execute(DIRECT_CHAT_MAIN_READ_OPERATION, run_id=MAIN_RUN)
            runner.execute(DIRECT_CHAT_MAIN_CANCEL_OPERATION, run_id=MAIN_RUN)
        read_argv = list(capture.call_args_list[2][0][0])
        cancel_argv = list(capture.call_args_list[3][0][0])
        prefix = [str(self.cli_binary.resolve()), "--url", "http://127.0.0.1:21000",
                  "--json", "--log-level", "error"]
        self.assertEqual(read_argv, prefix + ["chat-run", "status", "--run-id",
                                              MAIN_RUN])
        self.assertEqual(cancel_argv, prefix + ["chat-run", "cancel", "--run-id",
                                                MAIN_RUN])

    def test_local_context_uses_owned_driver_token_and_local_server_env(self):
        journals: list = []
        real_write = direct_chat_cli_leaf.write

        def record_write(path, data):
            journals.append((str(path), data))
            return real_write(path, data)

        with self.leaf(token=DRIVER_TOKEN,
                       stdout=bytes_json(submit_projection(self.profile, PING_RUN,
                                                           PING_SESSION))) as capture, \
             patch.object(direct_chat_cli_leaf, "write", record_write):
            runner = self.fresh()
            runner.execute(DIRECT_CHAT_PING_SUBMIT_OPERATION, message=PING_MESSAGE,
                           bot_uuid=MEMBER_BOT)
            environment = capture.call_args.kwargs["environment"]
            cwd = Path(capture.call_args.kwargs["cwd"])
        session_writes = [data for path, data in journals
                          if path.endswith("session.json")]
        self.assertEqual(session_writes[0], {"token": DRIVER_TOKEN,
                                             "bot_uuid": DRIVER_BOT,
                                             "api_base_url":
                                                 "http://127.0.0.1:21000"})
        self.assertEqual(session_writes[-1], {"token": "", "bot_uuid": "",
                                              "api_base_url": ""})
        self.assertEqual(environment["SERVER_ENV"], "local")
        self.assertEqual(environment["tc_sdb_nenv"], "production")
        self.assertNotIn(COOKIE_ENV, environment)
        self.assertFalse(cwd.exists())

    def test_preprod_cookie_context_journals_no_token(self):
        cookie_profile = preprod_profile()
        journals: list = []
        real_write = direct_chat_cli_leaf.write

        def record_write(path, data):
            journals.append((str(path), data))
            return real_write(path, data)

        with self.leaf(profile=cookie_profile, token=None, cookie=HUMAN_COOKIE,
                       stdout=bytes_json(submit_projection(cookie_profile, PING_RUN,
                                                           PING_SESSION))) as capture, \
             patch.object(direct_chat_cli_leaf, "write", record_write):
            runner = self.fresh(cookie_profile)
            runner.execute(DIRECT_CHAT_PING_SUBMIT_OPERATION, message=PING_MESSAGE,
                           bot_uuid=PREPROD_MEMBER)
            environment = capture.call_args.kwargs["environment"]
            cwd = Path(capture.call_args.kwargs["cwd"])
        session_writes = [data for path, data in journals
                          if path.endswith("session.json")]
        self.assertEqual(session_writes[0], {"token": "",
                                             "bot_uuid": PREPROD_DRIVER,
                                             "api_base_url":
                                                 "https://bcs-pre.alipay.com"})
        self.assertEqual(session_writes[-1], {"token": "", "bot_uuid": "",
                                              "api_base_url": ""})
        self.assertNotIn("SERVER_ENV", environment)
        self.assertEqual(environment["BCS_COOKIE"], HUMAN_COOKIE)
        self.assertEqual(environment["tc_sdb_nenv"], "production")
        self.assertFalse(cwd.exists())
        self.assertNotIn(HUMAN_COOKIE, json.dumps(journals))

    def test_launcher_envelope_widens_mounts_and_forwards_docker(self):
        with self.leaf(token=DRIVER_TOKEN, keep_root=True,
                       docker_path="/opt/custom/bin/docker", docker_host=True,
                       stdout=bytes_json(submit_projection(self.profile, PING_RUN,
                                                           PING_SESSION))) as capture:
            runner = self.fresh()
            with self.assertRaisesRegex(DirectChatCliRunnerError,
                                        "cleanup is unconfirmed"):
                runner.execute(DIRECT_CHAT_PING_SUBMIT_OPERATION,
                               message=PING_MESSAGE, bot_uuid=MEMBER_BOT)
            environment = capture.call_args.kwargs["environment"]
            cwd = Path(capture.call_args.kwargs["cwd"])
            self.assertTrue(environment["PATH"].startswith("/opt/custom/bin:"))
            self.assertEqual(environment["DOCKER_HOST"],
                             "tcp://docker.invalid:2375")
            self.assertEqual(environment["HOME"], str(cwd / "bot"))
            self.assertEqual(environment["BOT_DATA_DIR"], str(cwd / "bot"))
            self.assertEqual(environment["TMPDIR"], str(cwd / "tmp"))
            self.assertEqual(environment["LANG"], "C")
            for relative in ("home", "bot", "tmp"):
                self.assertEqual((cwd / relative).stat().st_mode & 0o777, 0o777)
            for relative in ("home/.bcs", "home/.bcs/logs", "home/.bcs-mktmp",
                             "bot", "bot/.bcs", "bot/.bcs/logs", "bot/.bcs-mktmp"):
                self.assertEqual((cwd / relative).stat().st_mode & 0o777, 0o777,
                                 relative)
            # the cleanup zero-write re-restricts the private journal
            self.assertEqual((cwd / "bot" / ".bcs" / "session.json"
                              ).stat().st_mode & 0o777, 0o600)
        shutil.rmtree(cwd)

    def test_cleanup_zeroes_the_private_context_before_flagging_unconfirmed(self):
        with self.leaf(token=DRIVER_TOKEN, keep_root=True,
                       stdout=bytes_json(submit_projection(self.profile, PING_RUN,
                                                           PING_SESSION))) as capture:
            runner = self.fresh()
            with self.assertRaisesRegex(DirectChatCliRunnerError,
                                        "cleanup is unconfirmed"):
                runner.execute(DIRECT_CHAT_PING_SUBMIT_OPERATION,
                               message=PING_MESSAGE, bot_uuid=MEMBER_BOT)
            cwd = Path(capture.call_args.kwargs["cwd"])
            context = json.loads(
                (cwd / "bot" / ".bcs" / "session.json").read_text())
        self.assertEqual(context, {"token": "", "bot_uuid": "", "api_base_url": ""})
        self.assertTrue(cwd.exists())
        shutil.rmtree(cwd)

    def test_stale_launcher_roots_are_swept_on_construction(self):
        stale = self.launcher_base / "runner-deadbeefdeadbeef"
        stale.mkdir()
        (stale / "bot").mkdir()
        fresh = self.launcher_base / "runner-feedfacefeedface"
        fresh.mkdir()
        os.utime(stale, (1700000000, 1700000000))
        os.utime(fresh, (time.time() + 60, time.time() + 60))
        with patch.object(DirectChatCliRunner, "_LAUNCHER_ROOT_BASE",
                          self.launcher_base):
            self.fresh()
        self.assertFalse(stale.exists())
        self.assertTrue(fresh.exists())

    def test_nonzero_exit_surfaces_the_first_stdout_detail(self):
        with self.leaf(token=DRIVER_TOKEN, returncode=127,
                       stdout=b"boom-detail-exit-127"):
            runner = self.fresh()
            with self.assertRaisesRegex(DirectChatCliRunnerError,
                                        "nonzero") as ctx:
                runner.execute(DIRECT_CHAT_PING_SUBMIT_OPERATION,
                               message=PING_MESSAGE, bot_uuid=MEMBER_BOT)
        self.assertIn("boom-detail-exit-127", str(ctx.exception))

    def test_credential_leak_and_invalid_json_fail_closed(self):
        cases = ((bytes_json(submit_projection(self.profile, PING_RUN,
                                               PING_SESSION)).replace(
                     b'"submitted"', DRIVER_TOKEN.encode()), "credential"),
                 (b"not a json value", "one JSON value"),
                 (b'["list", "not", "object"]', "object"))
        for stdout, where in cases:
            with self.subTest(where=where):
                with self.leaf(token=DRIVER_TOKEN, stdout=stdout):
                    runner = self.fresh()
                    with self.assertRaisesRegex(DirectChatCliRunnerError,
                                                "credential|JSON|object"):
                        runner.execute(DIRECT_CHAT_PING_SUBMIT_OPERATION,
                                       message=PING_MESSAGE, bot_uuid=MEMBER_BOT)

    def test_projection_drift_is_rejected(self):
        good_submit = submit_projection(self.profile, PING_RUN, PING_SESSION)
        for stdout, where in (
                (bytes_json({k: v for k, v in good_submit.items()
                             if k != "run_id"}), "submit projection"),
                (bytes_json({**good_submit, "submitted": False}), "submit admission"),
                (bytes_json({**good_submit, "bot_uuid": "other-bot"}),
                 "submit PM target")):
            with self.subTest(where=where):
                with self.leaf(token=DRIVER_TOKEN, stdout=stdout):
                    runner = self.fresh()
                    with self.assertRaisesRegex(DirectChatCliRunnerError,
                                                "projection|admission|target"):
                        runner.execute(DIRECT_CHAT_PING_SUBMIT_OPERATION,
                                       message=PING_MESSAGE, bot_uuid=MEMBER_BOT)
        for operation, stdout, where in (
                (DIRECT_CHAT_MAIN_READ_OPERATION, bytes_json(status_projection(
                    self.profile, MAIN_RUN, MAIN_SESSION, state="RUNNING",
                    version=5, is_terminal=False)), "status projection"),
                (DIRECT_CHAT_MAIN_CANCEL_OPERATION, bytes_json(
                    {**cancel_projection(MAIN_RUN, 1), "cancelled": "yes"}),
                 "cancel projection")):
            with self.subTest(where=where):
                side_effect = [
                    completed(0, bytes_json(submit_projection(
                        self.profile, PING_RUN, PING_SESSION))),
                    completed(0, bytes_json(submit_projection(
                        self.profile, MAIN_RUN, MAIN_SESSION))),
                ]
                if operation == DIRECT_CHAT_MAIN_CANCEL_OPERATION:
                    side_effect.append(completed(0, bytes_json(status_projection(
                        self.profile, MAIN_RUN, MAIN_SESSION, state="running",
                        version=5, is_terminal=False))))
                side_effect.append(completed(0, stdout))
                with self.leaf(token=DRIVER_TOKEN,
                               side_effect=side_effect) as capture:
                    runner = self.fresh()
                    runner.execute(DIRECT_CHAT_PING_SUBMIT_OPERATION,
                                   message=PING_MESSAGE, bot_uuid=MEMBER_BOT)
                    runner.execute(DIRECT_CHAT_MAIN_SUBMIT_OPERATION,
                                   message=MAIN_MESSAGE, tags=list(MAIN_TAGS),
                                   timeout_ms=MAIN_TIMEOUT_MS,
                                   bot_uuid=MEMBER_BOT)
                    if operation == DIRECT_CHAT_MAIN_CANCEL_OPERATION:
                        runner.execute(DIRECT_CHAT_MAIN_READ_OPERATION,
                                       run_id=MAIN_RUN)
                    with self.assertRaisesRegex(DirectChatCliRunnerError,
                                                "projection|differs"):
                        runner.execute(operation, run_id=MAIN_RUN)
                    self.assertEqual(capture.call_count, len(side_effect))

    def test_cancel_leaf_admits_asynchronous_ack_shapes(self):
        # The station acknowledges cancel before the downstream abort
        # converges; the CLI help allows an unconfirmed abort.  Shape-valid
        # acks with cancelled=false are admitted here; terminal convergence
        # is the dual adapter's remote-tier obligation.
        for state in ("running", "cancelled", "completed", "pending"):
            payload = {**cancel_projection(MAIN_RUN, 9), "cancelled": False,
                       "state": state}
            with self.subTest(state=state):
                side_effect = [
                    completed(0, bytes_json(submit_projection(
                        self.profile, PING_RUN, PING_SESSION))),
                    completed(0, bytes_json(submit_projection(
                        self.profile, MAIN_RUN, MAIN_SESSION))),
                    completed(0, bytes_json(status_projection(
                        self.profile, MAIN_RUN, MAIN_SESSION, state="running",
                        version=5, is_terminal=False))),
                    completed(0, bytes_json(payload)),
                ]
                with self.leaf(token=DRIVER_TOKEN,
                               side_effect=side_effect) as capture:
                    runner = self.fresh()
                    runner.execute(DIRECT_CHAT_PING_SUBMIT_OPERATION,
                                   message=PING_MESSAGE, bot_uuid=MEMBER_BOT)
                    runner.execute(DIRECT_CHAT_MAIN_SUBMIT_OPERATION,
                                   message=MAIN_MESSAGE, tags=list(MAIN_TAGS),
                                   timeout_ms=MAIN_TIMEOUT_MS,
                                   bot_uuid=MEMBER_BOT)
                    runner.execute(DIRECT_CHAT_MAIN_READ_OPERATION,
                                   run_id=MAIN_RUN)
                    data = runner.execute(DIRECT_CHAT_MAIN_CANCEL_OPERATION,
                                          run_id=MAIN_RUN)["data"]
                    self.assertEqual(data["cancelled"], False)
                    self.assertEqual(data["state"], state)
                self.assertEqual(capture.call_count, len(side_effect))

    def test_cancel_leaf_still_rejects_invalid_ack_shapes(self):
        for mutate, where in (
                ({"cancelled": "yes"}, "boolean cancelled"),
                ({"state": "exploded"}, "state vocabulary"),
                ({"version": 0}, "positive version"),
                ({"response": "not-a-dict"}, "response object")):
            payload = {**cancel_projection(MAIN_RUN, 9), **mutate}
            with self.subTest(where=where):
                side_effect = [
                    completed(0, bytes_json(submit_projection(
                        self.profile, PING_RUN, PING_SESSION))),
                    completed(0, bytes_json(submit_projection(
                        self.profile, MAIN_RUN, MAIN_SESSION))),
                    completed(0, bytes_json(status_projection(
                        self.profile, MAIN_RUN, MAIN_SESSION, state="running",
                        version=5, is_terminal=False))),
                    completed(0, bytes_json(payload)),
                ]
                with self.leaf(token=DRIVER_TOKEN,
                               side_effect=side_effect):
                    runner = self.fresh()
                    runner.execute(DIRECT_CHAT_PING_SUBMIT_OPERATION,
                                   message=PING_MESSAGE, bot_uuid=MEMBER_BOT)
                    runner.execute(DIRECT_CHAT_MAIN_SUBMIT_OPERATION,
                                   message=MAIN_MESSAGE, tags=list(MAIN_TAGS),
                                   timeout_ms=MAIN_TIMEOUT_MS,
                                   bot_uuid=MEMBER_BOT)
                    runner.execute(DIRECT_CHAT_MAIN_READ_OPERATION,
                                   run_id=MAIN_RUN)
                    with self.assertRaisesRegex(DirectChatCliRunnerError,
                                                "projection|differs"):
                        runner.execute(DIRECT_CHAT_MAIN_CANCEL_OPERATION,
                                       run_id=MAIN_RUN)

    def test_status_leaf_admits_station_extras_and_queue_states(self):
        # Station status payloads carry delivery/completion bookkeeping
        # beyond the reviewed subset contract; queue states are lawful.
        payload = {**status_projection(
            self.profile, MAIN_RUN, MAIN_SESSION, state="pending",
            version=4, is_terminal=False),
            "completed_at_ms": 1735689600003,
            "delivery": {"delivery_id": "delivery-e2e",
                         "message_id": "message-e2e", "state_version": 5,
                         "status": "queued", "wait_reason": "rate_limited"}}
        side_effect = [
            completed(0, bytes_json(submit_projection(
                self.profile, PING_RUN, PING_SESSION))),
            completed(0, bytes_json(submit_projection(
                self.profile, MAIN_RUN, MAIN_SESSION))),
            completed(0, bytes_json(payload)),
        ]
        with self.leaf(token=DRIVER_TOKEN, side_effect=side_effect):
            runner = self.fresh()
            runner.execute(DIRECT_CHAT_PING_SUBMIT_OPERATION,
                           message=PING_MESSAGE, bot_uuid=MEMBER_BOT)
            runner.execute(DIRECT_CHAT_MAIN_SUBMIT_OPERATION,
                           message=MAIN_MESSAGE, tags=list(MAIN_TAGS),
                           timeout_ms=MAIN_TIMEOUT_MS, bot_uuid=MEMBER_BOT)
            data = runner.execute(DIRECT_CHAT_MAIN_READ_OPERATION,
                                  run_id=MAIN_RUN)["data"]
        self.assertEqual(data["state"], "pending")
        self.assertEqual(data["delivery"]["status"], "queued")
        self.assertEqual(data["delivery"]["wait_reason"], "rate_limited")

    def test_operations_are_once_only_ordered_and_contracted(self):
        ping_kwargs = {"message": PING_MESSAGE, "bot_uuid": MEMBER_BOT}
        main_kwargs = {"message": MAIN_MESSAGE, "tags": list(MAIN_TAGS),
                       "timeout_ms": MAIN_TIMEOUT_MS, "bot_uuid": MEMBER_BOT}
        with self.leaf(token=DRIVER_TOKEN, side_effect=[
                completed(0, bytes_json(submit_projection(self.profile, PING_RUN,
                                                          PING_SESSION))),
                completed(0, bytes_json(submit_projection(self.profile, PING_RUN,
                                                          PING_SESSION)))]) as capture:
            runner = self.fresh()
            runner.execute(DIRECT_CHAT_PING_SUBMIT_OPERATION, **ping_kwargs)
            with self.assertRaisesRegex(DirectChatCliRunnerError, "already executed"):
                runner.execute(DIRECT_CHAT_PING_SUBMIT_OPERATION, **ping_kwargs)
            runner = self.fresh()
            with self.assertRaisesRegex(DirectChatCliRunnerError,
                                        "reordered or skipped"):
                runner.execute(DIRECT_CHAT_MAIN_READ_OPERATION, run_id=MAIN_RUN)
            runner = self.fresh()
            with self.assertRaisesRegex(DirectChatCliRunnerError,
                                        "not a reviewed direct chat CLI leaf"):
                runner.execute("direct_chat.bogus")
            runner = self.fresh()
            with self.assertRaisesRegex(DirectChatCliRunnerError,
                                        "exact PM Bot target"):
                runner.execute(DIRECT_CHAT_PING_SUBMIT_OPERATION,
                               message=PING_MESSAGE, bot_uuid="other-bot")
            runner = self.fresh()
            with self.assertRaisesRegex(DirectChatCliRunnerError,
                                        "admits no tags or timeout budget"):
                runner.execute(DIRECT_CHAT_PING_SUBMIT_OPERATION,
                               message=PING_MESSAGE, bot_uuid=MEMBER_BOT,
                               tags=["e2e"], timeout_ms=60000)
            runner = self.fresh()
            with self.assertRaisesRegex(DirectChatCliRunnerError,
                                        "exact reviewed message"):
                runner.execute(DIRECT_CHAT_PING_SUBMIT_OPERATION,
                               message="wrong", bot_uuid=MEMBER_BOT)
            runner = self.fresh()
            runner.execute(DIRECT_CHAT_PING_SUBMIT_OPERATION, **ping_kwargs)
            with self.assertRaisesRegex(DirectChatCliRunnerError,
                                        "exact reviewed message"):
                runner.execute(DIRECT_CHAT_MAIN_SUBMIT_OPERATION,
                               **{**main_kwargs, "message": "other"})
            with self.assertRaisesRegex(DirectChatCliRunnerError,
                                        "exact reviewed tags"):
                runner.execute(DIRECT_CHAT_MAIN_SUBMIT_OPERATION,
                               message=MAIN_MESSAGE, bot_uuid=MEMBER_BOT,
                               tags=["release"])
            with self.assertRaisesRegex(DirectChatCliRunnerError,
                                        "exact reviewed timeout"):
                runner.execute(DIRECT_CHAT_MAIN_SUBMIT_OPERATION,
                               message=MAIN_MESSAGE, bot_uuid=MEMBER_BOT,
                               tags=list(MAIN_TAGS), timeout_ms=30000)
            self.assertEqual(capture.call_count, 2)

    def test_binary_and_state_gates_fail_closed(self):
        not_executable = self.root / "not-executable"
        not_executable.write_bytes(b"#!/bin/sh\nexit 0\n")
        not_executable.chmod(0o600)
        with patch.object(DirectChatCliRunner, "_LAUNCHER_ROOT_BASE",
                          self.launcher_base):
            for binary in (self.root / "missing-cli", not_executable):
                with self.subTest(binary=str(binary)):
                    with self.assertRaisesRegex(DirectChatCliRunnerError, "approved"):
                        DirectChatCliRunner(
                            self.profile, binary=binary,
                            binary_sha256=self.cli_sha256,
                            state_dir=self.state_dir)
            with self.assertRaisesRegex(DirectChatCliRunnerError, "approved"):
                DirectChatCliRunner(self.profile, binary=self.cli_binary,
                                    binary_sha256="not-a-digest",
                                    state_dir=self.state_dir)
            with self.assertRaisesRegex(DirectChatCliRunnerError, "absolute"):
                DirectChatCliRunner(self.profile, binary=self.cli_binary,
                                    binary_sha256=self.cli_sha256,
                                    state_dir=Path("relative/state"))
            with self.assertRaisesRegex(DirectChatCliRunnerError, "absolute"):
                DirectChatCliRunner(self.profile, binary=self.cli_binary,
                                    binary_sha256=self.cli_sha256,
                                    state_dir=self.root / "missing-state")

    def test_missing_credentials_fail_before_any_process_start(self):
        cases = (
            (local_profile(), {}, COOKIE_ENV),
            (preprod_profile(), {}, COOKIE_ENV),
            (preprod_profile(auth=trio_auth()),
             {MEMBER_TOKEN_ENV: MEMBER_TOKEN, COOKIE_ENV: HUMAN_COOKIE},
             DRIVER_TOKEN_ENV),
            (preprod_profile(auth=trio_auth()), {}, MEMBER_TOKEN_ENV),
        )
        for profile, environment, missing in cases:
            with self.subTest(tier=profile["environment"]["tier"],
                              kind=profile["auth"]["kind"], missing=missing):
                cleared = {key: value for key, value in environment.items()}
                with patch.dict(os.environ, cleared, clear=True), \
                     patch.object(DirectChatCliRunner, "_LAUNCHER_ROOT_BASE",
                                  self.launcher_base), \
                     patch("bcs_e2e.direct_chat_cli_case_leaf.capture") as capture:
                    runner = DirectChatCliRunner(
                        profile, binary=self.cli_binary,
                        binary_sha256=self.cli_sha256, state_dir=self.state_dir)
                    with self.assertRaisesRegex(DirectChatCliRunnerError,
                                                "credential|Cookie|trio"):
                        runner.execute(DIRECT_CHAT_PING_SUBMIT_OPERATION,
                                       message=PING_MESSAGE,
                                       bot_uuid=profile["actors"]["member"]["bot_id"])
                    capture.assert_not_called()


if __name__ == "__main__":
    unittest.main()
