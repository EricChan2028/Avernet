"""Private, no-company-dependency configuration for an owned local BCS process.

This provisions runtime inputs only, not a prepared E2E profile or Bot fixture.
It deliberately does not read .env.local, a developer HOME, or existing secrets.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import secrets

from .profile import identifier
from .runtime import RuntimeContractError
from .local_panel_asset import LocalPanelAsset
from .local_template_asset import LocalTemplateAsset
from .local_provider_sink import OwnedLocalProviderSink
from .local_event_sink import OwnedLocalEventSink
from .local_judge_sandbox import OwnedLocalJudgeSandbox


@dataclass(frozen=True)
class LocalBcsInputs:
    root: Path
    config_path: Path
    origin: str
    human_id: str
    environment: dict[str, str] = field(repr=False)


def prepare_local_bcs_inputs(root: Path, *, run_id: str, port: int,
                             store_messages: bool = False,
                             panel_asset: LocalPanelAsset | None = None,
                             template_asset: LocalTemplateAsset | None = None,
                             provider_sink: OwnedLocalProviderSink | None = None,
                             event_sink: OwnedLocalEventSink | None = None,
                             allow_ambient_local_identity: bool = True,
                             loop_execution_enabled: bool = False,
                             judge_sandbox: OwnedLocalJudgeSandbox | None = None) -> LocalBcsInputs:
    """Create a fresh runtime directory, refusing to adopt or overwrite one."""
    identifier(run_id, 'local runtime run_id')
    if judge_sandbox is not None:
        if (type(judge_sandbox) is not OwnedLocalJudgeSandbox or provider_sink is not None
                or event_sink is not None or store_messages is not True):
            raise RuntimeContractError('Judge requires one owned sandbox, message storage and no other sinks')
        judge_sandbox.validate_live(run_id)
    if event_sink is not None:
        if type(event_sink) is not OwnedLocalEventSink or provider_sink is not None or store_messages is not True:
            raise RuntimeContractError('Eventing requires one owned receiver, message storage and no Provider sink')
        event_sink.validate_live(run_id)
    if provider_sink is not None:
        if type(provider_sink) is not OwnedLocalProviderSink:
            raise RuntimeContractError('provider sink requires an owned live receiver')
        provider_sink.validate_live(run_id)
    if panel_asset is not None:
        if type(panel_asset) is not LocalPanelAsset:
            raise RuntimeContractError('panel asset requires an explicit verified artifact input')
        panel_asset.validate()
    if template_asset is not None:
        if type(template_asset) is not LocalTemplateAsset:
            raise RuntimeContractError('template asset requires an explicit approved manifest input')
        template_asset.validate()
    if type(store_messages) is not bool:
        raise RuntimeContractError('local message storage selection must be boolean')
    if type(allow_ambient_local_identity) is not bool:
        raise RuntimeContractError('local identity selection must be boolean')
    if type(loop_execution_enabled) is not bool:
        raise RuntimeContractError('local loop execution selection must be boolean')
    if type(port) is not int or not 1024 <= port <= 65535:
        raise RuntimeContractError('local BCS port must be between 1024 and 65535')
    if root.is_symlink():
        raise RuntimeContractError('local runtime root cannot be a symlink')
    root = root.resolve()
    if '${' in str(root) or any(ord(c) < 32 for c in str(root)):
        raise RuntimeContractError('local runtime path contains configuration expansion characters')
    try:
        root.mkdir(mode=0o700, parents=True, exist_ok=False)
    except FileExistsError:
        raise RuntimeContractError('local runtime root already exists; preserve it for recovery') from None
    for name in ('bots', 'session-files', 'templates', 'logs', 'home', 'tmp'):
        (root / name).mkdir(mode=0o700)
    origin = f'http://127.0.0.1:{port}'
    # ensure-human requires an ASCII alphanumeric staff ID (not an actor UUID).
    human = 'e2ehuman' + hashlib.sha256(run_id.encode()).hexdigest()[:16]
    environment = {
        'PATH': '/usr/bin:/bin:/usr/sbin:/sbin',
        'HOME': str(root / 'home'), 'TMPDIR': str(root / 'tmp'),
        'LANG': 'C.UTF-8', 'TZ': 'UTC', 'SERVER_ENV': 'local', 'RUST_LOG': 'info',
        'BCS_DATA_DIR': str(root),
        'AVERNET_SECRET_PRINCIPAL_SIGNING_KEY_VALUE': secrets.token_hex(32),
        'BCS_SECRET_BCN_GROUP_SESSION_WS_JWT': secrets.token_hex(32),
        'BCS_E2E_INVITE_SECRET': secrets.token_hex(32),
        'BCS_E2E_SHARE_SECRET': secrets.token_hex(32),
    }
    config = {
        'bind': '127.0.0.1', 'port': port, 'bots_base_dir': str(root / 'bots'),
        'bcs_endpoint': origin, 'botchat_url': origin,
        'store_messages': store_messages, 'dingtalk_accounts': [], 'api_keys': [],
        'allowed_switch_provider_ids': [], 'onboard_binding_enabled': False,
        'group_chat_delay_min_ms': 0, 'group_chat_delay_max_ms': 0,
        'llm': {'type': 'none'}, 'leader_election': {'enabled': False},
        'cache': {'type': 'memory'},
        'database': {'type': 'sqlite', 'sqlite': {'path': str(root / 'bcs.sqlite')}},
        'secret': {'provider': 'env', 'providers': {'env': {'prefix': 'BCS_SECRET_'}}},
        'gateway_principal': {'issuers': ['gateway', 'backend'], 'audience': 'bcs',
                              'key_id': 'bare', 'signing_key_env': 'AVERNET_SECRET_PRINCIPAL_SIGNING_KEY_VALUE'},
        'group_session_ws': {'signing_key_secret': 'bcn-group-session-ws-jwt'},
        'auth': ({'chain': ['local', 'session'], 'require_authentication': False,
                  'mock_user_id': human, 'mock_user_name': 'E2E local test human',
                  'allow_mock_headers': False}
                 if allow_ambient_local_identity else
                 {'chain': ['local', 'session'], 'require_authentication': False,
                  'allow_mock_headers': True}),
        'auth_sdk': {'use_remote_login_check': False}, 'user_directory': {'enabled': False},
        'human_notify': {'providers': []}, 'bcsfuse': {'enabled': False},
        'eventing': {'enabled': False, 'dispatcher_enabled': False},
        'collaboration': {'loop_execution_enabled': loop_execution_enabled,
                          'templates': {'storage_type': 'file', 'base_dir': str(root / 'templates')}},
        'metrics': {'enabled': False},
        'logging': {'default_level': 'info', 'console': True, 'outputs': [
            {'name': 'e2e-access', 'path': str(root / 'logs'), 'file': 'e2e-access.jsonl',
             'level': 'info', 'rotation': 'daily', 'format': 'json',
             'targets': ['bcs_http_access'], 'max_keep_days': 0}]},
        'cors': {'allowed_origins': [origin]},
        'security_gateway': {'provider': 'noop'},
        'security': {'outbound_url': {'block_private_networks': True, 'allow_loopback': provider_sink is not None or judge_sandbox is not None}},
        'invite': {'token_secret': '${BCS_E2E_INVITE_SECRET}', 'public_claim_enabled': False,
                   'base_url': origin, 'group_link_url': origin + '/groups/{group_id}',
                   'session_link_url': origin + '/sessions/{session_id}'},
        'openapi_v1': {'public_collaboration_base_url': origin + '/api/v1/collaboration'},
        'session_files': {'storage_backend': 'local', 'max_file_size': 4096,
                          'backend': {'data_dir': str(root / 'session-files')},
                          'share': {'token_secret': '${BCS_E2E_SHARE_SECRET}', 'share_base_url': origin}},
        'manifest': {'schema_version': 1, 'bundles': []},
    }
    if judge_sandbox is not None:
        judge_sandbox.validate_live(run_id)
        environment['BCS_E2E_LOCAL_JUDGE_KEY'] = judge_sandbox.judge_api_key
        config['llm'] = {'type': 'openai_compatible', 'base_url': judge_sandbox.judge_base_url,
                         'api_key_env': 'BCS_E2E_LOCAL_JUDGE_KEY', 'model': 'owned-local-slow-judge',
                         'timeout_ms': 20000, 'temperature': 0.0, 'max_tokens': 1024}
    if event_sink is not None:
        event_sink.validate_live(run_id)
        config['eventing'] = {
            'enabled': True, 'dispatcher_enabled': True,
            'fanout_poll_interval_ms': 50, 'delivery_poll_interval_ms': 50,
            'worker_concurrency': 1, 'per_host_concurrency': 1,
            'lease_ms': 2000, 'drain_timeout_ms': 1000,
            'webhook': {'block_private_networks': True, 'allow_http_loopback': True,
                        'allow_non_standard_ports': True, 'private_endpoint_allowlist': [],
                        'max_event_body_bytes': 65536, 'max_response_body_bytes': 1024}}
    if panel_asset is not None:
        config['manifest']['bundles'] = [panel_asset.install(root, run_id=run_id)]
    if template_asset is not None:
        template_asset.install(root, run_id=run_id)
    path = root / 'bcs-config.json'
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as stream:
        json.dump(config, stream, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    return LocalBcsInputs(root, path, origin, human, environment)
