"""Owned-local PM/ENG setup for the next CLI session-file Story increment.

The actual product CLI creates one group with its legacy initial Session and one
explicit Session. The isolated runtime has no engine credentials or remote
bindings. Unknown create outcomes retain the database and are never retried or
adopted.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import signal
import uuid
from urllib.parse import quote
from urllib.request import Request

from .bounded_process import capture
from .correlation import record_response_id
from .errors import has_cancellation
from .inventory import executor_digest
from .ledger import Ledger
from .local_cli import require_cli_binary
from .local_cli_exchange import LocalCliExchange
from .local_fixtures import OwnedLocalFixtures
from .local_role_identity import OwnedLocalRoleIdentity
from .local_sqlite import OwnedSqliteDatabase
from .owned_bcs import OwnedBcsProcess
from .profile import fingerprint, identifier, unique_keys
from .run_state import read, write, RunStateError
from .trust_baseline import visibility

CASE_ID = 'LOCAL-CLI-FILE-SETUP-01'
REQUIRED = {
    'participant-public', 'group-create-output', 'group-api', 'group-stored',
    'initial-session-api', 'explicit-session-api', 'sessions-stored',
    'session-create-output', 'ledger-parent-chain',
}


class OwnedLocalFileSetup:
    """Reusable setup while the caller owns one live ``OwnedBcsProcess``."""

    def __init__(self, identities, binary, binary_sha256, *, case_id=CASE_ID):
        if case_id not in (CASE_ID, 'LOCAL-CLI-FILE-01'):
            raise RunStateError('unsupported owned CLI-file setup Case')
        self.case_id = case_id
        if (type(identities) is not OwnedLocalRoleIdentity
                or identities.fixture_set != 'cli-file'
                or set(identities._verified) != {'PM', 'ENG'}):
            raise RunStateError('CLI-file setup requires two freshly verified owned identities')
        require_cli_binary(binary, binary_sha256)
        self.identities = identities
        self.runtime = identities.runtime
        self.binary = binary
        self.binary_sha256 = binary_sha256
        self.source = executor_digest()
        self.fixture_digest = fingerprint(read(identities.fixtures.path))
        self.binding = fingerprint({
            'run_id': self.runtime.run_id,
            'fixture_digest': self.fixture_digest,
            'executor_digest': self.source,
            'cli_sha256': binary_sha256,
        })
        self.path = self.runtime.root / 'cli-file-setup.json'
        self.digest = ''
        self.invalidated = False
        self.state = {
            'schema_version': 1,
            'case_id': self.case_id,
            'run_id': self.runtime.run_id,
            'status': 'RUNNING',
            'binding_digest': self.binding,
            'executor_digest': self.source,
            'fixture_digest': self.fixture_digest,
            'participant_admission': {'status': 'NOT_ATTEMPTED'},
            'operations': [],
            'assertions': [],
            'resources': {},
            'ready_to_execute': False,
            'full_story_status': 'NOT_MIGRATED',
            'file_operations': 'NOT_RUN',
            'resource_disposition': 'RETAINED_LOCAL_DATABASE',
            'automatic_delete': False,
            'execution_scope': 'ISOLATED_NO_ENGINE_RUNTIME',
            'delivery_convergence': 'NOT_VERIFIED',
        }
        self.ledger = None

    def _journal_valid(self):
        if self.digest and fingerprint(read(self.path)) != self.digest:
            raise RunStateError('CLI-file setup journal drifted; preserve original artifact')

    def _save(self):
        self._journal_valid()
        write(self.path, self.state)
        self.digest = fingerprint(self.state)

    def _active(self):
        if self.invalidated:
            raise RunStateError('CLI-file setup authority invalidated')
        self.identities._require_roles(('PM', 'ENG'))
        if (executor_digest() != self.source
                or fingerprint(read(self.identities.fixtures.path)) != self.fixture_digest):
            raise RunStateError('CLI-file setup source or role binding changed')
        require_cli_binary(self.binary, self.binary_sha256)
        self._journal_valid()

    def _id(self, role):
        self._active()
        return self.identities._entries[role][0]

    def _assert(self, name, condition):
        self.state['assertions'].append({
            'id': self.case_id + '.' + name,
            'status': 'PASS' if condition else 'FAIL',
        })
        self._save()
        if not condition:
            raise RunStateError('CLI-file setup assertion failed')

    def _admit_participant(self):
        """Make the fresh owned ENG discoverable without creating relationships."""
        self._active()
        event = self.state['participant_admission']
        if event != {'status': 'NOT_ATTEMPTED'}:
            raise RunStateError('CLI-file participant admission cannot be retried')
        entry = self.identities._entries['ENG']
        eng, token = entry
        observed = visibility(self.identities._request('visibility', eng, token), eng)
        if observed != 'protected':
            raise RunStateError('fresh CLI-file participant visibility is not protected')
        request_id = 'e2e-file-visibility-' + uuid.uuid4().hex
        event.update({
            'operation': 'participant-visibility-public',
            'method': 'PUT',
            'path': '/bots/' + quote(eng, safe='') + '/visibility',
            'submitted_request_id': request_id,
            'status': 'INTENDED',
            'resource_binding': 'BOUND_OWNED_ENG',
        })
        self._save()
        body = b'{"visibility":"public"}'
        request = Request(
            self.identities.origin + event['path'],
            method='PUT',
            data=body,
            headers={
                'Authorization': 'Bearer ' + token,
                'Accept': 'application/json',
                'Content-Type': 'application/json',
                'X-Request-ID': request_id,
            },
        )
        try:
            with self.identities.opener.open(request, timeout=5) as response:
                event['http_status'] = response.status
                record_response_id(event, response.headers.get('x-request-id'), source='HEADER',
                                   sensitive_values=(token,))
                raw = response.read(65_537)
            self._active()
            value = json.loads(raw.decode('utf-8'), object_pairs_hook=unique_keys)
            if (len(raw) > 65_536 or event['http_status'] != 200
                    or not isinstance(value, dict) or value.get('success') is not True
                    or value.get('data') != {'bot_uuid': eng, 'visibility': 'public'}):
                raise RunStateError('CLI-file participant visibility was not exactly confirmed')
            if visibility(self.identities._request('visibility', eng, token), eng) != 'public':
                raise RunStateError('CLI-file participant visibility readback failed')
            event['status'] = 'ACKNOWLEDGED'
            self._assert('participant-public', True)
        except BaseException as error:
            event['status'] = 'UNKNOWN'
            event['error_type'] = type(error).__name__
            self.invalidated = True
            self._save()
            if has_cancellation(error):
                raise KeyboardInterrupt() from None
            raise RunStateError('CLI-file participant admission unconfirmed; retain without retry') from None

    def _invoke(self, operation):
        self._active()
        sequence = ('group-create', 'session-create')
        previous = [item['operation'] for item in self.state['operations']]
        if (operation not in sequence or previous != list(sequence[:len(previous)])
                or len(previous) >= len(sequence) or operation != sequence[len(previous)]):
            raise RunStateError('CLI-file setup operation is out of order or repeated')
        entry = self.identities._entries['PM']
        bot_id, token = entry
        for probe in ('visibility', 'friends', 'visibility'):
            self._active()
            value = self.identities._request(probe, bot_id, token)
            self._active()
            if probe == 'visibility':
                visibility(value, bot_id)
            elif not isinstance(value.get('data'), list):
                raise RunStateError('PM self-read is not valid')
        if entry != self.identities._entries['PM']:
            raise RunStateError('PM credential changed during admission')
        correlation = 'e2e-file-setup-' + uuid.uuid4().hex
        if operation == 'group-create':
            path = '/groups'
            body = {
                'id': None,
                'label': None,
                'driver_bot': bot_id,
                'participants': [
                    {'bot_uuid': bot_id, 'role': None},
                    {'bot_uuid': self._id('ENG'), 'role': None},
                ],
                'topic': 'e2e-cli-file-setup',
                'create_initial_session': True,
            }
            args = [
                'create-group', '--driver', bot_id,
                '--participants', bot_id + ',' + self._id('ENG'),
                '--topic', 'e2e-cli-file-setup',
            ]
            group_handle, initial_handle = self.ledger.group_creation_intent(
                self.case_id, 1, correlation)
            handles = {'group': group_handle, 'initial_session': initial_handle}
            expected_status = 200
        else:
            group = self.state['resources']['group']
            path = '/groups/' + quote(group['id'], safe='') + '/sessions'
            body = {'session_title': 'e2e-cli-file-workspace'}
            args = ['session', 'create', '--group', group['id'], '--title', body['session_title']]
            handles = {'session': self.ledger.intent(
                self.case_id, 1, 'session', correlation, group['handle'])}
            expected_status = 201
        event = {
            'operation': operation,
            'method': 'POST',
            'path': path,
            'submitted_request_id': correlation,
            'status': 'INTENDED',
            'handles': handles,
            'resource_binding': 'UNKNOWN',
            'credential_cleanup': 'NOT_YET_OBSERVED',
        }
        self.state['operations'].append(event)
        self._save()
        root = self.runtime.root / ('file-setup-cli-' + uuid.uuid4().hex)
        root.mkdir(mode=0o700)
        for name in ('home', 'bot', 'tmp'):
            (root / name).mkdir(mode=0o700)
        (root / 'bot' / '.bcs').mkdir(mode=0o700)
        context = root / 'bot' / '.bcs' / 'session.json'
        context_dirs = {
            item: (item.stat().st_dev, item.stat().st_ino)
            for item in (root, root / 'bot', context.parent)
        }
        credential = None

        def require_context(expected):
            for item, inode in context_dirs.items():
                metadata = item.stat()
                if (item.is_symlink() or not item.is_dir()
                        or (metadata.st_dev, metadata.st_ino) != inode):
                    raise RunStateError('CLI-file credential context directory changed')
            if fingerprint(read(context)) != fingerprint(expected):
                raise RunStateError('CLI-file credential file changed')

        try:
            with LocalCliExchange(self, event, token=token, body=body) as exchange:
                credential = {
                    'schema_version': 1,
                    'token': token,
                    'bot_uuid': bot_id,
                    'api_base_url': exchange.origin,
                }
                write(context, credential)
                require_context(credential)
                self._active()
                environment = {
                    'PATH': '/usr/bin:/bin',
                    'HOME': str(root / 'home'),
                    'BOT_DATA_DIR': str(root / 'bot'),
                    'TMPDIR': str(root / 'tmp'),
                    'LANG': 'C',
                    'SERVER_ENV': 'local',
                    'tc_sdb_nenv': 'production',
                }
                result = capture(
                    [str(self.binary), '--json', '--log-level', 'error', *args],
                    cwd=root,
                    environment=environment,
                    timeout_seconds=12,
                    max_output_bytes=65_536,
                )
            self._active()
            require_context(credential)
            if (exchange.failure is not None or exchange.forwarded != 1 or exchange.attempts != 1
                    or result.returncode != 0 or event.get('http_status') != expected_status
                    or not isinstance(exchange.response, dict)):
                raise RunStateError('CLI-file setup creation was not exactly confirmed')
            reply = exchange.response
            event['cli_exit_code'] = result.returncode
            if operation == 'group-create':
                group_id = identifier(reply.get('id'), 'CLI group ID')
                session_id = identifier(reply.get('session_id'), 'CLI initial Session ID')
                event['candidate_ids'] = {'group': group_id, 'initial_session': session_id}
                self._save()
                output = result.stdout.decode('utf-8')
                self._assert(
                    'group-create-output',
                    re.findall(r'^  ID: (.+)$', output, re.M) == [group_id]
                    and re.findall(r'^  Session: (.+)$', output, re.M) == [session_id]
                    and reply.get('driver_bot') == bot_id
                    and reply.get('participants') == [bot_id, self._id('ENG')]
                    and session_id.startswith(group_id + ':'),
                )
                self.ledger.observed(handles['group'], group_id, origin='created',
                                     actual_request_id=correlation)
                self.ledger.observed(handles['initial_session'], session_id, origin='created')
                self.state['resources'].update(
                    group={'id': group_id, 'handle': handles['group']},
                    initial_session={'id': session_id, 'handle': handles['initial_session']},
                )
            else:
                session_id = identifier(reply.get('session_id'), 'CLI explicit Session ID')
                event['candidate_ids'] = {'session': session_id}
                self._save()
                output = json.loads(result.stdout.decode('utf-8'), object_pairs_hook=unique_keys)
                group_id = self.state['resources']['group']['id']
                self._assert(
                    'session-create-output',
                    fingerprint(output) == fingerprint(reply)
                    and reply.get('id') == session_id
                    and reply.get('group_id') == group_id
                    and reply.get('session_title') == body['session_title']
                    and session_id.startswith(group_id + ':')
                    and session_id != self.state['resources']['initial_session']['id'],
                )
                self.ledger.observed(handles['session'], session_id, origin='created',
                                     actual_request_id=correlation)
                self.state['resources']['session'] = {
                    'id': session_id,
                    'handle': handles['session'],
                }
            event.update(status='ACKNOWLEDGED', resource_binding='BOUND')
            self._save()
        except BaseException as error:
            event['status'] = 'UNKNOWN'
            event['error_type'] = type(error).__name__
            self.invalidated = True
            if getattr(error, 'child_shutdown_status', None) == 'UNCONFIRMED':
                event['child_shutdown_status'] = 'UNCONFIRMED'
            if has_cancellation(error):
                raise KeyboardInterrupt() from None
            failure = RunStateError('CLI-file setup outcome unconfirmed; retain without retry')
            if event.get('child_shutdown_status') == 'UNCONFIRMED':
                failure.child_shutdown_status = 'UNCONFIRMED'
            raise failure from None
        finally:
            try:
                if credential is not None:
                    require_context(credential)
                    cleared = {
                        'schema_version': 1,
                        'token': '',
                        'api_base_url': self.identities.origin,
                    }
                    write(context, cleared)
                    require_context(cleared)
                event['credential_cleanup'] = 'CLEARED' if credential is not None else 'NOT_WRITTEN'
            except BaseException:
                self.invalidated = True
                event['credential_cleanup'] = 'UNCONFIRMED'
                raise
            finally:
                self._save()

    def _read(self, kind):
        self._active()
        resource = self.state['resources'][kind]['id']
        path = '/groups/' if kind == 'group' else '/sessions/'
        entry = self.identities._entries['PM']
        token = entry[1]
        request = Request(
            self.identities.origin + path + quote(resource, safe=''),
            headers={'Authorization': 'Bearer ' + token},
        )
        with self.identities.opener.open(request, timeout=5) as response:
            if response.status != 200:
                raise RunStateError('CLI-file setup readback failed')
            raw = response.read(65_537)
        self._active()
        if len(raw) > 65_536:
            raise RunStateError('CLI-file readback exceeds budget')
        return json.loads(raw.decode('utf-8'), object_pairs_hook=unique_keys)

    def _verify(self):
        pm, eng = self._id('PM'), self._id('ENG')
        group_id = self.state['resources']['group']['id']
        group = self._read('group')
        participants = group.get('participants', [])
        self._assert(
            'group-api',
            group.get('id') == group_id and group.get('driver_bot') == pm
            and isinstance(participants, list) and len(participants) == 2
            and {row.get('bot_uuid') for row in participants if isinstance(row, dict)} == {pm, eng},
        )
        for kind in ('initial_session', 'session'):
            row = self._read(kind)
            session_id = self.state['resources'][kind]['id']
            self._assert(
                'initial-session-api' if kind == 'initial_session' else 'explicit-session-api',
                row.get('id') == session_id and row.get('session_id') == session_id
                and row.get('group_id') == group_id and row.get('status') == 'running',
            )
        with OwnedSqliteDatabase(self.runtime)._connection() as database:
            database.execute('BEGIN')
            groups = [dict(row) for row in database.execute(
                'SELECT group_id,driver_bot,originator,status FROM bcs_groups '
                'WHERE env=? AND group_id=? LIMIT 2', ('local', group_id))]
            sessions = [dict(row) for row in database.execute(
                'SELECT session_id,group_id,status,session_title FROM bcs_group_sessions '
                'WHERE env=? AND group_id=? LIMIT 3', ('local', group_id))]
        self._active()
        self._assert(
            'group-stored',
            len(groups) == 1 and groups[0]['driver_bot'] == pm
            and groups[0]['originator'] == pm and groups[0]['status'] == 'active',
        )
        self._assert(
            'sessions-stored',
            len(sessions) == 2
            and {row['session_id'] for row in sessions} == {
                self.state['resources'][kind]['id'] for kind in ('initial_session', 'session')}
            and all(row['group_id'] == group_id and row['status'] == 'running' for row in sessions)
            and next(row for row in sessions
                     if row['session_id'] == self.state['resources']['session']['id'])['session_title']
            == 'e2e-cli-file-workspace',
        )
        resources = self.ledger.resource_snapshot()
        self._assert(
            'ledger-parent-chain',
            len(resources) == 3
            and [row['kind'] for row in resources].count('group') == 1
            and [row['kind'] for row in resources].count('session') == 2
            and all(row['origin'] == 'created' and row['state'] == 'live' for row in resources)
            and all(row['parent'] == self.state['resources']['group']['handle']
                    for row in resources if row['kind'] == 'session'),
        )

    def run(self):
        self._active()
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        os.close(fd)
        self._save()
        with Ledger(
                self.runtime.root / 'cli-file-resources.sqlite',
                self.runtime.run_id,
                self.binding,
                create=True) as ledger:
            self.ledger = ledger
            try:
                self._admit_participant()
                self._invoke('group-create')
                self._invoke('session-create')
                self._verify()
                ids = [row['id'] for row in self.state['assertions']]
                if (set(ids) != {self.case_id + '.' + key for key in REQUIRED}
                        or len(ids) != len(REQUIRED)
                        or any(row['status'] != 'PASS' for row in self.state['assertions'])
                        or self.state['participant_admission']['status'] != 'ACKNOWLEDGED'
                        or any(op['status'] != 'ACKNOWLEDGED'
                               or op['credential_cleanup'] != 'CLEARED'
                               for op in self.state['operations'])):
                    raise RunStateError('CLI-file setup evidence is incomplete')
                self.state['status'] = 'LOCAL_CLI_FILE_SETUP_PASS'
                self._save()
                return self.state
            except BaseException as error:
                self.invalidated = True
                cancelled = has_cancellation(error)
                self.state['status'] = 'CANCELLED' if cancelled else 'BLOCKED'
                try:
                    self._save()
                except BaseException as finalization:
                    cancelled = cancelled or has_cancellation(finalization)
                failure = (KeyboardInterrupt() if cancelled else
                           RunStateError('CLI-file setup failed; retain owned artifacts'))
                if any(op.get('child_shutdown_status') == 'UNCONFIRMED'
                       for op in self.state['operations']):
                    failure.child_shutdown_status = 'UNCONFIRMED'
                raise failure from None


def local_file_setup(*, binary: Path, binary_sha256: str, cli_binary: Path,
                     cli_binary_sha256: str, state_dir: Path, run_id: str,
                     allow_local_processes: bool, allow_fixture_write: bool,
                     allow_write: bool, confirm_run_id: str):
    """Direct smoke orchestration; callers may instead reuse the class above."""
    if not (allow_local_processes is True and allow_fixture_write is True
            and allow_write is True and confirm_run_id == run_id):
        raise RunStateError(
            'CLI-file setup requires process, fixture and business write permissions '
            'plus exact Run confirmation')
    identifier(run_id, 'CLI-file setup Run ID')
    require_cli_binary(cli_binary, cli_binary_sha256)
    executor_digest()
    if state_dir.exists() or state_dir.is_symlink():
        raise RunStateError('CLI-file setup requires a fresh state directory')
    previous = signal.signal(
        signal.SIGTERM,
        lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()),
    )
    try:
        with OwnedBcsProcess(
                binary=binary,
                binary_sha256=binary_sha256,
                root=state_dir.resolve(),
                run_id=run_id) as runtime:
            fixtures = OwnedLocalFixtures(runtime, fixture_set='cli-file')
            with OwnedLocalRoleIdentity(fixtures, roles=fixtures.roles) as identities:
                fixtures.apply(allow_write=True, role_identity=identities)
                for role in fixtures.roles:
                    identities.verify(role)
                case = OwnedLocalFileSetup(
                    identities, cli_binary, cli_binary_sha256).run()
        shutdown = runtime._record()
        if shutdown['status'] != 'STOPPED' or shutdown['returncode'] != 0:
            raise RunStateError('CLI-file setup child shutdown did not qualify')
        result = {
            'schema_version': 1,
            'status': 'LOCAL_CLI_FILE_SETUP_PASS',
            'run_id': run_id,
            'setup': case,
            'shutdown': shutdown,
            'ready_to_execute': False,
            'full_story_status': 'NOT_MIGRATED',
            'file_operations': 'NOT_RUN',
        }
        write(runtime.root / 'cli-file-setup-report.json', result)
        return result
    finally:
        signal.signal(signal.SIGTERM, previous)
