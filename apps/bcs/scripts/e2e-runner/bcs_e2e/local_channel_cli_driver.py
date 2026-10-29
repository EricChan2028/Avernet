"""Finite seven-command product CLI driver for the owned local Channel parent."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import selectors
import stat
import subprocess
import sys
import time
import uuid

from .errors import has_cancellation, primary_error
from .local_cli import require_cli_binary
from .local_cli_exchange import LocalCliExchange
from .profile import fingerprint, identifier, unique_keys
from .run_state import write


class ChannelCliDriverError(RuntimeError):
    pass


class ChannelCliDriver:
    """Execute only the reviewed Channel parent CLI commands, once and in order."""

    def __init__(self, case, binary: Path, binary_sha256: str):
        require_cli_binary(binary, binary_sha256)
        self.case = case
        self.binary = binary.resolve()
        self.binary_sha256 = binary_sha256

    @staticmethod
    def _token(value: object) -> str:
        if (not isinstance(value, str) or not 1 <= len(value) <= 4096
                or not value.isascii()
                or any(ord(char) <= 32 or ord(char) == 127 for char in value)):
            raise ChannelCliDriverError('owned CLI Bot token is missing or invalid')
        return value

    def require_tokens(self) -> None:
        self.case._active()
        for role in ('CEO', 'PM'):
            self._token(self.case.identities._entries[role][1])

    def _context(self, token: str, bot_id: str):
        root = self.case.root / ('channel-cli-context-' + uuid.uuid4().hex)
        root.mkdir(mode=0o700)
        for name in ('home', 'bot', 'tmp'):
            (root / name).mkdir(mode=0o700)
        context_dir = root / 'bot' / '.bcs'
        context_dir.mkdir(mode=0o700)
        context = context_dir / 'session.json'
        directories = {path: (path.stat().st_dev, path.stat().st_ino)
                       for path in (root, root/'home', root/'bot', root/'tmp', context_dir)}
        credential = {'schema_version': 1, 'token': token, 'bot_uuid': bot_id, 'api_base_url': ''}

        def verify(expected):
            for path, identity in directories.items():
                current = path.lstat()
                if (not stat.S_ISDIR(current.st_mode) or path.is_symlink()
                        or (current.st_dev, current.st_ino) != identity):
                    raise ChannelCliDriverError('private CLI context directory changed')
            if (not context.is_file() or context.is_symlink()
                    or fingerprint(json.loads(context.read_text())) != fingerprint(expected)):
                raise ChannelCliDriverError('private CLI credential context changed')

        environment = {'PATH': '/usr/bin:/bin', 'HOME': str(root/'home'),
            'BOT_DATA_DIR': str(root/'bot'), 'TMPDIR': str(root/'tmp'), 'LANG': 'C',
            'SERVER_ENV': 'local', 'tc_sdb_nenv': 'production'}
        return root, context, credential, environment, verify

    @staticmethod
    def _capture(command: list[str], *, cwd: Path, environment: dict[str, str]):
        """Bound stdout and stderr separately because bind redaction covers both."""
        child = subprocess.Popen(command, cwd=cwd, env=environment, shell=False,
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            start_new_session=True, umask=0o077)
        stdout, stderr, total = bytearray(), bytearray(), 0
        deadline = time.monotonic() + 12
        try:
            with selectors.DefaultSelector() as selector:
                for pipe in (child.stdout, child.stderr):
                    os.set_blocking(pipe.fileno(), False)
                    selector.register(pipe, selectors.EVENT_READ)
                while selector.get_map() or child.poll() is None:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise ChannelCliDriverError('product CLI exceeded its deadline')
                    for key, _ in selector.select(min(remaining, .1)):
                        chunk = os.read(key.fileobj.fileno(), 4096)
                        if not chunk:
                            selector.unregister(key.fileobj); continue
                        total += len(chunk)
                        if total > 65_536:
                            raise ChannelCliDriverError('product CLI output exceeded its budget')
                        (stdout if key.fileobj is child.stdout else stderr).extend(chunk)
            return subprocess.CompletedProcess(command, child.returncode, bytes(stdout), bytes(stderr))
        finally:
            original = sys.exc_info()[1]
            unconfirmed = False
            try:
                if child.poll() is None: child.kill()
                child.wait(timeout=2)
            except (OSError, subprocess.SubprocessError):
                unconfirmed = True
            for pipe in (child.stdout, child.stderr):
                try: pipe.close()
                except OSError: unconfirmed = True
            if unconfirmed:
                if original is None:
                    failure = ChannelCliDriverError('product CLI shutdown is unconfirmed')
                    failure.child_shutdown_status = 'UNCONFIRMED'
                    raise failure from None
                original.child_shutdown_status = 'UNCONFIRMED'
                primary_error(original).child_shutdown_status = 'UNCONFIRMED'

    def invoke(self, *, operation: str, role: str, method: str, path: str, body: object,
               arguments: list[str], expected_status: int, output_kind: str,
               handles: tuple[str, ...] = (), request_id: str, secret: str = '') -> dict:
        self.case._active(); require_cli_binary(self.binary, self.binary_sha256)
        bot_id, raw_token = self.case.identities._entries[role]
        token = self._token(raw_token)
        try:
            self.case._admit_cli(operation, role, method, path, body, arguments,
                                expected_status, output_kind, handles, secret)
        except BaseException:
            if handles and self.case.ledger is not None:
                for handle in handles:
                    try: self.case.ledger.unknown(handle)
                    except BaseException: pass
            self.case.invalidated = True
            raise
        event = self.case._append_operation(operation, method, path, role, request_id,
            transport='PRODUCT_CLI', handles=handles)
        root = context = credential = environment = verify = None
        result = exchange = None
        cleanup = 'NOT_WRITTEN'
        try:
            root, context, credential, environment, verify = self._context(token, bot_id)
            with LocalCliExchange(self.case, event, token=token, body=body) as exchange:
                credential['api_base_url'] = exchange.origin
                write(context, credential); cleanup = 'PENDING'; verify(credential)
                # The original helper always selects global JSON mode. Create-group is
                # intentionally legacy: it still emits its human summary in that mode.
                command = [str(self.binary), '--json', '--log-level', 'error', *arguments]
                self.case._active()
                result = self._capture(command, cwd=root, environment=environment)
            self.case._active(); verify(credential)
            if (exchange.failure is not None or exchange.attempts != 1 or exchange.forwarded != 1
                    or event.get('http_status') != expected_status or not isinstance(exchange.response, dict)):
                raise ChannelCliDriverError('product CLI wire operation was not exactly confirmed')
            evidence = self._validate(output_kind, result, exchange.response, secret)
            event.update(status='ACKNOWLEDGED', cli_exit_code=result.returncode,
                         resource_binding='BOUND' if handles else 'NOT_APPLICABLE',
                         output_validation=evidence)
            event['transitions'].append({'status':'ACKNOWLEDGED','http_status':expected_status,
                                         'cli_exit_code':result.returncode})
            self.case._save()
            return exchange.response
        except BaseException as error:
            self.case._operation_failed(event, error, handles=handles,
                                        mutation=method in ('POST','PUT','PATCH','DELETE'))
            if has_cancellation(error):
                cancellation = KeyboardInterrupt()
                if getattr(error, 'child_shutdown_status', None) == 'UNCONFIRMED':
                    cancellation.child_shutdown_status = 'UNCONFIRMED'
                raise cancellation from error
            failure = ChannelCliDriverError('product CLI outcome is unconfirmed; no retry, adoption, or cleanup')
            if getattr(error, 'child_shutdown_status', None) == 'UNCONFIRMED':
                failure.child_shutdown_status = 'UNCONFIRMED'
            raise failure from None
        finally:
            primary = sys.exc_info()[1]
            token = raw_token = secret = ''
            if credential is None:
                credential = {'schema_version':1,'token':'','api_base_url':self.case.identities.origin}
            else:
                credential['token'] = ''; credential.pop('bot_uuid', None)
                credential['api_base_url'] = self.case.identities.origin
            try:
                if cleanup != 'NOT_WRITTEN':
                    write(context, credential); verify(credential); cleanup = 'CLEARED'
            except BaseException:
                cleanup = 'UNCONFIRMED'; self.case.invalidated = True
            event['credential_cleanup'] = cleanup
            event['credential_context'] = root.name if root is not None else 'NOT_CREATED'
            checkpoint = 'WRITTEN'
            try:
                self.case._save()
            except BaseException:
                checkpoint = 'UNCONFIRMED'; self.case.invalidated = True
            if cleanup == 'UNCONFIRMED' or checkpoint == 'UNCONFIRMED':
                failure = ChannelCliDriverError(
                    'private CLI credential cleanup or durable checkpoint is unconfirmed')
                failure.credential_cleanup_status = cleanup
                failure.final_checkpoint_status = checkpoint
                if primary is None:
                    raise failure from None
                primary.credential_cleanup_status = cleanup
                primary.final_checkpoint_status = checkpoint
                cause = primary_error(primary)
                cause.credential_cleanup_status = cleanup
                cause.final_checkpoint_status = checkpoint

    @staticmethod
    def _validate(kind: str, result, reply: dict, secret: str) -> dict:
        try:
            stdout = result.stdout.decode('utf-8'); stderr = result.stderr.decode('utf-8')
        except UnicodeError:
            raise ChannelCliDriverError('product CLI output was not UTF-8') from None
        if secret and (secret in stdout or secret in stderr):
            raise ChannelCliDriverError('channel bind secret appeared in product CLI output')
        if kind == 'bind-redacted-error':
            diagnostic = json.dumps(reply, sort_keys=True)
            if (result.returncode == 0 or reply.get('code') != 'bad_request'
                    or 'channel bridge is disabled' not in diagnostic
                    or 'channel bridge is disabled' not in stderr):
                raise ChannelCliDriverError('channel bind did not return the exact disabled-bridge failure')
            return {'kind':kind,'expected_nonzero':True,'disabled_bridge':True,
                    'stdout_bytes_retained':False,'stderr_bytes_retained':False,'secret_redacted':True}
        if result.returncode != 0:
            raise ChannelCliDriverError('product CLI unexpectedly returned nonzero')
        if kind == 'group-create-human':
            gid = identifier(reply.get('id'),'CLI Group ID')
            sid = identifier(reply.get('session_id'),'CLI implicit Session ID')
            if (not sid.startswith(gid+':') or re.findall(r'^  ID: (.+)$',stdout,re.M) != [gid]
                    or re.findall(r'^  Session: (.+)$',stdout,re.M) != [sid]
                    or 'Group created:' not in stdout):
                raise ChannelCliDriverError('CLI Group human output differs from the forwarded response')
            return {'kind':kind,'group_id':gid,'initial_session_id':sid,'raw_output_persisted':False}
        try: value = json.loads(stdout, object_pairs_hook=unique_keys)
        except (ValueError, json.JSONDecodeError):
            raise ChannelCliDriverError('product CLI did not emit one JSON value') from None
        if fingerprint(value) != fingerprint(reply):
            raise ChannelCliDriverError('product CLI JSON differs from the forwarded response')
        return {'kind':kind,'json_matches_forwarded_response':True,'raw_output_persisted':False}
