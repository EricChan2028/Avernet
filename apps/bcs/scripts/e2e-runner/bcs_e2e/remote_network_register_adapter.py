"""Dual-environment adapter for the reviewed register-onboard core.

The adapter executes the reviewed register core of the Agent-network
Story from one admitted public-v1 profile: the Human mints a register
token, the minted token anonymously registers a new agent on the
public-v1 registration route, and the owning Human reads the
registered agent back to prove the exact reviewed name and Human
ownership. State is chained inside the adapter, the obligation runs
at most once, and neither the register token nor the returned Bot
token ever leaves the adapter or its evidence.
"""
from __future__ import annotations

import hashlib
import time
import uuid

from .case_adapters import AdapterCall
from .local_network_register_contract import (
    ADAPTER_METHOD,
    COMMUNITY_ONLY_CLOSURES,
    execute_network_register_case,
    FIXTURE_EFFECT,
    PUBLIC_OPERATIONS,
    SOURCE_REQUIREMENT_ID,
    network_register_case_plan,
)
from .profile import ConfigError, identifier
from .route_bindings import require_edge_case_binding
from .run_state import RunStateError
from .transport import ReadClient
from .write_transport import WriteClient

HUMAN_COOKIE_IDENTITY = 'AUTHENTICATED_HUMAN_COOKIE'
ANONYMOUS_REGISTER_IDENTITY = 'ANONYMOUS_REGISTER_TOKEN'
AGENT_NAME_PREFIX = 'story-agent-'
AGENT_NAME_SUFFIX_HEX = 16


class RemoteNetworkRegisterAdapter:
    """Execute the reviewed register-token, create and read-back core."""

    def __init__(self, profile: dict):
        if not isinstance(profile, dict):
            raise RunStateError('register adapter requires an admitted profile')
        entrypoint = profile.get('entrypoint', {})
        environment = profile.get('environment', {})
        valid_entrypoint = (
            entrypoint.get('kind') == 'singlebox' and environment.get('tier') == 'local'
            or entrypoint.get('kind') == 'gateway' and environment.get('tier') == 'preprod'
        )
        if not valid_entrypoint or entrypoint.get('api_face') != 'public-v1':
            raise RunStateError(
                'register adapter requires an admitted public-v1 Singlebox or gateway '
                'profile')
        self.plan = network_register_case_plan()
        self.bindings: dict[str, dict] = {}
        try:
            for operation in PUBLIC_OPERATIONS:
                self.bindings[operation] = require_edge_case_binding(profile, operation)
        except (ConfigError, ValueError) as error:
            raise RunStateError(str(error)) from None
        self.profile = profile
        profile_user_id = identifier(
            profile['human']['user_id'], 'register Human ID')
        self.human_actor = 'human_' + profile_user_id
        self.user_id = profile_user_id
        self.read_client = ReadClient(profile)
        self.write_client = WriteClient(profile)
        self._attempted = False
        self._register_token = ''
        self._bot_uuid = ''
        self._bot_name = ''
        self._bot_token = ''

    def register_owned_agent(self) -> AdapterCall:
        if self._attempted:
            raise RunStateError('register contract changed or obligation was repeated')
        self._attempted = True

        # 1. The Human mints a register token with its Cookie only.
        token_observation = self.read_client.read('register.token.read')
        token_data, token_record = token_observation.data, token_observation.evidence
        if (token_record.get('method') != 'GET'
                or token_record.get('http_status') != 200
                or token_record.get('credentials_sent') is not True
                or token_record.get('authorization_sent') is not False):
            raise RunStateError(
                'register token read transport evidence differs from the fixed contract')
        register_token = token_data.get('token')
        expires_at = token_data.get('expires_at')
        if (not isinstance(register_token, str) or not register_token
                or not isinstance(expires_at, int)
                or expires_at <= int(time.time() * 1000)):
            raise RunStateError(
                'register token mint response is outside its reviewed contract')
        self._register_token = register_token

        # 2. The minted token anonymously registers a new agent.
        bot_name = (AGENT_NAME_PREFIX + hashlib.sha256(
            uuid.uuid4().hex.encode()).hexdigest()[:AGENT_NAME_SUFFIX_HEX])
        registration, create_record = self.write_client.register_bot_create(
            token=register_token, bot_name=bot_name)
        if (create_record.get('method') != 'POST'
                or create_record.get('http_status') != 201
                or (create_record.get('credentials_sent') is not False
                    and create_record.get(
                        'platform_gateway_browser_context_sent') is not True)
                or create_record.get('authorization_sent') is not False):
            raise RunStateError(
                'register create transport evidence differs from the fixed contract')
        bot_uuid = registration.get('bot_uuid')
        bot_token = registration.get('bot_token')
        if (registration.get('bot_name') != bot_name
                or not isinstance(bot_uuid, str) or not bot_uuid
                or not isinstance(bot_token, str) or not bot_token):
            raise RunStateError(
                'registered agent response is outside its reviewed contract')
        self._bot_uuid, self._bot_name, self._bot_token = bot_uuid, bot_name, bot_token

        # 3. The owning Human reads the registered agent back.
        readback_observation = self.read_client.read(
            'register.bot.readback', bot_uuid)
        readback_data, readback_record = (
            readback_observation.data, readback_observation.evidence)
        if (readback_record.get('method') != 'GET'
                or readback_record.get('http_status') != 200
                or readback_record.get('bound_bot_id') != bot_uuid
                or readback_record.get('credentials_sent') is not True
                or readback_record.get('authorization_sent') is not False):
            raise RunStateError(
                'registered Bot read-back transport evidence differs from the fixed '
                'contract')
        created_by = readback_data.get('created_by')
        legacy_projection = (
            readback_data.get('bot_uuid') == bot_uuid
            and readback_data.get('actor_kind') == 'bot'
            and isinstance(readback_data.get('capabilities'), dict))
        station_projection = (
            readback_data.get('bot_id') == bot_uuid
            and readback_data.get('kind') == 'bot'
            and isinstance(readback_data.get('name'), str))
        if legacy_projection:
            capabilities = readback_data['capabilities']
            owner_actor_id = readback_data.get('owner_actor_id')
            exact_owner = (capabilities.get('name') == bot_name
                           and created_by == self.human_actor
                           and (owner_actor_id is None
                                or owner_actor_id == self.human_actor))
        elif station_projection:
            # Current OCB station public-v1 projects the Bot domain object with
            # bot_id/kind/name/created_by; created_by is the creator staff id.
            exact_owner = (readback_data['name'] == bot_name
                           and created_by == self.user_id)
        else:
            exact_owner = False
        if not exact_owner:
            raise RunStateError(
                'registered Bot read-back does not prove Human ownership of the exact '
                'agent')

        requests = [token_record, create_record, readback_record]
        for record, identity in zip(requests,
                                    (HUMAN_COOKIE_IDENTITY, ANONYMOUS_REGISTER_IDENTITY,
                                     HUMAN_COOKIE_IDENTITY)):
            record['identity'] = identity
        evidence = {
            'transport': 'public-v1-http',
            'requirement_id': SOURCE_REQUIREMENT_ID,
            'operation': ADAPTER_METHOD,
            'requests': requests,
            'identity': HUMAN_COOKIE_IDENTITY,
            'fixture_effect': FIXTURE_EFFECT,
            'register_token_sha256': hashlib.sha256(
                register_token.encode()).hexdigest(),
            'bot_token_sha256': hashlib.sha256(bot_token.encode()).hexdigest(),
            'community_only_closures': list(COMMUNITY_ONLY_CLOSURES),
        }
        secrets = [secret for client in (self.read_client, self.write_client)
                   for secret in client.headers.values()]
        if any(secret and secret in repr(evidence) for secret in secrets):
            raise RunStateError('register evidence must not expose a credential')
        if register_token in repr(evidence) or bot_token in repr(evidence):
            raise RunStateError('register evidence must not expose a credential')
        data = {'bot_uuid': bot_uuid, 'bot_name': bot_name,
                'owner_readback': True}
        return AdapterCall(data=data, evidence=evidence)

    def execute(self):
        return execute_network_register_case(self)
