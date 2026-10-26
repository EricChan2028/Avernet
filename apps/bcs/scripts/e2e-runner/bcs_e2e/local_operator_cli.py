"""Finite operator CLI Story against one owned local runtime.

The parent story proves an operator coordinates the agent network
with the product CLI only: unauthenticated health, the complete
five-bot trusted-team fixture listing, targeted Bot reads, name
discovery, the visibility round-trip, session connect, the
non-mutating web onboard, a complete direct-onboard lifecycle with
exact deletion, the status round-trip, the create-group/get-group round-trip with creator-scoped group cleanup, a detached
chat handle, the paginated group listing and the friend listing
after an in-case friendship admission. The owned driver never
retries a CLI step, never adopts another environment's fixtures,
and never extracts credentials or token-bearing URLs into the
frozen journal.
"""
from __future__ import annotations

from http.client import HTTPConnection
import json
from pathlib import Path
import re
import secrets
import time
from urllib.parse import quote, urlencode, urlsplit
import uuid

from .bounded_process import ProcessCaptureError, capture
from .inventory import current_manifest, executor_digest
from .local_bot_ws import OwnedLocalBotWebSocket
from .local_cli import require_cli_binary
from .local_network_reporting import _exclusive
from .local_operator_cli_contract import (
    CASE_ID,
    REQUIREMENTS,
    SUITE,
    operator_cli_source_binding,
)
from .local_role_identity import OwnedLocalRoleIdentity
from .owned_bcs import OwnedBcsProcess, file_sha256
from .profile import fingerprint, identifier, unique_keys
from .run_state import RunStateError, read, write

ROLES = ('CEO', 'PM', 'ENG', 'QA', 'CS')
CLI_TIMEOUT_SECONDS = 20
HTTP_TIMEOUT_SECONDS = 10
VISIBILITY_LINE = re.compile(r'(?m)^Visibility: (public|protected|private)$')
GROUP_CREATE_ID = re.compile(r'(?m)^  ID: ([A-Za-z0-9_-]+)$')
RESOURCE_DISPOSITION = ('IN_CASE_GROUPS_AND_BOTS_DELETED_EXACTLY_ONCE_'
                        'OWNED_RUNTIME_SCOPED_FRIENDSHIP_AND_RUN')


def require(value, message='Operator CLI finite contract differs'):
    if not value:
        raise RunStateError(message)


class OwnedLocalOperatorCli:
    """Thirteen ordered product-CLI obligations with strict transitions."""

    ROLES = ROLES

    def __init__(self, identities, cli_binary: Path, cli_binary_sha256: str):
        require(type(identities) is OwnedLocalRoleIdentity
                and identities.fixture_set == 'trusted-team'
                and tuple(identities.roles) == ROLES
                and set(identities._verified) == set(ROLES),
                'Operator CLI requires freshly verified CEO/PM/ENG/QA/CS identities')
        identities._require_roles(ROLES)
        require(type(identities.runtime) is OwnedBcsProcess
                and identities.runtime.provider_sink is None,
                'Operator CLI requires one provider-free owned BCS')
        require(identities.runtime.store_messages is False,
                'Operator CLI requires a message-store-disabled owned runtime')
        require_cli_binary(cli_binary, cli_binary_sha256)
        self.identities = identities
        self.runtime = identities.runtime
        self.root = self.runtime.root
        self.run_id = self.runtime.run_id
        self.cli_binary = cli_binary.resolve()
        self.cli_binary_sha256 = cli_binary_sha256
        self.path = self.root/'local-operator-cli.json'
        self.binding_path = self.root/'operator-cli-binding.json'
        self.source = executor_digest()
        self.source_binding = operator_cli_source_binding()
        self.binding = self._binding()
        self.binding_digest = fingerprint(self.binding)
        self.fixture_digest = fingerprint(read(identities.fixtures.path))
        self.config_digest = file_sha256(self.runtime.inputs.config_path)
        config = json.loads(self.runtime.inputs.config_path.read_text())
        require(config.get('store_messages') is False,
                'Operator CLI requires a message-store-disabled owned runtime')
        self.human = identities.human
        self.mock_human_headers = not self.runtime.allow_ambient_local_identity
        target = urlsplit(identities.origin)
        require(target.scheme == 'http' and target.hostname == '127.0.0.1'
                and target.port and target.netloc == '127.0.0.1:'+str(target.port)
                and not target.path and not target.query and not target.fragment,
                'Operator CLI requires an owned local origin')
        self.port = target.port
        self.state = None
        self._digest = ''
        self._attempted = False
        self._secrets = [token for _, token in identities._entries.values() if token]
        self._active()
        require(not self.path.exists() and not self.path.is_symlink(),
                'Operator CLI journal already exists')

    def _binding(self):
        keys = {'schema_version', 'contract', 'suite', 'case_id', 'run_id',
                'executor_digest', 'bcs_binary_sha256', 'cli_binary_sha256',
                'source_binding', 'selected_requirement_ids', 'legacy_inventory',
                'runtime_dir', 'store_messages'}
        try:
            value = read(self.binding_path)
            require(set(value) == keys and value['schema_version'] == 1
                    and value['contract'] == SUITE and value['suite'] == SUITE
                    and value['case_id'] == CASE_ID and value['run_id'] == self.run_id
                    and value['executor_digest'] == self.source
                    and value['bcs_binary_sha256'] == self.runtime.binary_sha256
                    and value['cli_binary_sha256'] == self.cli_binary_sha256
                    and value['source_binding'] == self.source_binding
                    and value['selected_requirement_ids'] == list(REQUIREMENTS)
                    and value['legacy_inventory'] == current_manifest()['legacy_inventory']
                    and value['runtime_dir'] == str(self.root.resolve())
                    and value['store_messages'] is False,
                    'Operator CLI frozen binding differs')
            return value
        except RunStateError:
            raise
        except Exception:
            raise RunStateError('frozen Operator CLI binding is missing or changed') from None

    def _active(self):
        self.identities._require_roles(ROLES)
        require_cli_binary(self.cli_binary, self.cli_binary_sha256)
        require(self.runtime.state == 'RUNNING' and self.runtime.ready
                and self.runtime.child is not None
                and self.runtime.child.poll() is None
                and self.runtime._owns_listener(1)
                and executor_digest() == self.source
                and file_sha256(self.runtime.inputs.config_path) == self.config_digest
                and fingerprint(read(self.identities.fixtures.path)) == self.fixture_digest
                and operator_cli_source_binding() == self.source_binding
                and fingerprint(self._binding()) == self.binding_digest,
                'Operator CLI runtime, source or fixture binding changed')
        if self._digest:
            require(fingerprint(read(self.path)) == self._digest,
                    'Operator CLI evidence changed')

    def _save(self):
        if self._digest:
            require(fingerprint(read(self.path)) == self._digest,
                    'Operator CLI evidence changed')
        raw = json.dumps(self.state)
        for secret in self._secrets:
            require(secret not in raw, 'credential in Operator CLI evidence')
        write(self.path, self.state)
        self._digest = fingerprint(self.state)

    def _id(self, role):
        self._active()
        return self.identities._entries[role][0]

    def _token(self, role):
        self._active()
        token = self.identities._entries[role][1]
        require(isinstance(token, str) and 1 <= len(token) <= 4096,
                'Operator CLI fixture credential is missing')
        return token

    def _http(self, method: str, path: str, *, token: str = '',
              body: bytes | None = None, mock_human: bool = False):
        """One bounded authenticated HTTP observation; never retries."""
        self._active()
        headers = {'Accept': 'application/json',
                   'X-Request-ID': 'e2e-operator-'+uuid.uuid4().hex}
        if body is not None:
            headers['Content-Type'] = 'application/json'
        if self.mock_human_headers and mock_human:
            headers['X-Mock-User-Id'] = self.runtime.inputs.human_id
            headers['X-Mock-Nick-Name'] = 'E2E local test human'
        if token:
            headers['Authorization'] = 'Bearer '+token
        connection = HTTPConnection('127.0.0.1', self.port, timeout=HTTP_TIMEOUT_SECONDS)
        try:
            connection.request(method, path, body=body, headers=headers)
            response = connection.getresponse()
            raw = bytearray()
            deadline = time.monotonic()+HTTP_TIMEOUT_SECONDS
            with response:
                while True:
                    remaining = deadline-time.monotonic()
                    require(remaining > 0, 'Operator CLI HTTP deadline exceeded')
                    sock = connection.sock
                    if sock is not None:
                        sock.settimeout(remaining)
                    chunk = response.read1(min(4096, 65_537-len(raw)))
                    if not chunk:
                        break
                    raw.extend(chunk)
                    require(len(raw) <= 65_536, 'Operator CLI HTTP response exceeds budget')
                require(response.length in (None, 0), 'Operator CLI HTTP response truncated')
                code = response.status
        finally:
            connection.close()
        value = json.loads(raw.decode(), object_pairs_hook=unique_keys) if raw else None
        return code, value

    def _invoke_cli(self, *, arguments: list[str], token: str = '',
                    bot_id: str = '', json_mode: bool = False):
        """One bounded product-CLI execution in a fresh private context."""
        self._active()
        for argument in arguments:
            require(isinstance(argument, str) and argument and argument == argument.strip(),
                    'Operator CLI arguments must be non-empty trimmed strings')
        root = self.root/('operator-cli-'+uuid.uuid4().hex)
        root.mkdir(mode=0o700)
        (root/'home').mkdir(mode=0o700)
        (root/'bot').mkdir(mode=0o700)
        (root/'bot'/'.bcs').mkdir(mode=0o700)
        (root/'tmp').mkdir(mode=0o700)
        context = root/'bot'/'.bcs'/'session.json'
        write(context, {'schema_version': 1, 'token': token, 'bot_uuid': bot_id,
                        'api_base_url': self.identities.origin})
        environment = {'PATH': '/usr/bin:/bin', 'HOME': str(root/'home'),
                       'BOT_DATA_DIR': str(root/'bot'), 'TMPDIR': str(root/'tmp'),
                       'LANG': 'C', 'SERVER_ENV': 'local', 'tc_sdb_nenv': 'production'}
        command = [str(self.cli_binary)]
        if json_mode:
            command.append('--json')
        command += ['--log-level', 'error', *arguments]
        try:
            result = capture(command, cwd=root, environment=environment,
                             timeout_seconds=CLI_TIMEOUT_SECONDS, max_output_bytes=65_536)
        except ProcessCaptureError as error:
            raise RunStateError('product CLI exceeded its budget or failed to start') from error
        finally:
            write(context, {'schema_version': 1, 'token': '', 'bot_uuid': '',
                            'api_base_url': self.identities.origin})
        stdout = result.stdout.decode('utf-8', 'replace')
        return result.returncode, stdout, result.stderr.decode('utf-8', 'replace')

    def _begin(self, requirement: str, cli_command: str):
        self._active()
        require(self.state is not None
                and len(self.state['operations']) == len(self.state['assertions'])
                and self.state['assertions'] == [{'id': name, 'status': 'PASS'}
                                                  for name in REQUIREMENTS[:len(self.state['assertions'])]],
                'Operator CLI step order differs')
        row = {'operation': requirement, 'cli_command': cli_command,
               'status': 'INTENDED', 'transitions': ['INTENDED'],
               'observations': []}
        self.state['operations'].append(row)
        self._save()
        return row

    def _observe(self, row, observation):
        self._active()
        row['observations'].append(observation)
        self._save()

    def _finish(self, row):
        self._active()
        row['status'] = 'VERIFIED'
        row['transitions'].append('VERIFIED')
        self.state['assertions'].append({'id': row['operation'], 'status': 'PASS'})
        self._save()

    def _abort(self, row):
        row['status'] = 'UNKNOWN'
        row['transitions'].append('UNKNOWN')
        self._save()

    def _step_health(self):
        row = self._begin(REQUIREMENTS[0], 'bcs-cli health')
        try:
            code, out, err = self._invoke_cli(arguments=['health'])
            self._observe(row, {'kind': 'cli', 'command': 'bcs-cli health',
                                  'exit_code': code})
            require(code == 0, 'product CLI health exited nonzero')
            lowered = out.lower()
            require('status' in lowered or 'ok' in lowered or 'healthy' in lowered,
                    'product CLI health payload contract differs')
        except BaseException:
            self._abort(row)
            raise
        self._finish(row)

    def _step_list(self):
        row = self._begin(REQUIREMENTS[1], 'bcs-cli list')
        try:
            code, out, err = self._invoke_cli(arguments=['list'],
                                                token=self._token('CEO'),
                                                bot_id=self._id('CEO'))
            self._observe(row, {'kind': 'cli', 'command': 'bcs-cli list',
                                  'exit_code': code})
            require(code == 0, 'product CLI list exited nonzero')
            for role in ROLES:
                require(self._id(role) in out,
                        'product CLI list omits the '+role+' fixture Bot')
        except BaseException:
            self._abort(row)
            raise
        self._finish(row)

    def _step_get(self):
        row = self._begin(REQUIREMENTS[2], 'bcs-cli get <uuid>')
        try:
            target = self._id('CEO')
            code, out, err = self._invoke_cli(arguments=['get', target],
                                                token=self._token('CEO'),
                                                bot_id=target)
            self._observe(row, {'kind': 'cli', 'command': 'bcs-cli get <uuid>',
                                  'exit_code': code, 'target': target})
            require(code == 0 and target in out,
                    'product CLI get did not echo the requested fixture Bot')
        except BaseException:
            self._abort(row)
            raise
        self._finish(row)

    def _step_discover(self):
        row = self._begin(REQUIREMENTS[3], 'bcs-cli discover --query')
        try:
            fixture = read(self.identities.fixtures.path)
            cs_name = fixture['roles']['CS']['name']
            code, out, err = self._invoke_cli(
                arguments=['discover', '--query', cs_name],
                token=self._token('CEO'), bot_id=self._id('CEO'))
            self._observe(row, {'kind': 'cli', 'command': 'bcs-cli discover --query',
                                  'exit_code': code, 'query': cs_name})
            require(code == 0 and self._id('CS') in out,
                    'product CLI discover did not return the CS fixture Bot')
        except BaseException:
            self._abort(row)
            raise
        self._finish(row)

    def _step_visibility(self):
        row = self._begin(REQUIREMENTS[4], 'bcs-cli visibility get/set')
        try:
            code, out, err = self._invoke_cli(arguments=['visibility', 'get'],
                                                token=self._token('CEO'),
                                                bot_id=self._id('CEO'))
            self._observe(row, {'kind': 'cli', 'command': 'bcs-cli visibility get',
                                  'exit_code': code})
            require(code == 0, 'product CLI visibility get exited nonzero')
            before = VISIBILITY_LINE.search(out)
            require(before is not None,
                    'product CLI visibility get did not expose the original value')
            original = before.group(1)
            require(original in ('public', 'protected', 'private'),
                    'product CLI visibility original value is not a visibility level')
            code, out, err = self._invoke_cli(
                arguments=['visibility', 'set', '--value', 'public'],
                token=self._token('CEO'), bot_id=self._id('CEO'))
            self._observe(row, {'kind': 'cli', 'command': 'bcs-cli visibility set public',
                                  'exit_code': code})
            require(code == 0, 'product CLI visibility set public exited nonzero')
            code, out, err = self._invoke_cli(arguments=['visibility', 'get'],
                                                token=self._token('CEO'),
                                                bot_id=self._id('CEO'))
            self._observe(row, {'kind': 'cli', 'command': 'bcs-cli visibility get',
                                  'exit_code': code, 'observed': 'public'})
            require(code == 0 and VISIBILITY_LINE.search(out) is not None
                    and VISIBILITY_LINE.search(out).group(1) == 'public',
                    'product CLI visibility set public did not take effect')
            code, out, err = self._invoke_cli(
                arguments=['visibility', 'set', '--value', original],
                token=self._token('CEO'), bot_id=self._id('CEO'))
            self._observe(row, {'kind': 'cli', 'command': 'bcs-cli visibility set restore',
                                  'exit_code': code})
            require(code == 0, 'product CLI visibility restore exited nonzero')
            code, out, err = self._invoke_cli(arguments=['visibility', 'get'],
                                                token=self._token('CEO'),
                                                bot_id=self._id('CEO'))
            self._observe(row, {'kind': 'cli', 'command': 'bcs-cli visibility get',
                                  'exit_code': code, 'observed': original})
            require(code == 0 and VISIBILITY_LINE.search(out) is not None
                    and VISIBILITY_LINE.search(out).group(1) == original,
                    'product CLI visibility restore did not take effect')
            self.state['visibility_round_trip'] = {'original': original,
                                                     'round_trip_verified': True}
        except BaseException:
            self._abort(row)
            raise
        self._finish(row)

    def _step_connect(self):
        row = self._begin(REQUIREMENTS[5], 'bcs-cli connect')
        try:
            ceo_id = self._id('CEO')
            code, out, err = self._invoke_cli(arguments=['connect'],
                                                token=self._token('CEO'),
                                                bot_id=ceo_id, json_mode=True)
            self._observe(row, {'kind': 'cli', 'command': 'bcs-cli connect',
                                  'exit_code': code})
            require(code == 0, 'product CLI connect exited nonzero')
            payload = json.loads(out, object_pairs_hook=unique_keys)
            require(isinstance(payload, dict)
                    and payload.get('bot_uuid') == ceo_id
                    and isinstance(payload.get('is_new'), bool)
                    and isinstance(payload.get('token'), str)
                    and len(payload['token']) >= 1,
                    'product CLI connect response contract differs')
        except BaseException:
            self._abort(row)
            raise
        self._finish(row)

    def _step_onboard_web(self):
        row = self._begin(REQUIREMENTS[6], 'bcs-cli onboard --web')
        try:
            probe = 'operator-cli-web-'+secrets.token_hex(6)
            code, out, err = self._invoke_cli(
                arguments=['onboard', '--web', '--name', probe,
                             '--summary', 'operator e2e throwaway probe'],
                token=self._token('CEO'), bot_id=self._id('CEO'))
            self._observe(row, {'kind': 'cli', 'command': 'bcs-cli onboard --web',
                                  'exit_code': code, 'probe_suffix': probe[-6:]})
            require(code == 0, 'product CLI onboard --web exited nonzero')
            require('register' in out.lower() and probe in out,
                    'product CLI onboard --web did not print the registration URL')
            code, value = self._http('GET', '/bots?onboarded=true',
                                        mock_human=True)
            self._observe(row, {'kind': 'http', 'method': 'GET',
                                  'path': '/bots?onboarded=true',
                                  'http_status': code})
            require(code == 200 and isinstance(value, list),
                    'product CLI onboard --web independent read-back differs')
            require(probe not in json.dumps(value, ensure_ascii=False),
                    'product CLI onboard --web must not register a Bot')
        except BaseException:
            self._abort(row)
            raise
        self._finish(row)

    def _step_direct_onboard(self):
        row = self._begin(REQUIREMENTS[7], 'bcs-cli onboard')
        try:
            code, value = self._http('GET', '/register/token',
                                        mock_human=True)
            self._observe(row, {'kind': 'http', 'method': 'GET',
                                  'path': '/register/token',
                                  'http_status': code})
            require(code == 200 and isinstance(value, dict)
                    and isinstance(value.get('token'), str)
                    and len(value['token']) >= 1,
                    'CLI onboarding fixture did not obtain a register token')
            register_token = value['token']
            self._secrets.append(register_token)
            bot_name = 'operator-cli-direct-'+secrets.token_hex(6)
            path = '/register?'+urlencode({'token': register_token,
                                                'bot-name': bot_name})
            code, value = self._http('POST', path, body=b'{}',
                                        mock_human=True)
            self._observe(row, {'kind': 'http', 'method': 'POST',
                                  'path': '/register', 'http_status': code})
            require(code == 200 and isinstance(value, dict),
                    'CLI onboarding fixture did not register a temporary agent')
            bot_uuid = identifier(value.get('bot_uuid'),
                                    'CLI onboarding fixture Bot ID')
            bot_token = value.get('bot_token')
            require(isinstance(bot_token, str) and 1 <= len(bot_token) <= 4096,
                    'CLI onboarding fixture did not return a Bot token')
            self._secrets.append(bot_token)
            code, out, err = self._invoke_cli(
                arguments=['onboard', '--name', bot_name,
                             '--summary', 'Operator CLI direct onboarding',
                             '--skills', 'triage,review',
                             '--domains', 'release',
                             '--scopes', 'local'],
                token=bot_token, bot_id='', json_mode=True)
            self._observe(row, {'kind': 'cli', 'command': 'bcs-cli onboard',
                                  'exit_code': code})
            require(code == 0, 'product CLI direct onboard exited nonzero')
            payload = json.loads(out, object_pairs_hook=unique_keys)
            require(payload.get('bot_uuid') == bot_uuid
                    and payload.get('onboarded') is True
                    and payload.get('name') == bot_name,
                    'product CLI direct onboarding response differs')
            self.state['direct_onboard'] = {'bot_id': bot_uuid,
                                                'bot_name': bot_name,
                                                'onboarded': True}
            self.state['resources']['created'].append(
                {'kind': 'bot', 'id': bot_uuid, 'disposition': 'pending'})
            self._save()
            code, value = self._http('GET', '/bots/'+quote(bot_uuid, safe=''),
                                        mock_human=True)
            self._observe(row, {'kind': 'http', 'method': 'GET',
                                  'path': '/bots/{id}', 'http_status': code})
            require(code == 200 and isinstance(value, dict)
                    and value.get('capabilities', {}).get('name') == bot_name,
                    'directly onboarded agent is not queryable with its name')
            code, value = self._http('DELETE', '/bots/'+quote(bot_uuid, safe=''),
                                        mock_human=True)
            self._observe(row, {'kind': 'http', 'method': 'DELETE',
                                  'path': '/bots/{id}', 'http_status': code})
            require(code == 200, 'temporary CLI-onboarded agent was not deleted')
            code, value = self._http('GET', '/bots/'+quote(bot_uuid, safe=''),
                                        mock_human=True)
            self._observe(row, {'kind': 'http', 'method': 'GET',
                                  'path': '/bots/{id}', 'http_status': code})
            require(code == 404, 'deleted CLI-onboarded agent did not read 404')
            self.state['direct_onboard']['disposition'] = 'DELETED_404_VERIFIED'
            for resource in self.state['resources']['created']:
                if resource['id'] == bot_uuid:
                    resource['disposition'] = 'deleted'
            self.state['resources']['deleted'].append(
                {'kind': 'bot', 'id': bot_uuid, 'disposition': 'deleted'})
        except BaseException:
            self._abort(row)
            raise
        self._finish(row)

    def _step_update_status(self):
        row = self._begin(REQUIREMENTS[8], 'bcs-cli update-status')
        try:
            ceo_id = self._id('CEO')
            code, value = self._http('GET', '/bots/'+quote(ceo_id, safe=''),
                                        mock_human=True)
            registry_status = value.get('status') if value else None
            dynamic = (value.get('dynamic_status') or {}).get('status') \
                if isinstance(value, dict) else None
            self._observe(row, {'kind': 'http', 'method': 'GET',
                                  'path': '/bots/{id}', 'http_status': code,
                                  'observed': registry_status})
            require(code == 200 and registry_status in ('online', 'offline')
                    and dynamic in ('active', 'offline'),
                    'independent Bot read before update-status differs')
            code, out, err = self._invoke_cli(
                arguments=['update-status', '--status', 'idle'],
                token=self._token('CEO'), bot_id=ceo_id)
            self._observe(row, {'kind': 'cli', 'command': 'bcs-cli update-status idle',
                                  'exit_code': code})
            require(code == 0 and 'Status' in out,
                    'product CLI update-status idle was not accepted')
            code, value = self._http('GET', '/bots/'+quote(ceo_id, safe=''),
                                        mock_human=True)
            after_status = value.get('status') if isinstance(value, dict) else None
            after_dynamic = (value.get('dynamic_status') or {}).get('status') \
                if isinstance(value, dict) else None
            self._observe(row, {'kind': 'http', 'method': 'GET',
                                  'path': '/bots/{id}', 'http_status': code,
                                  'observed': after_status})
            # The status broadcast is echoed without being retained,
            # so the honest invariant is an unchanged registry record.
            require(code == 200 and after_status == registry_status
                    and after_dynamic == dynamic,
                    'product CLI update-status changed the Bot record')
            code, out, err = self._invoke_cli(
                arguments=['update-status', '--status', 'busy'],
                token=self._token('CEO'), bot_id=ceo_id)
            self._observe(row, {'kind': 'cli', 'command': 'bcs-cli update-status busy',
                                  'exit_code': code})
            require(code == 0 and 'Status' in out,
                    'product CLI update-status busy restore was not accepted')
            self.state['status_round_trip'] = {'registry_status': registry_status,
                                                     'dynamic_status': dynamic,
                                                     'broadcast': 'idle',
                                                     'restore': 'busy',
                                                     'registry_status_unchanged': True}
        except BaseException:
            self._abort(row)
            raise
        self._finish(row)

    def _parse_group_create(self, raw: bytes) -> dict:
        text = raw.decode("utf-8")
        if not text.startswith("Group created:\n"):
            raise RunStateError("product CLI create-group output prefix differs")
        group = GROUP_CREATE_ID.search(text)
        require(group is not None and group.group(1),
                "product CLI create-group did not return a Group ID")
        return {"group_id": identifier(group.group(1), "CLI-created Group ID"),
                "driver_bot": self._singleton_line(text, "Driver"),
                "participants": [value.strip() for value
                                 in self._singleton_line(text, "Participants").split(",")]}

    @staticmethod
    def _singleton_line(text: str, label: str) -> str:
        rows = re.findall(r"^  "+re.escape(label)+r": (.*)$", text, re.M)
        require(len(rows) == 1 and rows[0].strip(),
                "product CLI bounded line output differs")
        return rows[0].strip()

    def _step_create_group(self):
        row = self._begin(REQUIREMENTS[9], 'bcs-cli create-group + get-group')
        try:
            pm_id = self._id('PM')
            eng_id = self._id('ENG')
            topic = 'operator cli e2e group'
            code, out, err = self._invoke_cli(
                arguments=['create-group', '--driver', pm_id,
                           '--participants', pm_id+','+eng_id,
                           '--topic', topic],
                token=self._token('PM'), bot_id=pm_id)
            self._observe(row, {'kind': 'cli', 'command': 'bcs-cli create-group',
                                  'exit_code': code})
            require(code == 0, 'product CLI create-group exited nonzero')
            group = self._parse_group_create(out.encode('utf-8'))
            require(group['driver_bot'] == pm_id
                    and group['participants'] == [pm_id, eng_id],
                    'product CLI create-group participant projection differs')
            group_id = group['group_id']
            self.state['group_create'] = {'topic': topic,
                                                'group_id': group_id,
                                                'driver_bot': pm_id,
                                                'participants': [pm_id, eng_id]}
            self.state['resources']['created'].append(
                {'kind': 'group', 'id': group_id, 'disposition': 'pending'})
            self._save()
            code, out, err = self._invoke_cli(
                arguments=['get-group', '--id', group_id],
                token=self._token('PM'), bot_id=pm_id)
            self._observe(row, {'kind': 'cli', 'command': 'bcs-cli get-group',
                                  'exit_code': code})
            require(code == 0, 'product CLI get-group exited nonzero')
            require(group_id in out and pm_id in out and eng_id in out,
                    'product CLI get-group did not echo the exact owned Group')
            code, value = self._http(
                'DELETE',
                '/groups/'+quote(group_id, safe='')+'?bot_id='
                + quote(pm_id, safe=''),
                mock_human=True)
            self._observe(row, {'kind': 'http', 'method': 'DELETE',
                                  'path': '/groups/{id}?bot_id={driver}',
                                  'http_status': code})
            require(code == 200,
                    'creator-scoped DELETE did not remove the CLI-created group')
            code, value = self._http(
                'GET', '/groups/'+quote(group_id, safe=''),
                mock_human=True)
            self._observe(row, {'kind': 'http', 'method': 'GET',
                                  'path': '/groups/{id}', 'http_status': code})
            require(code == 404,
                    'deleted CLI-created group did not read 404')
            self.state['group_create']['disposition'] = 'DELETED_404_VERIFIED'
            for resource in self.state['resources']['created']:
                if resource['id'] == group_id:
                    resource['disposition'] = 'deleted'
            self.state['resources']['deleted'].append(
                {'kind': 'group', 'id': group_id, 'disposition': 'deleted'})
        except BaseException:
            self._abort(row)
            raise
        self._finish(row)

    def _admit_friendship(self, row, caller_role, target_role):
        """Establish one in-case friend pair; returns the request ID."""
        caller_id = self._id(caller_role)
        target_id = self._id(target_role)
        code, value = self._http(
            'POST', '/friends/request',
            body=json.dumps({'to_bot': target_id}).encode('ascii'),
            token=self._token(caller_role))
        self._observe(row, {'kind': 'http', 'method': 'POST',
                                  'path': '/friends/request',
                                  'http_status': code})
        # The route answers 201 Created for a new pending request
        # and 200 OK only when the pair is already friends, so both
        # admission statuses are accepted; the owned-local fixture is
        # fresh, which makes 201 the deterministic path.
        require(code in (200, 201) and isinstance(value, dict)
                and isinstance(value.get('data'), dict),
                caller_role+' friend request to '+target_role
                + ' was not admitted')
        request_id = identifier(value['data'].get('id'),
                                    'friend request ID')
        code, value = self._http(
            'POST', '/friends/requests/'+quote(request_id, safe='')
            +'/accept', body=b'{}', token=self._token(target_role))
        self._observe(row, {'kind': 'http', 'method': 'POST',
                                  'path': '/friends/requests/{id}/accept',
                                  'http_status': code})
        require(code == 200 and isinstance(value, dict)
                and value.get('success') is True,
                target_role+' did not accept the '+caller_role
                + ' friend request')
        self.state['resources']['created'].append(
            {'kind': 'friendship', 'id': caller_id+':'+target_id,
             'disposition': 'owned-runtime-scoped'})
        return request_id

    def _step_chat(self):
        row = self._begin(REQUIREMENTS[10], 'bcs-cli chat')
        try:
            pm_id = self._id('PM')
            # The parent story chats with the PM fixture Bot
            # because friends.sh already paired CEO<->PM in the
            # original release order. An owned-local Run seeds a
            # fresh trusted-team fixture with no prior
            # friendships, so the executor bootstraps the same
            # prerequisite pair in-case and records it.
            #
            # The owned runtime is provider-free: the fixture PM
            # Bot has no engine, so it is only effectively online
            # while a live streaming connection occupies the Bot
            # WebSocket registry. The executor reconnects the PM
            # fixture with its own stored session token for the
            # chat delivery window; the connection closes before
            # this step finishes, so the registry drops the Bot
            # again and no owned resource outlives the case.
            with OwnedLocalBotWebSocket(self.port, pm_id,
                                        self._token('PM')) as connection:
                self._observe(row, connection.observation())
                request_id = self._admit_friendship(row, 'CEO', 'PM')
                arguments = ['chat', '--bot-uuid', pm_id,
                                 '--message', 'operator e2e chat',
                                 '--detach']
                code, out, err = self._invoke_cli(arguments=arguments,
                                                      token=self._token('CEO'),
                                                      bot_id=self._id('CEO'),
                                                      json_mode=True)
                fallback = False
                if code != 0:
                    fallback = True
                    arguments = ['chat', '--bot-uuid', pm_id,
                                     '--message', 'operator e2e chat']
                    code, out, err = self._invoke_cli(
                        arguments=arguments, token=self._token('CEO'),
                        bot_id=self._id('CEO'), json_mode=True)
                self._observe(row, {'kind': 'cli',
                                      'command': 'bcs-cli chat',
                                      'exit_code': code,
                                      'detach_fallback': fallback})
                require(code == 0, 'product CLI chat exited nonzero')
                payload = json.loads(out, object_pairs_hook=unique_keys)
                run_id = identifier(payload.get('run_id'),
                                    'CLI chat run ID')
                session_id = identifier(payload.get('session_id'),
                                        'CLI chat session ID')
                self.state['chat'] = {'run_id': run_id,
                                        'session_id': session_id,
                                        'detach_fallback': fallback,
                                        'disposition': 'OWNED_RUNTIME_SCOPED',
                                        'prerequisite': {
                                            'caller_bot_id': self._id('CEO'),
                                            'target_bot_id': pm_id,
                                            'request_id': request_id,
                                            'admission': 'ACCEPTED',
                                            'connection': {
                                                'endpoint': connection.endpoint,
                                                'bot_id': pm_id,
                                                'request_id': connection.request_id,
                                                'reconnected': connection.reconnected}}}
                self.state['resources']['created'].append(
                    {'kind': 'chat_run', 'id': run_id,
                     'disposition': 'owned-runtime-scoped'})
        except BaseException:
            self._abort(row)
            raise
        self._finish(row)

    def _step_list_groups(self):
        row = self._begin(REQUIREMENTS[11], 'bcs-cli list-groups')
        try:
            code, out, err = self._invoke_cli(arguments=['list-groups'],
                                                  token=self._token('CEO'),
                                                  bot_id=self._id('CEO'))
            self._observe(row, {'kind': 'cli', 'command': 'bcs-cli list-groups',
                                  'exit_code': code})
            require(code == 0, 'product CLI list-groups exited nonzero')
            payload = json.loads(out, object_pairs_hook=unique_keys)
            require(isinstance(payload, dict),
                    'product CLI list-groups did not return an object')
            items = payload.get('items')
            returned = payload.get('returned')
            total = payload.get('total')
            has_more = payload.get('has_more')
            require(isinstance(items, list)
                    and type(returned) is int and returned == len(items)
                    and type(total) is int and total >= returned
                    and isinstance(has_more, bool)
                    and all(isinstance(item, dict)
                            and (item.get('group_id') or item.get('id'))
                            for item in items),
                    'product CLI list-groups page shape differs')
        except BaseException:
            self._abort(row)
            raise
        self._finish(row)

    def _step_friend_list(self):
        row = self._begin(REQUIREMENTS[12], 'bcs-cli friend list')
        try:
            ceo_id = self._id('CEO')
            eng_id = self._id('ENG')
            request_id = self._admit_friendship(row, 'CEO', 'ENG')
            code, out, err = self._invoke_cli(arguments=['friend', 'list'],
                                                  token=self._token('CEO'),
                                                  bot_id=ceo_id)
            self._observe(row, {'kind': 'cli', 'command': 'bcs-cli friend list',
                                  'exit_code': code})
            require(code == 0, 'product CLI friend list exited nonzero')
            require(eng_id in out,
                    'product CLI friend list did not contain the ENG fixture Bot')
            self.state['friendship'] = {'caller_bot_id': ceo_id,
                                              'target_bot_id': eng_id,
                                              'request_id': request_id,
                                              'admission': 'ACCEPTED'}
        except BaseException:
            self._abort(row)
            raise
        self._finish(row)

    def run(self):
        require(not self._attempted, 'Operator CLI execution cannot repeat')
        self._attempted = True
        self._active()
        fixture = read(self.identities.fixtures.path)
        self.state = {
            'schema_version': 1, 'run_id': self.run_id, 'case_id': CASE_ID,
            'status': 'RUNNING', 'executor_digest': self.source,
            'binding_digest': self.binding_digest,
            'source_binding_digest': fingerprint(self.source_binding),
            'human_actor': 'human_'+self.human,
            'driver_bot_id': self._id('CEO'),
            'fixture_roles': list(ROLES),
            'role_bot_ids': {role: self._id(role) for role in ROLES},
            'fixture_names': {role: fixture['roles'][role]['name']
                                   for role in ROLES},
            'cli_binary_sha256': self.cli_binary_sha256,
            'cli_timeout_seconds': CLI_TIMEOUT_SECONDS,
            'operations': [], 'assertions': [],
            'visibility_round_trip': None, 'direct_onboard': None,
            'group_create': None, 'chat': None, 'friendship': None,
            'resources': {'created': [], 'deleted': [], 'unknown': []},
            'resource_disposition': 'UNVERIFIED'}
        _exclusive(self.path, self.state)
        self._digest = fingerprint(self.state)
        try:
            self._step_health()
            self._step_list()
            self._step_get()
            self._step_discover()
            self._step_visibility()
            self._step_connect()
            self._step_onboard_web()
            self._step_direct_onboard()
            self._step_update_status()
            self._step_create_group()
            self._step_chat()
            self._step_list_groups()
            self._step_friend_list()
            self.state.update(status='LOCAL_OPERATOR_CLI_PASS',
                              resource_disposition=RESOURCE_DISPOSITION)
        except BaseException as error:
            self.state.update(status='BLOCKED',
                              error_type=type(error).__name__)
            if isinstance(error, (KeyboardInterrupt, SystemExit)):
                raise
            raise RunStateError('Operator CLI Story blocked; no retry or cleanup') from None
        finally:
            self._save()
        return json.loads(json.dumps(self.state))
