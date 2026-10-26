"""Project the strict policy response onto only the two bound test actors.

A current policy snapshot is not historical dispatch coverage, downstream
completion, scheduler health or a lease. Do not persist unrelated Bot settings.
"""
from __future__ import annotations

from .profile import ConfigError, fingerprint, object_keys

I64_MAX = 2**63 - 1
U32_MAX = 2**32 - 1
BOT_FIELDS = {'mode', 'max_running', 'min_send_interval_ms', 'max_queued'}
FLOWS = {'group', 'system', 'task', 'direct_a2a', 'state_machine'}


def _integer(value, low, high):
    if type(value) is not int or not low <= value <= high:
        raise ConfigError('policy numeric field violates the reviewed contract')


def _bot(value: dict) -> dict:
    object_keys(value, BOT_FIELDS, 'effective Bot delivery configuration')
    if value['mode'] not in ('off', 'enforce'):
        raise ConfigError('unsupported Bot delivery policy mode')
    _integer(value['max_running'], 1, U32_MAX)
    _integer(value['max_queued'], 1, U32_MAX)
    _integer(value['min_send_interval_ms'], 0, I64_MAX)
    return value


def project_policy(value: dict, profile: dict) -> dict:
    object_keys(value, {'version', 'policy', 'updated_by', 'updated_at_ms'}, 'delivery policy record')
    _integer(value['version'], 0, I64_MAX)
    _integer(value['updated_at_ms'], 0, I64_MAX)
    if not isinstance(value['updated_by'], str):
        raise ConfigError('policy record updater has invalid type')
    policy = object_keys(value['policy'], {'flow_enabled', 'defaults', 'bots', 'queue_ttl_ms',
        'safe_retry', 'pause_dispatch', 'max_context_messages', 'max_context_bytes'}, 'delivery policy')
    switches = object_keys(policy['flow_enabled'], FLOWS, 'delivery flow switches')
    if (any(type(v) is not bool for v in switches.values()) or switches['state_machine']
            or switches['group'] != switches['system'] or type(policy['pause_dispatch']) is not bool):
        raise ConfigError('policy flow switches violate the reviewed source contract')
    _bot(policy['defaults'])
    _integer(policy['max_context_messages'], 1, 1024)
    _integer(policy['max_context_bytes'], 512, 16777216)
    if policy['queue_ttl_ms'] is not None:
        _integer(policy['queue_ttl_ms'], 1, I64_MAX)
    if policy['safe_retry'] is not None:
        retry = object_keys(policy['safe_retry'], {'max_retries', 'backoff_ms'}, 'safe retry')
        _integer(retry['max_retries'], 0, 10); _integer(retry['backoff_ms'], 1, I64_MAX)
    bots = policy['bots']
    if not isinstance(bots, dict) or len(bots) > 4096:
        raise ConfigError('policy Bot map is invalid or exceeds the bounded parser')
    if value['version'] == 0 and (any(switches.values()) or policy['defaults']['mode'] != 'off' or bots):
        raise ConfigError('synthetic version-zero policy cannot claim enabled delivery flows or overrides')
    resolved = {}
    for bot_id, override in bots.items():
        if not isinstance(bot_id, str) or not bot_id or bot_id != bot_id.strip():
            raise ConfigError('policy Bot key violates the reviewed source contract')
        if not isinstance(override, dict) or set(override) - BOT_FIELDS:
            raise ConfigError('policy Bot override contains unknown fields')
        resolved[bot_id] = _bot({**policy['defaults'], **{k: v for k, v in override.items() if v is not None}})
    actors = {}
    for role, actor in profile['actors'].items():
        effective = resolved.get(actor['bot_id'], policy['defaults'])
        actors[role] = {'bot_id': actor['bot_id'], 'configuration': dict(effective),
                        'managed_new_flows': {flow: enabled and effective['mode'] == 'enforce'
                                              for flow, enabled in switches.items()}}
    return {'version': value['version'], 'record_digest': fingerprint(value),
            'synthetic_disabled_version': value['version'] == 0,
            'updated_at_ms': value['updated_at_ms'], 'flow_enabled': dict(switches),
            'pause_dispatch': policy['pause_dispatch'], 'queue_ttl_ms': policy['queue_ttl_ms'],
            'safe_retry': policy['safe_retry'], 'actors': actors,
            'both_actors_group_system_managed': all(a['managed_new_flows']['group'] and
                a['managed_new_flows']['system'] for a in actors.values()),
            'meaning': 'CURRENT_RESPONDING_SERVICE_POLICY_ONLY',
            'historical_dispatch_coverage': 'NOT_PROVEN', 'scheduler_health': 'NOT_PROVEN',
            'whole_scope_completion': 'NOT_PROVEN'}
