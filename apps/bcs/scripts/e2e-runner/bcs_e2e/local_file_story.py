"""Actual six-leaf CLI file Story in an exclusively owned local BCS.

No remote Profile or borrowed actors; no retry, group cleanup or hidden fallback.
The caller owns process shutdown; business File deletion remains a real asserted
CLI operation and cannot be replaced by teardown success.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import signal
import time
import uuid
from urllib.error import HTTPError
from urllib.parse import quote, urlencode
from urllib.request import Request

from .bounded_process import capture
from .errors import has_cancellation
from .file_assertions import FileExpectation
from .legacy_file_assertions import LegacyFileExpectation
from .inventory import executor_digest
from .ledger import Ledger
from .local_cli import require_cli_binary
from .local_file_setup import OwnedLocalFileSetup
from .local_fixtures import OwnedLocalFixtures
from .local_role_identity import OwnedLocalRoleIdentity
from .local_sqlite import OwnedSqliteDatabase
from .owned_bcs import OwnedBcsProcess
from .profile import fingerprint, identifier, unique_keys
from .run_state import RunStateError, read, write

CASE_ID = 'LOCAL-CLI-FILE-01'
SUITE = 'owned-local-cli-file-v1'
LEAVES = ('upload', 'list', 'capabilities', 'download', 'share', 'delete')
REQUIREMENTS = ('setup', 'upload', 'list', 'capabilities',
                'metadata-status', 'metadata-file-id', 'download', 'share',
                'shared-metadata-status', 'shared-metadata-hides-session',
                'shared-content-bytes', 'delete')
PAYLOAD = b'bcs-e2e-session-file-payload\n'


class OwnedLocalFileStory:
    def __init__(self, setup: OwnedLocalFileSetup, binding: dict):
        if (type(setup) is not OwnedLocalFileSetup or setup.case_id != CASE_ID
                or setup.state['status'] != 'LOCAL_CLI_FILE_SETUP_PASS'):
            raise RunStateError('CLI-file Story requires exact successful owned setup')
        self.setup, self.identities, self.runtime = setup, setup.identities, setup.runtime
        self.binding = binding
        self.binding_digest = fingerprint(binding)
        self.binary, self.binary_sha256 = setup.binary, setup.binary_sha256
        self.path = self.runtime.root / 'local-file-story.json'
        self.digest = ''
        self.invalidated = False
        self.file_id = self.file_handle = ''
        self.session_id = setup.state['resources']['session']['id']
        self.session_handle = setup.state['resources']['session']['handle']
        self.capability = ''  # In memory only; no output/receipt stores a share token.
        self.state = {'schema_version': 1, 'case_id': CASE_ID, 'case_version': 1,
            'run_id': self.runtime.run_id, 'binding_digest': self.binding_digest,
            'status': 'RUNNING', 'assertions': [], 'operations': [], 'http_observations': [],
            'setup': setup.state, 'file_id': None, 'file_handle': None,
            'session_id': self.session_id, 'file_delete': 'NOT_RUN',
            'resource_disposition': 'RETAINED_LOCAL_DATABASE', 'ready_to_execute': False,
            'full_release_status': 'NOT_QUALIFIED', 'automatic_cleanup': False}

    def _active(self):
        if self.invalidated:
            raise RunStateError('CLI-file Story authority is invalidated')
        self.setup._active()
        if (executor_digest() != self.binding['executor_digest']
                or fingerprint(read(self.runtime.root / 'file-story-binding.json')) != self.binding_digest):
            raise RunStateError('CLI-file execution binding changed')
        self._journal_valid()

    def _journal_valid(self):
        if self.digest and fingerprint(read(self.path)) != self.digest:
            raise RunStateError('CLI-file Story journal changed')

    def _save(self):
        self._journal_valid()
        write(self.path, self.state)
        self.digest = fingerprint(self.state)

    def _assert(self, requirement: str, condition: bool):
        self.state['assertions'].append({'id': 'CLI-FILE.' + requirement,
                                         'status': 'PASS' if condition else 'FAIL'})
        self._save()
        if not condition:
            raise RunStateError('CLI-file business assertion failed')

    def _ledger(self):
        # The HTTP capture invokes on_prepared in its own thread: open a new
        # bound connection there, never share a sqlite connection across threads.
        return Ledger(self.runtime.root / 'cli-file-resources.sqlite', self.runtime.run_id, self.setup.binding)

    def _prepared(self, value: str):
        self._active()
        if self.file_id:
            raise RunStateError('CLI upload cannot bind a second File')
        file_id = identifier(value, 'owned CLI File ID')
        with self._ledger() as ledger:
            ledger.observed(self.file_handle, file_id, origin='created')
        self.file_id = file_id
        self.state['file_id'] = file_id
        self._save()  # Durable exact File ID before CLI is allowed to PUT.

    def _stored(self, *, absent=False):
        self._active()
        with OwnedSqliteDatabase(self.runtime)._connection() as db:
            rows = [dict(row) for row in db.execute(
                'SELECT file_id,env,session_id,status,size,sha256,storage_backend '
                'FROM bcs_session_files WHERE env=? AND file_id=? LIMIT 2', ('local', self.file_id))]
        self._active()
        if absent:
            return rows == []
        if len(rows) != 1 or rows[0]['env'] != 'local' or rows[0]['status'] != 'Ready':
            return False
        row = {**rows[0], 'status': 'ready'}
        return self._expected().file.matches_persisted(row)

    def _expected(self):
        return LegacyFileExpectation(FileExpectation(
            self.file_id, self.session_id, 'source.txt', 'text/plain', len(PAYLOAD),
            hashlib.sha256(PAYLOAD).hexdigest(), self.identities._entries['PM'][0], 'local', owner_kind='bot'))

    def _http(self, operation: str):
        self._active()
        base = '/sessions/' + quote(self.session_id, safe='') + '/files/' + quote(self.file_id, safe='')
        anonymous = operation.startswith('shared-')
        if operation in ('metadata', 'deleted-metadata'):
            path = base
        elif operation in ('shared-metadata', 'shared-content', 'shared-deleted-metadata', 'shared-deleted-content'):
            content = operation in ('shared-content', 'shared-deleted-content')
            path = '/sessions/shared-file' + ('/content' if content else '')
            value = self.capability
            path += '?' + urlencode({'token': value})
        else:
            raise RunStateError('unsupported CLI-file HTTP observation')
        headers = {} if anonymous else {'Authorization': 'Bearer ' + self.identities._entries['PM'][1]}
        request = Request(self.identities.origin + path, method='GET', headers=headers)
        response = None
        try:
            try:
                response = self.identities.opener.open(request, timeout=5)
            except HTTPError as error:
                response = error
            raw = response.read(65537)
            status = response.code
        finally:
            if response is not None:
                response.close()
        self._active()
        self.state['http_observations'].append({'operation': operation, 'http_status': status,
            'anonymous': anonymous, 'resource_id': self.file_id, 'bytes': len(raw)})
        self._save()
        if len(raw) > 65536:
            raise RunStateError('CLI-file HTTP observation exceeds budget')
        if operation in ('deleted-metadata', 'shared-deleted-metadata', 'shared-deleted-content'):
            return status == 404
        if status != 200:
            raise RunStateError('CLI-file HTTP observation was not successful')
        return raw if operation == 'shared-content' else json.loads(raw, object_pairs_hook=unique_keys)

    def _last_status(self, operation: str) -> int | None:
        for row in reversed(self.state['http_observations']):
            if row.get('operation') == operation:
                return row.get('http_status')
        return None

    def _invoke(self, operation: str):
        from .local_file_exchange import LocalFileExchange
        self._active()
        index = len(self.state['operations'])
        if index >= len(LEAVES) or operation != LEAVES[index]:
            raise RunStateError('CLI-file operation is out of order or replayed')
        event = {'operation': operation, 'status': 'INTENDED', 'wire': [],
                 'submitted_request_id': 'cli-file-' + uuid.uuid4().hex,
                 'credential_cleanup': 'NOT_WRITTEN'}
        self.state['operations'].append(event)
        if operation == 'upload':
            with self._ledger() as ledger:
                self.file_handle = ledger.intent(CASE_ID, 1, 'file', 'file-upload-' + uuid.uuid4().hex, self.session_handle)
            self.state['file_handle'] = self.file_handle
        self._save()
        root = self.runtime.root / ('file-cli-' + uuid.uuid4().hex)
        root.mkdir(mode=0o700)
        for sub in ('home', 'bot', 'tmp'):
            (root / sub).mkdir(mode=0o700)
        (root / 'bot/.bcs').mkdir(mode=0o700)
        context = root / 'bot/.bcs/session.json'
        dirs = {p: (p.stat().st_dev, p.stat().st_ino) for p in (root, root/'bot', context.parent)}
        credential = None
        def require_context(expected):
            if any(p.is_symlink() or not p.is_dir() or (p.stat().st_dev, p.stat().st_ino) != inode
                   for p, inode in dirs.items()) or read(context) != expected:
                raise RunStateError('CLI-file temporary context changed')
        entry = self.identities._entries['PM']
        bot_id, token = entry
        output = root/'download.txt'
        args = ['session', 'file', operation, '--session', self.session_id]
        if operation == 'upload':
            source = root/'source.txt'
            fd = os.open(source, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, 'wb') as stream:
                stream.write(PAYLOAD)
            args += ['--path', str(source), '--name', 'source.txt', '--mime', 'text/plain']
        elif operation in ('download', 'share', 'delete'):
            args += ['--file-id', self.file_id]
        if operation == 'download':
            if output.exists() or output.is_symlink():
                raise RunStateError('CLI download requires a fresh private output path')
            args += ['--out', str(output)]
        try:
            with LocalFileExchange(self, event, token=token, operation=operation,
                    session_id=self.session_id, file_id=self.file_id if operation in ('download', 'share', 'delete') else '',
                    payload=PAYLOAD if operation == 'upload' else b'',
                    filename='source.txt' if operation == 'upload' else '', on_prepared=self._prepared if operation == 'upload' else None) as exchange:
                credential = {'schema_version': 1, 'bot_uuid': bot_id, 'token': token, 'api_base_url': exchange.origin}
                write(context, credential)
                require_context(credential)
                self._active()
                result = capture([str(self.binary), '--json', '--log-level', 'error', *args], cwd=root,
                    environment={'PATH': '/usr/bin:/bin', 'HOME': str(root/'home'), 'BOT_DATA_DIR': str(root/'bot'),
                        'TMPDIR': str(root/'tmp'), 'LANG': 'C', 'SERVER_ENV': 'local', 'tc_sdb_nenv': 'production'},
                    timeout_seconds=15, max_output_bytes=65536)
            self._active()
            require_context(credential)
            expected_count = 3 if operation == 'upload' else 1
            if (result.returncode != 0 or exchange.failure is not None
                    or exchange.attempts != expected_count or exchange.forwarded != expected_count):
                raise RunStateError('actual File CLI did not confirm the finite wire sequence')
            decoded = json.loads(result.stdout, object_pairs_hook=unique_keys)
            if operation == 'download':
                if (decoded != {'out': str(output)} or output.is_symlink() or not output.is_file()
                        or output.stat().st_size != len(PAYLOAD) or output.read_bytes() != PAYLOAD
                        or exchange.response != PAYLOAD):
                    raise RunStateError('CLI download output differs from original bytes')
            elif fingerprint(decoded) != fingerprint(exchange.response):
                raise RunStateError('CLI JSON differs from the actual upstream response')
            event.update(status='ACKNOWLEDGED', cli_exit_code=result.returncode)
            self._save()
            return decoded
        except BaseException as error:
            event.update(status='UNKNOWN', error_type=type(error).__name__)
            self.invalidated = True
            unknown_child = getattr(error, 'child_shutdown_status', None) == 'UNCONFIRMED'
            if unknown_child:
                event['child_shutdown_status'] = 'UNCONFIRMED'
            failure = (KeyboardInterrupt() if has_cancellation(error) else
                       RunStateError('CLI-file operation failed; retain without retry or fallback deletion'))
            if unknown_child:
                failure.child_shutdown_status = 'UNCONFIRMED'
            raise failure from None
        finally:
            try:
                if credential is not None:
                    require_context(credential)
                    cleared = {'schema_version': 1, 'token': '', 'api_base_url': self.identities.origin}
                    write(context, cleared)
                    require_context(cleared)
                event['credential_cleanup'] = 'CLEARED' if credential is not None else 'NOT_WRITTEN'
            except BaseException:
                self.invalidated = True
                event['credential_cleanup'] = 'UNCONFIRMED'
                raise
            finally:
                self._save()

    def run(self):
        self._active()
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        os.close(fd)
        self._save()
        try:
            self._assert('setup', all(a['status'] == 'PASS' for a in self.setup.state['assertions']))
            metadata = self._invoke('upload')
            self._assert('upload', bool(self.file_id) and self._expected().matches_metadata(metadata) and self._stored())
            self._assert('list', self._expected().matches_fresh_page(self._invoke('list')))
            self._assert('capabilities', self._expected().matches_local_capabilities(self._invoke('capabilities'), max_size=4096))
            observed_metadata = self._http('metadata')
            self._assert('metadata-status', self._last_status('metadata') == 200)
            self._assert('metadata-file-id', self._expected().matches_metadata(observed_metadata))
            self._invoke('download')
            self._assert('download', True)
            shared = self._invoke('share')
            capability = shared.get('share_token') if isinstance(shared, dict) else None
            self._assert('share', isinstance(capability, str) and bool(capability)
                         and isinstance(shared.get('share_url'), str) and bool(shared['share_url'])
                         and type(shared.get('expires_at')) is int and shared['expires_at'] > int(time.time()))
            self.capability = capability
            observed_shared_metadata = self._http('shared-metadata')
            self._assert('shared-metadata-status', self._last_status('shared-metadata') == 200)
            self._assert('shared-metadata-hides-session',
                         isinstance(observed_shared_metadata, dict)
                         and 'session_id' not in observed_shared_metadata
                         and self._expected().matches_shared_metadata(observed_shared_metadata))
            self._assert('shared-content-bytes',
                         self._expected().matches_bytes(self._http('shared-content')))
            self._invoke('delete')
            deleted_metadata = self._http('deleted-metadata')
            deleted_shared_metadata = self._http('shared-deleted-metadata')
            deleted_shared_content = self._http('shared-deleted-content')
            self._assert('delete', deleted_metadata and deleted_shared_metadata
                         and deleted_shared_content and self._stored(absent=True))
            with self._ledger() as ledger:
                ledger.mark_deleted(self.file_handle)
            self.state['file_delete'] = 'PASS'
            if ([a['id'] for a in self.state['assertions']] != ['CLI-FILE.'+r for r in REQUIREMENTS]
                    or any(a['status'] != 'PASS' for a in self.state['assertions'])
                    or any(op['status'] != 'ACKNOWLEDGED' or op['credential_cleanup'] != 'CLEARED' for op in self.state['operations'])):
                raise RunStateError('CLI-file requirement inventory is incomplete')
            self.state['status'] = 'PASS'
        except BaseException as error:
            self.state['status'] = 'CANCELLED' if has_cancellation(error) else 'FAIL'
            self.state['error_type'] = type(error).__name__
            self._save()
            if has_cancellation(error):
                raise KeyboardInterrupt() from None
            raise
        finally:
            self.capability = ''
            self._save()
        return self.state


def local_file_run(*, binary, binary_sha256, cli_binary, cli_binary_sha256, state_dir, run_id,
                   allow_local_processes, allow_fixture_write, allow_write, confirm_run_id):
    if not (allow_local_processes is True and allow_fixture_write is True and allow_write is True and confirm_run_id == run_id):
        raise RunStateError('CLI-file Story requires explicit owned process, fixture and business permissions')
    identifier(run_id, 'CLI-file Run ID')
    require_cli_binary(cli_binary, cli_binary_sha256)
    if state_dir.exists() or state_dir.is_symlink():
        raise RunStateError('CLI-file Story requires a fresh state directory')
    from .local_file_reporting import freeze_binding, publish
    previous = signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    runtime = None
    binding = None
    failure = None
    try:
        with OwnedBcsProcess(binary=binary, binary_sha256=binary_sha256, root=state_dir.resolve(), run_id=run_id) as runtime:
            binding = freeze_binding(runtime, cli_binary_sha256)
            fixtures = OwnedLocalFixtures(runtime, fixture_set='cli-file')
            with OwnedLocalRoleIdentity(fixtures, roles=fixtures.roles) as identities:
                fixtures.apply(allow_write=True, role_identity=identities)
                for role in fixtures.roles:
                    identities.verify(role)
                setup = OwnedLocalFileSetup(identities, cli_binary, cli_binary_sha256, case_id=CASE_ID)
                setup.run()
                OwnedLocalFileStory(setup, binding).run()
    except BaseException as error:
        failure = error
    finally:
        signal.signal(signal.SIGTERM, previous)
    if binding is None or runtime is None:
        if failure is not None:
            raise failure
        raise RunStateError('CLI-file runtime never acquired its execution binding')
    report = publish(runtime, binding, failure)
    if failure is not None and has_cancellation(failure):
        report['status'] = 'CANCELLED'
    return report
