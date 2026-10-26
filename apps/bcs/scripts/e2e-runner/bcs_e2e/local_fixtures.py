"""Create-once real fixtures, exclusively inside a live runner-owned local BCS.

The legacy registration API is used explicitly for provisioning, not counted
as public-v1 Story coverage. No remote profile, origin, credentials or arbitrary
HTTP operation can be supplied to this handler.
"""
from __future__ import annotations

from collections.abc import Callable
import json
import os
import uuid
from urllib.parse import quote, urlencode
from urllib.request import Request, ProxyHandler, build_opener

from .owned_bcs import OwnedBcsProcess, file_sha256
from .profile import identifier
from .run_state import RunStateError, read, write
from .transport import NoRedirect


class LocalFixtureError(RuntimeError):
    pass


FIXTURE_SETS = {
    'pilot': ('driver', 'member'),
    'workspace': ('CEO', 'PM', 'ENG', 'QA'),
    'trusted-team': ('CEO', 'ENG', 'PM', 'QA', 'CS'),
    'cli-file': ('PM', 'ENG'),
}


def fixture_roles(fixture_set: str) -> tuple[str, ...]:
    if not isinstance(fixture_set, str) or fixture_set not in FIXTURE_SETS:
        raise LocalFixtureError('unsupported owned-local fixture set')
    return FIXTURE_SETS[fixture_set]


class OwnedLocalFixtures:
    def __init__(self, runtime: OwnedBcsProcess, *, fixture_set: str = 'pilot', registration=None):
        if registration is not None:
            from .trust_registration import TrustRegistration
            if type(registration) is not TrustRegistration or fixture_set != 'trusted-team':
                raise LocalFixtureError('registration is restricted to the owned trusted-team executor')
        self.registration = registration
        self.roles = fixture_roles(fixture_set)
        self.fixture_set = fixture_set
        if not isinstance(runtime, OwnedBcsProcess) or runtime.inputs is None:
            raise LocalFixtureError('fixture provisioning requires a live owned local runtime')
        self.runtime = runtime
        self.path = runtime.root / 'local-fixtures.json'
        self.config_digest = file_sha256(runtime.inputs.config_path)
        self._require_owned()
        self.opener = build_opener(ProxyHandler({}), NoRedirect())

    def _require_owned(self):
        if self.registration is not None:
            self.registration.validate(self.runtime)
        if (self.runtime.state != 'RUNNING' or not self.runtime.ready
                or self.runtime.child is None or self.runtime.child.poll() is not None
                or not self.runtime._owns_listener(1)
                or file_sha256(self.runtime.inputs.config_path) != self.config_digest):
            raise LocalFixtureError('fixture runtime ownership or configuration is no longer valid')

    def _request(self, operation: str, *, token: str = '', name: str = '', bot_id: str = '') -> dict:
        self._require_owned()
        if operation == 'identity':
            method, path, body = 'GET', '/auth/user', None
        elif operation == 'ensure-human':
            method, path, body = 'POST', '/me/ensure-human', b'{}'
        elif operation == 'registration-token':
            method, path, body = 'GET', '/register/token', None
        elif operation == 'register':
            method, path, body = 'POST', '/register?' + urlencode({'token': token, 'bot-name': name}), b'{}'
        elif operation == 'read-bot':
            identifier(bot_id, 'fixture bot ID')
            method, path, body = 'GET', '/bots/' + quote(bot_id, safe=''), None
        else:
            raise LocalFixtureError('unsupported local fixture operation')
        headers = {'Content-Type': 'application/json', 'Accept': 'application/json',
                   'X-Request-ID': 'e2e-fixture-' + uuid.uuid4().hex}
        if not self.runtime.allow_ambient_local_identity:
            # This never becomes the runtime's default identity. It is added only
            # to provisioning and receipt-read-back calls made by this bound
            # runtime owner, while unauthenticated case requests remain anonymous.
            headers['X-Mock-User-Id'] = self.runtime.inputs.human_id
            headers['X-Mock-Nick-Name'] = 'E2E local test human'
        request = Request(self.runtime.inputs.origin + path, data=body, method=method,
                          headers=headers)
        try:
            with self.opener.open(request, timeout=5) as response:
                if response.status != 200:
                    raise LocalFixtureError('local fixture operation was not confirmed')
                raw = response.read(1_048_577)
            if len(raw) > 1_048_576:
                raise LocalFixtureError('local fixture response exceeds limit')
            value = json.loads(raw)
            if not isinstance(value, dict):
                raise LocalFixtureError('local fixture response is not an object')
            return value
        except Exception:
            # Registration URLs and response tokens must never escape in errors.
            raise LocalFixtureError('local fixture HTTP operation failed; preserve runtime for inspection') from None

    def _identity(self):
        identity = self._request('identity')
        if identity.get('user_id') != self.runtime.inputs.human_id:
            raise LocalFixtureError('local authenticated Human differs from generated binding')

    def _verify_bot(self, role: str, binding: dict):
        bot = self._request('read-bot', bot_id=binding['bot_id'])
        if (bot.get('bot_uuid') != binding['bot_id'] or bot.get('created_by') != self.runtime.inputs.human_id
                or bot.get('actor_kind') != 'bot' or bot.get('env') != 'local'
                or bot.get('status') != 'online'
                or not isinstance(bot.get('capabilities'), dict)
                or bot['capabilities'].get('name') != binding['name']):
            raise LocalFixtureError(f'local fixture {role} failed identity/owner/state read-back')

    def apply(self, *, allow_write: bool = False,
              driver_token_sink: Callable[[str, str], None] | None = None,
              role_identity=None) -> dict:
        if role_identity is not None:
            from .local_role_identity import OwnedLocalRoleIdentity
            if type(role_identity) is not OwnedLocalRoleIdentity:
                raise LocalFixtureError('role credentials require the managed local identity lifecycle')
            if driver_token_sink is not None:
                role_identity.close()
                raise LocalFixtureError('role credentials cannot mix with the pilot token sink')
        try:
            if role_identity is not None:
                role_identity._begin(self)
            return self._apply(allow_write=allow_write, driver_token_sink=driver_token_sink,
                               role_identity=role_identity)
        except BaseException:
            if role_identity is not None:
                role_identity.close()
            raise

    def _apply(self, *, allow_write: bool,
               driver_token_sink: Callable[[str, str], None] | None, role_identity) -> dict:
        if driver_token_sink is not None and self.fixture_set != 'pilot':
            raise LocalFixtureError('the pilot driver credential sink cannot consume an expanded fixture set')
        if allow_write is not True:
            raise LocalFixtureError('local fixture apply requires explicit write permission')
        self._require_owned()
        try:
            fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        except FileExistsError:
            raise LocalFixtureError('local fixtures already attempted; inspect instead of retrying creation') from None
        os.close(fd)
        state = {'schema_version': 1, 'status': 'APPLYING', 'run_id': self.runtime.run_id,
                 'runtime_origin': self.runtime.inputs.origin, 'config_digest': self.config_digest,
                 'human': {'user_id': self.runtime.inputs.human_id}, 'roles': {},
                 'friendship': 'unchanged', 'api_face': 'legacy-local-provisioning',
                 'pending_operation': None, 'ready_to_execute': False}
        if self.fixture_set != 'pilot':
            state.update(fixture_set=self.fixture_set, fixture_contract='owned-local-role-fixtures-v1',
                         relationship_baseline='NOT_VERIFIED', story_admission='NOT_EVALUATED')
        write(self.path, state)
        try:
            self._identity()
            state['pending_operation'] = 'ensure-human'
            write(self.path, state)
            ensured = self._request('ensure-human')
            if (ensured.get('actor_uuid') != 'human_' + self.runtime.inputs.human_id
                    or ensured.get('failed_bots') != [] or ensured.get('errors') != []):
                raise LocalFixtureError('local Human initialization was not confirmed')
            state['human']['actor_id'] = ensured['actor_uuid']
            state['pending_operation'] = None
            write(self.path, state)
            response = self._request('registration-token')
            token = response.get('token')
            if not isinstance(token, str) or not token or len(token) > 4096:
                raise LocalFixtureError('local registration token is missing or invalid')
            for role in self.roles:
                name = f'e2e-{role}-{self.runtime.inputs.human_id}'
                state['pending_operation'] = 'register-' + role
                write(self.path, state)  # Unknown outcomes are never retried.
                created = self._request('register', token=token, name=name)
                bot_id = identifier(created.get('bot_uuid'), 'created local Bot ID')
                if bot_id in {binding['bot_id'] for binding in state['roles'].values()}:
                    raise LocalFixtureError('registration returned a duplicate Bot ID')
                # Only the explicit managed identity lifecycle may retain
                # selected role credentials. The default still discards them;
                # the existing pilot driver-only handoff remains unchanged.
                binding = {'bot_id': bot_id, 'owner_id': self.runtime.inputs.human_id,
                           'dedicated': True, 'name': name, 'origin': 'created'}
                state['roles'][role] = binding
                state['pending_operation'] = None
                write(self.path, state)
                self._verify_bot(role, binding)
                if role_identity is not None:
                    role_identity._capture(self, role, bot_id, created.get('bot_token'))
                if role == 'driver' and driver_token_sink is not None:
                    bot_token = created.get('bot_token')
                    if (not isinstance(bot_token, str) or not 1 <= len(bot_token) <= 4096
                            or not bot_token.isascii() or any(ord(c) <= 32 or ord(c) == 127 for c in bot_token)):
                        raise LocalFixtureError('local driver credential is missing or invalid')
                    driver_token_sink(bot_id, bot_token)
            state['status'] = 'APPLIED'
            write(self.path, state)
            if role_identity is not None:
                role_identity._seal(self)
            return state
        except BaseException:
            state['status'] = 'BLOCKED'
            write(self.path, state)
            raise

    def check(self) -> dict:
        """Read-only revalidation; never ensure, register, onboard or fix relations."""
        self._require_owned()
        try:
            state = read(self.path)
        except RunStateError:
            raise LocalFixtureError('local fixture receipt is invalid or unsupported') from None
        if self.fixture_set == 'pilot':
            contract_matches = 'fixture_set' not in state and 'fixture_contract' not in state
        else:
            contract_matches = (state.get('fixture_set') == self.fixture_set
                                and state.get('fixture_contract') == 'owned-local-role-fixtures-v1')
        if (not contract_matches or state.get('status') != 'APPLIED' or state.get('run_id') != self.runtime.run_id
                or state.get('runtime_origin') != self.runtime.inputs.origin
                or state.get('config_digest') != self.config_digest
                or state.get('human', {}).get('user_id') != self.runtime.inputs.human_id
                or set(state.get('roles', {})) != set(self.roles)):
            raise LocalFixtureError('local fixture declaration is incomplete or belongs to another runtime')
        roles = state['roles']
        ids = []
        for role, binding in roles.items():
            if (not isinstance(binding, dict) or binding.get('owner_id') != self.runtime.inputs.human_id
                    or binding.get('dedicated') is not True or binding.get('origin') != 'created'
                    or binding.get('name') != f'e2e-{role}-{self.runtime.inputs.human_id}'):
                raise LocalFixtureError('local fixture actor binding has drifted')
            ids.append(identifier(binding.get('bot_id'), 'fixture bot ID'))
        if len(set(ids)) != len(self.roles) or state.get('pending_operation') is not None:
            raise LocalFixtureError('local fixture actors are duplicated or creation is unresolved')
        self._identity()
        for role, binding in roles.items():
            self._verify_bot(role, binding)
        # Return a finite projection instead of arbitrary metadata from disk.
        return {'schema_version': 1, 'status': 'VERIFIED', 'run_id': self.runtime.run_id,
                **({'fixture_set': self.fixture_set, 'fixture_contract': 'owned-local-role-fixtures-v1',
                    'relationship_baseline': 'NOT_VERIFIED',
                    'story_admission': 'NOT_EVALUATED'} if self.fixture_set != 'pilot' else {}),
                'runtime_origin': self.runtime.inputs.origin, 'api_face': 'legacy-local-provisioning',
                'human': {'user_id': self.runtime.inputs.human_id,
                          'actor_id': 'human_' + self.runtime.inputs.human_id},
                'roles': {role: {key: binding[key] for key in ('bot_id', 'owner_id', 'dedicated', 'name', 'origin')}
                          for role, binding in roles.items()},
                'friendship': 'unchanged', 'ready_to_execute': False}
