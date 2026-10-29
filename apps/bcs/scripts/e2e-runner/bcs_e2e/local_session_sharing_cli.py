"""Finite product-CLI adapter for the owned local Session sharing slice."""
from __future__ import annotations

import json
from pathlib import Path
import re
import uuid

from .bounded_process import capture
from .local_cli import require_cli_binary
from .local_cli_exchange import LocalCliExchange
from .profile import fingerprint, identifier, unique_keys
from .run_state import write


class LocalSessionSharingCliError(RuntimeError):
    pass


class SharingCliDriver:
    """Run only the ten reviewed CLI operations against a controlled forwarder."""

    def __init__(self, case, binary: Path, binary_sha256: str):
        require_cli_binary(binary, binary_sha256)
        self.case = case
        self.binary = binary.resolve()
        self.binary_sha256 = binary_sha256

    def require_pm_token(self) -> None:
        self.case._active()
        self._token(self.case.identities._entries['PM'][1])

    @staticmethod
    def _token(value: object) -> str:
        if (not isinstance(value, str) or not 1 <= len(value) <= 4096
                or not value.isascii()
                or any(ord(char) <= 32 or ord(char) == 127 for char in value)):
            raise LocalSessionSharingCliError('owned CLI Bot token is missing or invalid')
        return value

    def _context(self, operation: str, token: str, bot_id: str):
        root = self.case.root / ('session-sharing-cli-' + uuid.uuid4().hex)
        root.mkdir(mode=0o700)
        for name in ('home', 'bot', 'tmp'):
            (root / name).mkdir(mode=0o700)
        context_dir = root / 'bot' / '.bcs'
        context_dir.mkdir(mode=0o700)
        context = context_dir / 'session.json'
        directories = {
            item: (item.stat().st_dev, item.stat().st_ino)
            for item in (root, root / 'bot', context_dir)
        }
        credential = {'schema_version': 1, 'token': token, 'bot_uuid': bot_id,
                      'api_base_url': ''}

        def verify(expected):
            for item, inode in directories.items():
                metadata = item.stat()
                if (item.is_symlink() or not item.is_dir()
                        or (metadata.st_dev, metadata.st_ino) != inode):
                    raise LocalSessionSharingCliError('private CLI context directory changed')
            if context.is_symlink() or fingerprint(json.loads(context.read_text())) != fingerprint(expected):
                raise LocalSessionSharingCliError('private CLI credential context changed')

        environment = {
            'PATH': '/usr/bin:/bin', 'HOME': str(root / 'home'),
            'BOT_DATA_DIR': str(root / 'bot'), 'TMPDIR': str(root / 'tmp'),
            'LANG': 'C', 'SERVER_ENV': 'local', 'tc_sdb_nenv': 'production',
        }
        return root, context, credential, environment, verify

    def invoke(self, *, operation: str, method: str, path: str, body: object,
               arguments: list[str], expected_status: int, output_kind: str,
               expected: dict, handles: tuple[str, ...] = (),
               request_id: str) -> dict:
        self.case._active()
        require_cli_binary(self.binary, self.binary_sha256)
        bot_id, token = self.case.identities._entries['PM']
        token = self._token(token)
        try:
            self.case._admit_operation(operation=operation, method=method, path=path,
                body=body, auth_role='PM', expected_status=expected_status,
                handles=handles, transport='PRODUCT_CLI', arguments=arguments,
                output_kind=output_kind, output_expected=expected)
        except BaseException:
            if handles and self.case.ledger is not None:
                for handle in handles:
                    try: self.case.ledger.unknown(handle)
                    except BaseException: pass
            self.case.invalidated = True
            raise
        event = self.case._append_operation(
            operation, method, path, kind='MUTATION' if method == 'POST' else 'CHECK',
            handles=handles, request_id=request_id, transport='PRODUCT_CLI')
        root, context, credential, environment, verify = self._context(operation, token, bot_id)
        result = exchange = None
        cleanup = 'NOT_WRITTEN'
        try:
            with LocalCliExchange(self.case, event, token=token, body=body) as exchange:
                credential['api_base_url'] = exchange.origin
                write(context, credential)
                cleanup = 'PENDING'
                verify(credential)
                self.case._active()
                result = capture(
                    [str(self.binary), '--json', '--log-level', 'error', *arguments],
                    cwd=root, environment=environment, timeout_seconds=12,
                    max_output_bytes=65_536)
            self.case._active()
            verify(credential)
            if (exchange.failure is not None or exchange.attempts != 1 or exchange.forwarded != 1
                    or result.returncode != 0 or event.get('http_status') != expected_status
                    or not isinstance(exchange.response, dict)):
                raise LocalSessionSharingCliError('product CLI operation was not exactly confirmed')
            reply = exchange.response
            stdout = result.stdout.decode('utf-8')
            evidence = self._validate_output(output_kind, stdout, reply, expected)
            event.update(status='ACKNOWLEDGED', cli_exit_code=0,
                         resource_binding='BOUND' if handles else 'NOT_APPLICABLE',
                         output_validation=evidence)
            event['transitions'].append({'status': 'ACKNOWLEDGED', 'http_status': expected_status,
                                         'cli_exit_code': 0})
            self.case._save()
            return reply
        except BaseException as error:
            if isinstance(error, UnicodeError):
                error = LocalSessionSharingCliError('product CLI stdout was not UTF-8')
            self.case._operation_failed(event, error, handles=handles, mutation=method == 'POST')
            failure = LocalSessionSharingCliError(
                'product CLI outcome is unconfirmed; no retry, adoption, or cleanup')
            if getattr(error, 'child_shutdown_status', None) == 'UNCONFIRMED':
                failure.child_shutdown_status = 'UNCONFIRMED'
            raise failure from None
        finally:
            token = ''
            credential['token'] = ''
            credential.pop('bot_uuid', None)
            credential['api_base_url'] = self.case.identities.origin
            try:
                if cleanup != 'NOT_WRITTEN':
                    write(context, credential)
                    verify(credential)
                    cleanup = 'CLEARED'
            except BaseException:
                cleanup = 'UNCONFIRMED'
                self.case.invalidated = True
            event['credential_cleanup'] = cleanup
            event['credential_context'] = root.name
            try:
                self.case._save()
            except BaseException:
                pass
            if cleanup == 'UNCONFIRMED' and result is not None:
                raise LocalSessionSharingCliError('private CLI credential cleanup is unconfirmed')

    @staticmethod
    def _validate_output(kind: str, stdout: str, reply: dict, expected: dict) -> dict:
        if kind == 'group-create-human':
            group_id = identifier(reply.get('id'), 'CLI Group ID')
            session_id = identifier(reply.get('session_id'), 'CLI implicit Session ID')
            if (reply.get('driver_bot') != expected['pm']
                    or reply.get('participants') != [expected['pm'], expected['eng']]
                    or not session_id.startswith(group_id + ':')
                    or re.findall(r'^  ID: (.+)$', stdout, re.M) != [group_id]
                    or re.findall(r'^  Session: (.+)$', stdout, re.M) != [session_id]
                    or 'Group created:' not in stdout):
                raise LocalSessionSharingCliError('CLI Group human stdout differs from the live response')
            return {'kind': kind, 'group_id': group_id, 'initial_session_id': session_id,
                    'raw_output_persisted': False}
        try:
            value = json.loads(stdout, object_pairs_hook=unique_keys)
        except (ValueError, json.JSONDecodeError):
            raise LocalSessionSharingCliError('CLI did not emit one JSON value') from None
        if fingerprint(value) != fingerprint(reply):
            raise LocalSessionSharingCliError('CLI JSON stdout differs from the live response')
        if kind == 'session-create-json':
            session_id = identifier(reply.get('session_id'), 'CLI explicit Session ID')
            if (reply.get('id') != session_id or reply.get('group_id') != expected['group_id']
                    or reply.get('session_title') != expected['title']
                    or not session_id.startswith(expected['group_id'] + ':')):
                raise LocalSessionSharingCliError('CLI Session create response differs')
            return {'kind': kind, 'session_id': session_id, 'raw_output_persisted': False}
        if kind == 'session-list-json':
            items = reply.get('items')
            if (not isinstance(items, list) or len(items) > 20
                    or not any(isinstance(row, dict)
                               and (row.get('session_id') or row.get('id')) == expected['session_id']
                               for row in items)):
                raise LocalSessionSharingCliError('CLI Session list omitted the exact created Session')
            return {'kind': kind, 'contains_session': True, 'raw_output_persisted': False}
        if kind == 'session-get-json':
            if (reply.get('id') != expected['session_id']
                    or reply.get('session_id') != expected['session_id']
                    or reply.get('group_id') != expected['group_id']):
                raise LocalSessionSharingCliError('CLI Session get did not return the exact Session')
            return {'kind': kind, 'session_match': True, 'raw_output_persisted': False}
        if kind == 'invite-json':
            # The caller validates and consumes the credential entirely in memory.
            if set(reply) != {'invite_token', 'expires_at', 'join_url'}:
                raise LocalSessionSharingCliError('CLI invite response fields differ')
            return {'kind': kind, 'credential_fields_validated': True,
                    'raw_output_persisted': False, 'invite_value_persisted': False}
        raise LocalSessionSharingCliError('unsupported finite CLI output contract')
