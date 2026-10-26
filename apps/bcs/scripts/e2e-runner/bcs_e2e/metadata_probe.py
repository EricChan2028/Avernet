"""Explicit metadata preparation, never observer admission or business querying."""
from __future__ import annotations

import os
from pathlib import Path
import re
import time
from typing import Protocol

from .alisd_wire import metadata_identifier
from .alisddbquery import McporterAlisdDbQueryClient
from .evidence import ASSERTIONS
from .errors import primary_error
from .inventory import executor_digest
from .profile import ConfigError


class MetadataClient(Protocol):
    def db_list(self) -> dict: ...
    def table_list(self, database: str) -> dict: ...
    def table_describe(self, database: str, table: str) -> dict: ...


class MetadataProbeError(RuntimeError):
    pass


_PILOT_ASSERTIONS = ('group.persisted', 'session.persisted', 'file.persisted')

# Candidate read projections, not business-query authorization or proof of
# terminal state. In particular baas_bot_run has no env column: correlating it
# requires a separately reviewed exact downstream run identity, never a Bot scan.
EXECUTION_METADATA_FIELDS = {
    'bcs_group_sessions': ('session_id', 'group_id', 'env', 'status', 'completed_at',
                           'callback_status'),
    'bcs_messages': ('message_id', 'group_id', 'session_id', 'session_seq', 'env',
                     'sender_id', 'message_type', 'status', 'run_id'),
    'bcs_message_deliveries': ('delivery_id', 'env', 'source_message_id', 'target_bot_id',
                               'session_id', 'group_id', 'kind', 'status', 'state_version',
                               'attempt_no', 'request_id', 'run_id', 'downstream_run_id',
                               'bound_to_delivery_id', 'may_have_been_sent',
                               'available_at_ms', 'terminal_at_ms'),
    'bcs_chat_runs': ('env', 'run_id', 'bot_uuid', 'session_key', 'state', 'version',
                      'delivery_id', 'source_message_id', 'completion_policy',
                      'delivery_ack_at_ms', 'completed_at_ms'),
    'baas_bot_run': ('run_id', 'bot_id', 'status', 'completed_at', 'gmt_modified'),
    'baas_bot_run_queue': ('run_id', 'bot_id', 'session_id', 'status', 'env',
                            'last_heartbeat', 'gmt_modified'),
}


def _required_fields(scope: str) -> dict[str, tuple[str, ...]]:
    if scope == 'pilot':
        return {ASSERTIONS[name][0]: ASSERTIONS[name][1] for name in _PILOT_ASSERTIONS}
    if scope == 'execution':
        return EXECUTION_METADATA_FIELDS
    raise ConfigError('metadata scope must be pilot or execution')


def _server_name(value: object) -> str:
    # This command accepts only a configured server name, never mcporter flags,
    # inline tool expressions or endpoint references in the selector position.
    if not isinstance(value, str) or re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_-]{0,127}', value) is None:
        raise ConfigError('metadata server must be a simple configured server name')
    return value


def _names(response: object) -> list[str]:
    if (not isinstance(response, dict) or response.get('success') is not True
            or type(response.get('truncated', False)) is not bool or response.get('truncated', False)):
        raise MetadataProbeError('metadata list failed or is incomplete')
    values = response.get('data')
    if not isinstance(values, list) or len(values) > 10000:
        raise MetadataProbeError('metadata list is invalid')
    names = [metadata_identifier(value) for value in values]
    if len(set(names)) != len(names):
        raise MetadataProbeError('metadata list is ambiguous')
    return names


def discover_metadata(client: MetadataClient, *, server: str, database: str,
                      timeout_seconds: int = 30, scope: str = 'pilot') -> dict:
    """Normalized client seam; deliberately has no db_query/provider operation."""
    _server_name(server)
    metadata_identifier(database)
    _required_fields(scope)
    if type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 60:
        raise ConfigError('metadata timeout must be 1..60 seconds')
    required = _required_fields(scope)
    result = {'schema_version': 1, 'status': 'BLOCKED', 'ready_to_execute': False,
              'source': 'alisddbquery-metadata', 'server': server, 'database': database,
              'physical_environment_binding': 'UNVERIFIED', 'business_queries': 0,
              'compatibility_scope': 'required-column-presence-only',
              'executor_digest': executor_digest(), 'metadata_calls': 0,
              'maximum_metadata_calls': 2 + len(required), 'timeout_seconds_per_call': timeout_seconds,
              'cleanup_wait_seconds_per_call': 2, 'tables': []}
    if scope == 'execution':
        result.update(metadata_scope='execution', execution_status='UNCONFIRMED',
                      grants_business_query_authority=False, grants_write_authority=False,
                      tables_without_environment_column=[],
                      note='Column presence is not state semantics, target binding or execution completion.')
    started = time.monotonic()
    stage, table = 'db_list', None
    try:
        result['metadata_calls'] += 1
        if database not in _names(client.db_list()):
            raise MetadataProbeError('selected database is absent')
        stage = 'table_list'
        result['metadata_calls'] += 1
        tables = _names(client.table_list(database))
        if any(name not in tables for name in required):
            raise MetadataProbeError('a required pilot table is absent')
        for table, fields in required.items():
            stage = 'table_describe'
            result['metadata_calls'] += 1
            schema = client.table_describe(database, table)
            if (not isinstance(schema, dict) or schema.get('success') is not True
                    or type(schema.get('truncated', False)) is not bool or schema.get('truncated', False)):
                raise MetadataProbeError('table metadata failed or is incomplete')
            columns = schema.get('columns')
            digest = schema.get('schema_digest')
            if (not isinstance(columns, list) or not 1 <= len(columns) <= 512
                    or not isinstance(digest, str) or re.fullmatch('[0-9a-f]{64}', digest) is None):
                raise MetadataProbeError('table metadata contract is invalid')
            names = [metadata_identifier(column) for column in columns]
            if len(set(name.lower() for name in names)) != len(names):
                raise MetadataProbeError('column metadata is ambiguous')
            if scope == 'execution' and 'env' not in names:
                result['tables_without_environment_column'].append(table)
            missing = [field for field in fields if field not in names]
            result['tables'].append({'table': table, 'required_fields': list(fields),
                                     'column_count': len(names), 'ddl_capture_digest': digest,
                                     'status': 'MISSING_FIELDS' if missing else 'FIELDS_PRESENT',
                                     'missing_required_fields': missing})
            if missing:
                raise MetadataProbeError('required projection is incomplete')
        result['status'] = 'METADATA_COMPATIBLE'
    except Exception as error:
        cause = primary_error(error)
        if isinstance(cause, KeyboardInterrupt):
            raise cause from None
        # Tool responses/messages/DDL and unrelated names never enter the report.
        result.update(failed_stage=stage, error_type=type(error).__name__)
        if table is not None:
            result['failed_table'] = table
        if getattr(error, 'child_shutdown_status', None) == 'UNCONFIRMED':
            result['child_shutdown_status'] = 'UNCONFIRMED'
    finally:
        result['elapsed_ms'] = round((time.monotonic() - started) * 1000, 2)
    return result


def probe_db_metadata(*, server: str, database: str, executable: Path,
                      allow_read: bool = False, timeout_seconds: int = 30, scope: str = 'pilot') -> dict:
    if allow_read is not True:
        raise ConfigError('probe db-metadata requires --allow-read')
    _server_name(server)
    metadata_identifier(database)
    _required_fields(scope)
    if type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 60:
        raise ConfigError('metadata timeout must be 1..60 seconds')
    # Standard package managers expose the configured CLI through a symlink.
    # Resolve only the explicitly supplied executable, never discover via PATH.
    try:
        if not executable.is_absolute():
            raise ConfigError('metadata executable must be an absolute path')
        executable = executable.resolve(strict=True)
        if not executable.is_file() or not os.access(executable, os.X_OK):
            raise ConfigError('metadata executable must be an executable file')
    except OSError:
        raise ConfigError('metadata executable is unavailable') from None
    client = McporterAlisdDbQueryClient(server=server, executable=(str(executable),),
                                      timeout_seconds=timeout_seconds)
    return discover_metadata(client, server=server, database=database,
                             timeout_seconds=timeout_seconds, scope=scope)
