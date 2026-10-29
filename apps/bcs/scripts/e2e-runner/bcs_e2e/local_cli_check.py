"""Opt-in local CLI check, independently reported from the shared pilot Cases."""
from __future__ import annotations

import math
import re
from pathlib import Path

from .errors import primary_error
from .local_adapter import OwnedLocalPilotAdapter
from .local_cli import LocalCliError, OwnedLocalSessionCli, require_cli_binary
from .run_state import write


class LocalCliCheck:
    def __init__(self, binary: Path, binary_sha256: str):
        require_cli_binary(binary, binary_sha256)
        self.binary, self.binary_sha256 = binary, binary_sha256
        self._driver_id, self._driver_token = '', ''
        self.result = {'schema_version': 1, 'status': 'NOT_RUN', 'required': True,
                       'operation': 'cli.session.get', 'case_fixture': 'PRE-02',
                       'binary_sha256': binary_sha256, 'attempts': 0,
                       'full_cli_coverage': 'NOT_QUALIFIED', 'remote_authentication': 'NOT_TESTED'}

    @property
    def passed(self) -> bool:
        return (self.result['status'] == 'LOCAL_CLI_SLICE_PASS' and self.result['attempts'] == 1
                and 'persistence_error_type' not in self.result
                and self.result.get('bot_permission', {}).get('status') == 'LOCAL_CLI_AUTH_PASS')

    def capture_driver_token(self, bot_id: str, bot_token: str) -> None:
        if self._driver_id or self.result['attempts']:
            raise LocalCliError('local CLI driver credential cannot be replaced or replayed')
        self._driver_id, self._driver_token = bot_id, bot_token

    def forget_credentials(self) -> None:
        self._driver_id, self._driver_token = '', ''

    def persist(self, state_dir: Path) -> None:
        write(state_dir / 'product-cli-check.json', self.result)

    def observe(self, runtime, profile: dict, state_dir: Path, session_id: str,
                group_id: str, title: str) -> None:
        if self.result['attempts'] != 0:
            self.result.update(status='BLOCKED', error_type='RepeatedCliCheck')
        else:
            self.result.update(status='RUNNING', attempts=1)
            try:
                self.persist(state_dir)
                cli = OwnedLocalSessionCli(runtime, profile, state_dir=state_dir, case_id='PRE-02',
                                           binary=self.binary, binary_sha256=self.binary_sha256)
                observed = cli.read_session(session_id, group_id, title)
                expected = {'status': 'LOCAL_CLI_SLICE_PASS', 'operation': 'cli.session.get',
                            'binary_sha256': self.binary_sha256, 'session_id': session_id,
                            'group_id': group_id, 'authentication': 'NONE',
                            'source': 'product-bcs-cli', 'api_face': 'legacy',
                            'full_cli_coverage': 'NOT_QUALIFIED', 'remote_authentication': 'NOT_TESTED'}
                if not isinstance(observed, dict) or any(observed.get(key) != value for key, value in expected.items()):
                    raise LocalCliError('product CLI check returned an invalid result')
                elapsed, context = observed.get('elapsed_ms'), observed.get('context_dir')
                if (type(elapsed) not in (int, float) or not math.isfinite(elapsed) or elapsed < 0
                        or not isinstance(context, str) or len(context) > 4096
                        or any(ord(char) < 32 or ord(char) == 127 for char in context)):
                    raise LocalCliError('product CLI context or timing evidence is invalid')
                path = Path(context)
                if (not path.is_absolute() or path.is_symlink() or not path.is_dir()
                        or path.parent.resolve() != state_dir.resolve()
                        or re.fullmatch(r'product-cli-[0-9a-f]{32}', path.name) is None):
                    raise LocalCliError('product CLI context is outside the current Run')
                self.result.update({key: observed[key] for key in (*expected, 'context_dir', 'elapsed_ms')})
                permission = cli.read_permission(session_id, group_id, self._driver_id, self._driver_token)
                required = {'status': 'LOCAL_CLI_AUTH_PASS', 'operation': 'cli.collaborate.permission',
                            'source': 'product-bcs-cli', 'api_face': 'legacy',
                            'authentication': 'OWNED_LOCAL_BOT', 'credential_cleanup': 'CLEARED',
                            'binary_sha256': self.binary_sha256, 'caller_bot_id': self._driver_id,
                            'group_owner_bot_id': self._driver_id, 'session_id': session_id, 'group_id': group_id,
                            'allowed': True, 'reason_code': 'allowed', 'group_strategy': 'chat',
                            'policy_version': 'session_state_machine_v1',
                            'remote_authentication': 'NOT_TESTED', 'full_cli_coverage': 'NOT_QUALIFIED'}
                if (not self._driver_id or not isinstance(permission, dict) or permission.get('allowed') is not True
                        or any(permission.get(key) != value for key, value in required.items())):
                    raise LocalCliError('product CLI authenticated permission check returned an invalid result')
                # Project only contract scalars; never trust arbitrary subprocess output.
                self.result['bot_permission'] = {key: permission[key] for key in required}

            except KeyboardInterrupt as error:
                self._failure_metadata(error)
                if getattr(error, 'child_shutdown_status', None) == 'UNCONFIRMED':
                    self.result['child_shutdown_status'] = 'UNCONFIRMED'
                self.result.update(status='CANCELLED', error_type='KeyboardInterrupt')
                raise
            except Exception as error:
                self._failure_metadata(error)
                if getattr(error, 'child_shutdown_status', None) == 'UNCONFIRMED':
                    self.result['child_shutdown_status'] = 'UNCONFIRMED'
                cause = primary_error(error)
                if isinstance(cause, KeyboardInterrupt):
                    self.result.update(status='CANCELLED', error_type='KeyboardInterrupt',
                                       secondary_error_type=type(error).__name__)
                    raise cause from None
                # A side-check failure is not an API assertion failure. Preserve
                # the actual API result and let PRE-02 finish scoped cleanup.
                self.result.update(status='BLOCKED', error_type=type(error).__name__)
            finally:
                self.forget_credentials()
                self._checkpoint(state_dir)
            return
        self._checkpoint(state_dir)

    def _failure_metadata(self, error: BaseException) -> None:
        # Scrubbing can fail while a process failure/cancellation is unwinding.
        # Neither local cleanup axis may hide the other one's uncertainty.
        if any(getattr(cause, 'child_shutdown_status', None) == 'UNCONFIRMED'
               for cause in (error, primary_error(error))):
            self.result['child_shutdown_status'] = 'UNCONFIRMED'
        if getattr(error, 'credential_cleanup', None) == 'UNCONFIRMED':
            self.result['credential_cleanup'] = 'UNCONFIRMED'
            self.result['credential_context'] = error.credential_context

    def _checkpoint(self, state_dir: Path) -> None:
        try:
            self.persist(state_dir)
        except KeyboardInterrupt:
            self.result.update(status='CANCELLED', error_type='KeyboardInterrupt')
            raise
        except Exception as error:
            cause = primary_error(error)
            if isinstance(cause, KeyboardInterrupt):
                self.result.update(status='CANCELLED', error_type='KeyboardInterrupt',
                                   persistence_error_type=type(error).__name__)
                raise cause from None
            # Keep the check result and storage failure separate, without losing
            # the original API outcome or swallowing missing durable evidence.
            self.result['persistence_error_type'] = type(error).__name__


class CliCheckedLocalAdapter(OwnedLocalPilotAdapter):
    def __init__(self, runtime, profile, *, state_dir, case_id, cli_check: LocalCliCheck):
        super().__init__(runtime, profile, state_dir=state_dir, case_id=case_id)
        self.cli_check, self.cli_runtime, self.cli_state_dir = cli_check, runtime, state_dir

    def session_update(self, session_id: str, body: dict):
        data, record = super().session_update(session_id, body)
        # This adapter is installed only for PRE-02. The same Story owns the
        # Session and cleanup; no additional product resource is created here.
        if isinstance(data, dict):
            self.cli_check.observe(self.cli_runtime, self.profile, self.cli_state_dir,
                                   session_id, data.get('group_id', ''), body.get('title', ''))
        return data, record
