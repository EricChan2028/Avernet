"""Two-call product-CLI boundary for original Session completion helpers."""
from __future__ import annotations
import json
from pathlib import Path
import re
import sys
import uuid

from .bounded_process import capture
from .local_cli import require_cli_binary
from .local_cli_exchange import LocalCliExchange
from .profile import fingerprint, identifier
from .run_state import write


class CompletionCliError(RuntimeError):
    pass


class CompletionCliDriver:
    def __init__(self, case, binary: Path, binary_sha256: str):
        require_cli_binary(binary, binary_sha256)
        self.case, self.binary, self.digest = case, binary.resolve(), binary_sha256

    @staticmethod
    def _token(value):
        if (not isinstance(value, str) or not value or len(value) > 4096 or not value.isascii()
                or any(ord(c) <= 32 or ord(c) == 127 for c in value)):
            raise CompletionCliError('owned CLI Bot token is missing or invalid')
        return value

    def require_pm_token(self):
        self.case._active()
        self._token(self.case.identities._entries['PM'][1])

    def create_group(self, helper: str, handles: tuple[str, str], request_id: str) -> dict:
        self.case._active()
        require_cli_binary(self.binary, self.digest)
        pm, token = self.case.identities._entries['PM']
        eng = self.case.identities._entries['ENG'][0]
        token = self._token(token)
        operation = helper + '-group-create'
        topic = 'e2e-session-completion-' + helper
        body = {'id': None, 'label': None, 'driver_bot': pm,
                'participants': [{'bot_uuid': pm, 'role': None},
                                 {'bot_uuid': eng, 'role': None}],
                'topic': topic, 'create_initial_session': True}
        arguments = ['create-group', '--driver', pm, '--participants', pm + ',' + eng,
                     '--topic', topic]
        self.case._admit(operation, 'POST', '/groups', body, 'PM', 200, handles,
                         'PRODUCT_CLI', arguments)
        event = self.case._append(operation, 'POST', '/groups', True, handles, request_id,
                                  'PRODUCT_CLI')
        root = self.case.root / ('session-completion-cli-' + uuid.uuid4().hex)
        root.mkdir(mode=0o700)
        for name in ('home', 'bot', 'tmp'):
            (root/name).mkdir(mode=0o700)
        context_dir = root/'bot'/'.bcs'; context_dir.mkdir(mode=0o700)
        context = context_dir/'session.json'
        dirs = {p: (p.stat().st_dev, p.stat().st_ino) for p in (root, root/'bot', context_dir)}
        credential = {'schema_version': 1, 'token': token, 'bot_uuid': pm, 'api_base_url': ''}
        environment = {'PATH': '/usr/bin:/bin', 'HOME': str(root/'home'),
                       'BOT_DATA_DIR': str(root/'bot'), 'TMPDIR': str(root/'tmp'),
                       'LANG': 'C', 'SERVER_ENV': 'local', 'tc_sdb_nenv': 'production'}
        result = exchange = None
        cleanup = 'NOT_WRITTEN'
        def verify(expected):
            for path, inode in dirs.items():
                stat = path.stat()
                if path.is_symlink() or not path.is_dir() or (stat.st_dev, stat.st_ino) != inode:
                    raise CompletionCliError('private CLI context directory changed')
            if context.is_symlink() or fingerprint(json.loads(context.read_text())) != fingerprint(expected):
                raise CompletionCliError('private CLI credential context changed')
        try:
            with LocalCliExchange(self.case, event, token=token, body=body) as exchange:
                credential['api_base_url'] = exchange.origin
                write(context, credential); cleanup = 'PENDING'; verify(credential)
                result = capture([str(self.binary), '--json', '--log-level', 'error', *arguments],
                                 cwd=root, environment=environment, timeout_seconds=12,
                                 max_output_bytes=65_536)
            self.case._active(); verify(credential)
            if (exchange.failure is not None or exchange.attempts != 1 or exchange.forwarded != 1
                    or result.returncode != 0 or event.get('http_status') != 200
                    or not isinstance(exchange.response, dict)):
                raise CompletionCliError('product CLI operation was not exactly confirmed')
            reply = exchange.response
            group_id = identifier(reply.get('id'), 'CLI Group ID')
            session_id = identifier(reply.get('session_id'), 'CLI implicit Session ID')
            stdout = result.stdout.decode('utf-8')
            if (reply.get('driver_bot') != pm or reply.get('participants') != [pm, eng]
                    or not session_id.startswith(group_id + ':')
                    or re.findall(r'^  ID: (.+)$', stdout, re.M) != [group_id]
                    or re.findall(r'^  Session: (.+)$', stdout, re.M) != [session_id]
                    or 'Group created:' not in stdout):
                raise CompletionCliError('CLI Group output differs from the live response')
            event.update(status='ACKNOWLEDGED', cli_exit_code=0, resource_binding='PENDING_RESPONSE_VALIDATION',
                         output_validation={'kind': 'group-create-human', 'group_id': group_id,
                                            'initial_session_id': session_id, 'raw_output_persisted': False})
            event['transitions'].append({'status': 'ACKNOWLEDGED', 'http_status': 200, 'cli_exit_code': 0})
            self.case._save()
            return reply
        except BaseException as error:
            self.case._failed(event, error, handles, True)
            raise CompletionCliError('product CLI outcome is unconfirmed; no retry, adoption, or cleanup') from None
        finally:
            primary = sys.exc_info()[1]
            token = ''; credential['token'] = ''; credential.pop('bot_uuid', None)
            credential['api_base_url'] = self.case.identities.origin
            try:
                if cleanup != 'NOT_WRITTEN':
                    write(context, credential); verify(credential); cleanup = 'CLEARED'
            except BaseException:
                cleanup = 'UNCONFIRMED'; self.case.invalidated = True
            event['credential_cleanup'] = cleanup; event['credential_context'] = root.name
            checkpoint = 'WRITTEN'
            try:
                self.case._save()
            except BaseException:
                checkpoint = 'UNCONFIRMED'; self.case.invalidated = True
            if cleanup == 'UNCONFIRMED' or checkpoint == 'UNCONFIRMED':
                failure = CompletionCliError(
                    'private CLI credential cleanup or durable checkpoint is unconfirmed')
                failure.credential_cleanup_status = cleanup
                failure.final_checkpoint_status = checkpoint
                if primary is None:
                    raise failure from None
                primary.credential_cleanup_status = cleanup
                primary.final_checkpoint_status = checkpoint
