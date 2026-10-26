"""Foreground ownership of one BCS child; no adoption of PIDs from disk.

A future persistent supervisor can build on this primitive. This module never
calls legacy singlebox scripts, scans for processes to kill, or removes data.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import stat
import subprocess
import time
from urllib.request import ProxyHandler, build_opener

from .local_config import prepare_local_bcs_inputs
from .local_panel_asset import LocalPanelAsset
from .local_template_asset import LocalTemplateAsset
from .local_provider_sink import OwnedLocalProviderSink
from .local_event_sink import OwnedLocalEventSink
from .local_judge_sandbox import OwnedLocalJudgeSandbox
from .profile import identifier
from .run_state import write
from .runtime import RuntimeContractError
from .transport import NoRedirect


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


class OwnedBcsProcess:
    """Only the live Popen handle created by this object authorizes termination."""
    def __init__(self, *, binary: Path, binary_sha256: str, root: Path, run_id: str,
                 port: int = 0, startup_timeout: int = 30, stop_timeout: int = 15,
                 store_messages: bool = False, panel_asset: LocalPanelAsset | None = None,
                 template_asset: LocalTemplateAsset | None = None,
                 provider_sink: OwnedLocalProviderSink | None = None,
                 event_sink: OwnedLocalEventSink | None = None,
                 judge_sandbox: OwnedLocalJudgeSandbox | None = None,
                 allow_ambient_local_identity: bool = True,
                 loop_execution_enabled: bool = False,
                 friend_work_order_base_url: str | None = None):
        identifier(run_id, 'runtime.run_id')
        if judge_sandbox is not None:
            if (type(judge_sandbox) is not OwnedLocalJudgeSandbox or provider_sink is not None
                    or event_sink is not None or store_messages is not True):
                raise RuntimeContractError('Judge requires one owned sandbox, message storage and no other sinks')
            judge_sandbox.validate_live(run_id)
        self.judge_sandbox = judge_sandbox
        if event_sink is not None:
            if type(event_sink) is not OwnedLocalEventSink or provider_sink is not None or store_messages is not True:
                raise RuntimeContractError('Eventing requires one owned receiver, message storage and no Provider sink')
            event_sink.validate_live(run_id)
        self.event_sink = event_sink
        if provider_sink is not None:
            if type(provider_sink) is not OwnedLocalProviderSink:
                raise RuntimeContractError('runtime provider sink requires an owned live receiver')
            provider_sink.validate_live(run_id)
        self.provider_sink = provider_sink
        if type(store_messages) is not bool:
            raise RuntimeContractError('runtime message storage selection must be boolean')
        self.store_messages = store_messages
        if type(loop_execution_enabled) is not bool:
            raise RuntimeContractError('runtime loop execution selection must be boolean')
        self.loop_execution_enabled = loop_execution_enabled
        if panel_asset is not None:
            if type(panel_asset) is not LocalPanelAsset:
                raise RuntimeContractError('runtime panel asset requires an explicit artifact input')
            panel_asset.validate()
        self.panel_asset = panel_asset
        if template_asset is not None:
            if type(template_asset) is not LocalTemplateAsset:
                raise RuntimeContractError('runtime template asset requires an explicit approved manifest input')
            template_asset.validate()
        self.template_asset = template_asset
        if (not isinstance(binary_sha256, str) or len(binary_sha256) != 64
                or any(c not in '0123456789abcdef' for c in binary_sha256)):
            raise RuntimeContractError('runtime requires an explicit binary SHA256')
        if type(port) is not int or port != 0 and not 1024 <= port <= 65535:
            raise RuntimeContractError('runtime port must be zero or an unprivileged TCP port')
        for value in (startup_timeout, stop_timeout):
            if type(value) is not int or not 1 <= value <= 60:
                raise RuntimeContractError('runtime timeout must be 1..60 seconds')
        if binary.is_symlink() or not binary.is_file() or not os.access(binary, os.X_OK):
            raise RuntimeContractError('runtime binary must be an executable regular file')
        self.binary = binary.resolve()
        self.binary_sha256 = binary_sha256
        self.allow_ambient_local_identity = allow_ambient_local_identity
        self.friend_work_order_base_url = friend_work_order_base_url
        self.root, self.run_id, self.port = root, run_id, port
        self.startup_timeout, self.stop_timeout = startup_timeout, stop_timeout
        self.inputs = None
        self.child: subprocess.Popen | None = None
        self._log = None
        self._lsof = shutil.which('lsof')
        self.state = 'NEW'
        self.ready = False
        self.forced_stop = False
        self.stop_attempted = False
        # Captured at readiness, never reconstructed from a later receipt.
        self.database_identity: tuple[int, int] | None = None
        self.access_log_identity: tuple[int, int] | None = None

    def _record(self) -> dict:
        return {'schema_version': 1, 'run_id': self.run_id, 'status': self.state,
                'origin': self.inputs.origin if self.inputs is not None else None,
                'pid': self.child.pid if self.child is not None else None,
                'returncode': self.child.poll() if self.child is not None else None,
                'binary_sha256': self.binary_sha256, 'ready': self.ready,
                'forced_stop': self.forced_stop,
                'ownership': 'live-child-handle-only; saved PID never authorizes stop'}

    def _persist(self) -> dict:
        record = self._record()
        write(self.root / 'owned-bcs.json', record)
        return record

    def _owns_listener(self, timeout: float) -> bool:
        if self.child is None or self.child.poll() is not None:
            return False
        result = subprocess.run([self._lsof, '-nP', '-a', '-p', str(self.child.pid),
                                 f'-iTCP:{self.port}', '-sTCP:LISTEN', '-Fpn'],
                                stdin=subprocess.DEVNULL, capture_output=True, text=True,
                                timeout=timeout, check=False)
        lines = result.stdout.splitlines()
        return (result.returncode == 0 and f'p{self.child.pid}' in lines
                and f'n127.0.0.1:{self.port}' in lines)

    def start(self) -> dict:
        if self.state != 'NEW':
            raise RuntimeContractError('runtime object cannot be started twice')
        if self._lsof is None:
            raise RuntimeContractError('lsof is required to verify the owned listener')
        if file_sha256(self.binary) != self.binary_sha256:
            raise RuntimeContractError('runtime binary SHA256 does not match the approved artifact')
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as reservation:
            try:
                reservation.bind(('127.0.0.1', self.port))
            except OSError:
                raise RuntimeContractError('local runtime port is occupied; no process was stopped') from None
            self.port = reservation.getsockname()[1]
            self.inputs = prepare_local_bcs_inputs(self.root, run_id=self.run_id, port=self.port,
                                                   store_messages=self.store_messages, panel_asset=self.panel_asset,
                                                   template_asset=self.template_asset, provider_sink=self.provider_sink, event_sink=self.event_sink,
                                                   judge_sandbox=self.judge_sandbox, allow_ambient_local_identity=self.allow_ambient_local_identity,
                                                   loop_execution_enabled=self.loop_execution_enabled,
                                                   friend_work_order_base_url=self.friend_work_order_base_url)
            self.root = self.inputs.root
            self.state = 'STARTING'
            self._persist()
            # BCS currently binds its own socket. Ownership checks below reject
            # another listener that wins the small close -> child bind race.
        try:
            fd = os.open(self.root / 'console.log', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            self._log = os.fdopen(fd, 'wb')
            self.child = subprocess.Popen([str(self.binary), '--config-dir', str(self.inputs.config_path)],
                cwd=self.root, env=self.inputs.environment, stdin=subprocess.DEVNULL,
                stdout=self._log, stderr=subprocess.STDOUT, start_new_session=True)
            self._persist()
            deadline = time.monotonic() + self.startup_timeout
            opener = build_opener(ProxyHandler({}), NoRedirect())
            while time.monotonic() < deadline:
                if self.child.poll() is not None:
                    raise RuntimeContractError('owned BCS exited before readiness; inspect private console.log')
                remaining = max(.01, min(1.0, deadline - time.monotonic()))
                try:
                    if self._owns_listener(remaining):
                        with opener.open(self.inputs.origin + '/health', timeout=remaining) as response:
                            raw = response.read(4097)
                            healthy = (response.status == 200 and len(raw) <= 4096
                                       and json.loads(raw).get('status') == 'ok')
                        if healthy and self.child.poll() is None and self._owns_listener(remaining):
                            database = self.root / 'bcs.sqlite'
                            if database.exists():
                                metadata = database.lstat()
                                if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
                                    raise RuntimeContractError('owned database is not a regular unshared file')
                                self.database_identity = (metadata.st_dev, metadata.st_ino)
                            access_log = self.root / 'logs' / 'e2e-access.jsonl'
                            if access_log.exists():
                                metadata = access_log.lstat()
                                if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
                                    raise RuntimeContractError('owned access log is not a regular unshared file')
                                self.access_log_identity = (metadata.st_dev, metadata.st_ino)
                            self.state, self.ready = 'RUNNING', True
                            return self._persist()
                except (OSError, ValueError, AttributeError, subprocess.TimeoutExpired):
                    pass
                time.sleep(min(.1, max(0, deadline - time.monotonic())))
            raise RuntimeContractError('owned BCS readiness timed out')
        except BaseException:
            # Reap only our direct child. Preserve configuration/logs and the
            # failure marker; never attempt cleanup by port or saved PID.
            self._reap()
            self.state, self.ready = 'FAILED', False
            self._persist()
            raise

    def _reap(self) -> None:
        if self.child is not None:
            self.stop_attempted = True
        try:
            if self.child is not None and self.child.poll() is None:
                self.child.terminate()
                try:
                    self.child.wait(timeout=self.stop_timeout)
                except subprocess.TimeoutExpired:
                    self.forced_stop = True
                    self.child.kill()
                    self.child.wait(timeout=self.stop_timeout)
        finally:
            if self._log is not None:
                self._log.close()
                self._log = None

    def stop(self) -> dict:
        if self.child is None:
            raise RuntimeContractError('no owned child handle; saved runtime state cannot authorize stop')
        self._reap()
        self.ready = False
        self.state = 'STOPPED' if self.child.returncode == 0 and not self.forced_stop else 'FAILED_STOP'
        return self._persist()

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *_args):
        self.stop()


def local_runtime_smoke(*, with_fixtures: bool = False, fixture_set: str = 'pilot',
                        verify_bot_identities: bool = False, check_trust_baseline: bool = False,
                        run_trust_api: bool = False, trust_cli=None, **kwargs) -> dict:
    """Start, verify, and stop within one foreground invocation, including SIGTERM."""
    if type(with_fixtures) is not bool:
        raise RuntimeContractError('with_fixtures must be an explicit boolean')
    from .local_fixtures import fixture_roles
    fixture_roles(fixture_set)
    if not with_fixtures and fixture_set != 'pilot':
        raise RuntimeContractError('a fixture set requires explicit fixture provisioning')
    if (type(verify_bot_identities) is not bool or verify_bot_identities
            and (not with_fixtures or fixture_set == 'pilot')):
        raise RuntimeContractError('Bot identity verification requires an explicit expanded fixture set')
    if (type(check_trust_baseline) is not bool or check_trust_baseline
            and (not verify_bot_identities or fixture_set != 'trusted-team')):
        raise RuntimeContractError('trust baseline requires all trusted-team Bot identities')
    if (type(run_trust_api) is not bool or run_trust_api
            and (not check_trust_baseline or not verify_bot_identities or fixture_set != 'trusted-team')):
        raise RuntimeContractError('local trust API execution requires verified trusted-team baseline')
    registration = None
    if trust_cli is not None:
        from .local_trust_cli import OwnedLocalTrustCli
        if type(trust_cli) is not OwnedLocalTrustCli or not run_trust_api:
            raise RuntimeContractError('CLI trust execution requires the full owned local API sequence')
        from .trust_registration import TrustRegistration
        registration = TrustRegistration(cli_sha256=trust_cli.digest)
    def cancelled(_signum, _frame):
        raise KeyboardInterrupt()
    previous = signal.signal(signal.SIGTERM, cancelled)
    try:
        fixture_result = {'status': 'NOT_RUN'}
        identity_result = {'status': 'NOT_SELECTED'}
        baseline_result = {'status': 'NOT_SELECTED'}
        trust_result = {'status': 'NOT_SELECTED'}
        panel_result = {'status': 'NOT_SELECTED'}
        with OwnedBcsProcess(**kwargs) as runtime:
            started = runtime._record()
            if runtime.panel_asset is not None:
                from .local_panel_probe import verify_owned_panel
                panel_result = verify_owned_panel(runtime)
            if registration is not None:
                registration.attach(runtime)
            if with_fixtures:
                from .local_fixtures import OwnedLocalFixtures
                fixtures = OwnedLocalFixtures(runtime, fixture_set=fixture_set, registration=registration)
                if verify_bot_identities:
                    from .local_role_identity import OwnedLocalRoleIdentity
                    with OwnedLocalRoleIdentity(fixtures, roles=fixtures.roles) as identities:
                        fixtures.apply(allow_write=True, role_identity=identities)
                        verified = [identities.verify(role) for role in fixtures.roles]
                        if registration is not None:
                            registration.bind_context(identities)
                        if run_trust_api:
                            from .local_trust_api import OwnedLocalTrustApi
                            trust_result = OwnedLocalTrustApi(identities, cli=trust_cli).run()
                            baseline_result = trust_result['baseline']
                        elif check_trust_baseline:
                            baseline_result = identities.trust_baseline()
                    identity_result = {'status': 'PASS', 'roles': verified, **identities.summary()}
                else:
                    fixtures.apply(allow_write=True)
                fixture_result = fixtures.check()
        stopped = runtime._record()
        if registration is not None:
            registration.validate(runtime)
        success_status = 'LOCAL_FIXTURE_SMOKE_PASS' if with_fixtures else 'LOCAL_RUNTIME_SMOKE_PASS'
        if run_trust_api:
            success_status = 'LOCAL_TRUST_API_CLI_PASS' if trust_cli is not None else 'LOCAL_TRUST_API_SLICE_PASS'
        result = {'schema_version': 1,
                'status': success_status if stopped['status'] == 'STOPPED' else 'BLOCKED',
                'run_id': runtime.run_id, 'startup': started, 'shutdown': stopped,
                'runtime_dir': str(runtime.root), 'ready_to_execute': False,
                'fixtures': fixture_result, 'bot_identities': identity_result,
                'trust_baseline': baseline_result, 'trust_api': trust_result,
                'panel_asset': panel_result,
                'not_verified': ([] if with_fixtures else ['Bot fixtures']) +
                                ['Bot runtime/model behavior', 'pilot Stories', 'Gateway/SSO', 'BaaS/OB', 'full release']}
        write(runtime.root / 'smoke-report.json', result)
        if registration is not None:
            from .trust_reporting import publish_trust_report
            canonical = publish_trust_report(registration, runtime, result=result)
            if canonical['status'] != 'LOCAL_SUITE_PASS':
                raise RuntimeContractError('trusted canonical report did not qualify')
            result['canonical_report'] = canonical
        return result
    except BaseException as error:
        if (registration is not None and registration.context is not None
                and not getattr(registration.context, 'publication_attempted', False)):
            from .trust_reporting import publish_trust_report
            try:
                publish_trust_report(registration, runtime, failure=error)
            except BaseException as publication:
                from .errors import has_cancellation
                failed = (KeyboardInterrupt('trusted report publication interrupted')
                          if has_cancellation(error) or has_cancellation(publication) else
                          RuntimeContractError('trusted execution failed; canonical publication also failed; retain artifacts'))
                if (getattr(error, 'child_shutdown_status', None) == 'UNCONFIRMED'
                        or getattr(publication, 'child_shutdown_status', None) == 'UNCONFIRMED'):
                    failed.child_shutdown_status = 'UNCONFIRMED'
                raise failed from None
        raise
    finally:
        signal.signal(signal.SIGTERM, previous)
