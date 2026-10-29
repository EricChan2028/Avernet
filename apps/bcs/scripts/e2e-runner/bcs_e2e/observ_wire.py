"""Observ read-only wire validation; raw log content never leaves this adapter."""
from __future__ import annotations

from datetime import datetime
import html
import ipaddress
import json
from pathlib import PurePosixPath
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .profile import ConfigError, identifier, unique_keys

CONTRACT = 'observ-retained-ws-binding-v1'


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ConfigError('Observ: ' + message)


def binding(value: dict, environment: str) -> dict:
    fields = {'schema_version', 'contract', 'environment', 'source_environment', 'app_name',
              'from_uri', 'to_uri', 'log_directory', 'log_file', 'timezone'}
    require(isinstance(value, dict) and set(value) == fields, 'invalid binding fields')
    require(type(value['schema_version']) is int and value['schema_version'] == 1
            and value['contract'] == CONTRACT and value['environment'] == environment,
            'binding does not match the original environment')
    require(value['source_environment'] == 'PRE', 'this binding cannot select production instances')
    for key in fields - {'schema_version'}:
        require(isinstance(value[key], str) and 0 < len(value[key]) <= 256, 'invalid binding scalar')
    for key in ('environment', 'source_environment', 'app_name'):
        identifier(value[key], 'Observ binding identifier')
    uri = r'amodel://[A-Za-z][A-Za-z0-9_.]{0,120}'
    require(re.fullmatch(uri, value['to_uri']) is not None
            and re.fullmatch(uri + '/' + re.escape(value['app_name']), value['from_uri']) is not None,
            'binding requires discovered instance and entity-set URIs')
    directory = PurePosixPath(value['log_directory'])
    require(str(directory) == value['log_directory'] and '..' not in directory.parts
            and directory.is_relative_to('/home/admin/logs'), 'invalid log directory')
    require(re.fullmatch(r'[A-Za-z0-9_-]+\.log', value['log_file']) is not None, 'invalid log filename')
    try:
        ZoneInfo(value['timezone'])
    except (ZoneInfoNotFoundError, ValueError):
        raise ConfigError('Observ: unknown deployment timezone') from None
    return dict(value)


def server_page(value: dict, config: dict) -> tuple[list[dict], str | None]:
    require(isinstance(value, dict) and isinstance(value.get('data'), list)
            and isinstance(value.get('meta'), dict), 'invalid instance page')
    meta, rows = value['meta'], value['data']
    require(type(meta.get('returned')) is int and meta['returned'] == len(rows)
            and len(rows) <= 100 and meta.get('truncated') is False
            and type(meta.get('hasMore')) is bool, 'incomplete instance page')
    cursor = meta.get('nextCursor')
    if meta['hasMore']:
        require(isinstance(cursor, str) and 0 < len(cursor) <= 4096 and '\0' not in cursor,
                'instance pagination cursor missing')
    else:
        require(cursor in (None, ''), 'contradictory instance pagination')
        cursor = None
    selected = []
    for edge in rows:
        require(isinstance(edge, dict) and isinstance(edge.get('entityData'), dict)
                and edge.get('sourceUri') == config['from_uri'], 'invalid instance relation source')
        row = edge['entityData']
        require(edge.get('targetUri') == row.get('uri')
                and edge.get('uri') == row.get('uri'), 'instance relation target differs from its entity')
        require(isinstance(row, dict) and row.get('app') == config['app_name']
                and isinstance(row.get('env'), str) and isinstance(row.get('status'), str),
                'instance application or environment missing or mismatched')
        if row['env'] != config['source_environment'] or row['status'] != 'online':
            continue
        try:
            ip = str(ipaddress.ip_address(row['ip']))
        except (ValueError, KeyError, TypeError):
            raise ConfigError('Observ: invalid instance address') from None
        hostname = identifier(row.get('hostname'), 'Observ instance hostname')
        instance_uri = row.get('uri')
        require(isinstance(instance_uri, str) and instance_uri.startswith(config['to_uri'] + '/')
                and len(instance_uri) <= 384, 'instance URI differs from discovered entity set')
        selected.append({'ip': ip, 'hostname': hostname, 'uri': instance_uri})
    return selected, cursor


def log_target(value: dict, instance: dict, config: dict) -> str:
    require(isinstance(value, dict) and value.get('success') is True
            and isinstance(value.get('data'), dict), 'log discovery failed')
    data = value['data']
    require(data.get('ip') == instance['ip'], 'log discovery returned another target; no fallback')
    expected = config['log_directory'] + '/' + config['log_file']
    stack = list(data.get('dirTrees', [])) if isinstance(data.get('dirTrees'), list) else []
    found, visited = 0, 0
    while stack:
        node = stack.pop(); visited += 1
        require(visited <= 200 and isinstance(node, dict), 'invalid or oversized log directory tree')
        if node.get('dir') is False and node.get('fullPath') == expected:
            found += 1
        children = node.get('subs', [])
        require(isinstance(children, list), 'invalid log directory children')
        stack.extend(children)
    require(found == 1, 'exact message log not uniquely discovered')
    return expected


def ack_rows(value: dict, query: dict, instance: dict, delivery: dict, config: dict) -> list[dict]:
    require(isinstance(value, dict) and value.get('success') is True
            and isinstance(value.get('data'), dict), 'log query failed')
    data = value['data']; echo = data.get('query')
    require(isinstance(echo, dict) and all(echo.get(k) == query[k]
            for k in ('ip', 'fullPath', 'time', 'limit', 'keyword'))
            and echo.get('notKeyword', []) == [], 'log query echo differs from frozen selectors')
    rows = data.get('logs')
    require(isinstance(rows, list) and type(data.get('matched')) is int
            and data['matched'] == len(rows) and len(rows) < query['limit']
            and data.get('truncated', False) is False, 'log page may be truncated')
    result = []
    for row in rows:
        require(isinstance(row, dict) and row.get('__source__') == instance['ip']
                and row.get('__tag__:__hostname__') == instance['hostname'], 'log row target mismatch')
        epoch = row.get('__time__')
        require((type(epoch) is int and epoch >= 0) or
                (isinstance(epoch, str) and re.fullmatch(r'[0-9]{1,12}', epoch) is not None),
                'invalid log epoch')
        epoch = int(epoch)
        require(query['time'] <= epoch*1000 < query['time']+60000, 'log row outside queried minute')
        raw = row.get('content')
        require(isinstance(raw, str), 'invalid log row')
        try:
            require(len(raw.encode('utf-8')) <= 65536, 'oversized log row')
            try:
                event = json.loads(raw, object_pairs_hook=unique_keys)
            except json.JSONDecodeError:
                event = json.loads(html.unescape(raw), object_pairs_hook=unique_keys)
        except (ValueError, UnicodeError, RecursionError, ConfigError):
            raise ConfigError('Observ: log row is not supported JSON') from None
        require(isinstance(event, dict) and event.get('target') == 'bcs_message'
                and type(event.get('schema_version')) is int and event['schema_version'] == 1
                and event.get('event_type') == 'bot_accept' and event.get('status') == 'accepted',
                'log row is not a supported WS acknowledgement')
        timestamp = event.get('timestamp')
        require(isinstance(timestamp, str) and re.fullmatch(r'\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}', timestamp)
                is not None, 'invalid event timestamp')
        try:
            local = datetime.strptime(timestamp, '%Y-%m-%d %H:%M:%S').replace(tzinfo=ZoneInfo(config['timezone']))
            require(local.utcoffset() == local.replace(fold=1).utcoffset(), 'ambiguous event timezone')
            require(int(local.timestamp()) == epoch, 'event and platform timestamps disagree')
        except (ValueError, OverflowError):
            raise ConfigError('Observ: invalid event timestamp') from None
        fields = {'run_id': 'request_id', 'bot_id': 'target_bot_id', 'group_id': 'group_id', 'session_id': 'session_id'}
        require(all(event.get(k) == delivery[v] for k, v in fields.items()), 'ACK identity tuple mismatch')
        alias = event.get('accepted_run_id')
        if alias not in (None, ''):
            identifier(alias, 'Observ accepted Run alias')
        result.append({'event': 'WS_ACK', 'timestamp_ms': epoch*1000,
                       **{k: event[k] for k in fields}, 'accepted_run_id': alias})
    return result
