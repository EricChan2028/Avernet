"""Observed alisd metadata wire format -> internal evidence client contract.

Only SHOW TABLES / SHOW CREATE TABLE metadata is interpreted. DDL is never
executed. Unsupported or ambiguous syntax fails closed instead of guessing.
"""
from __future__ import annotations

import hashlib
import re


class AlisdWireError(RuntimeError):
    pass


_NAME = re.compile(r'[A-Za-z_][A-Za-z0-9_]{0,127}\Z')
_HEADER = re.compile(r'\s*CREATE\s+TABLE\s+`([A-Za-z_][A-Za-z0-9_]*)`\s*\(', re.IGNORECASE)
_COLUMN = re.compile(r'`([A-Za-z_][A-Za-z0-9_]*)`\s+([A-Za-z]+)\b')
_TYPES = {'bigint', 'int', 'integer', 'smallint', 'tinyint', 'mediumint', 'bit', 'bool', 'boolean',
          'float', 'double', 'real', 'decimal', 'numeric', 'char', 'varchar', 'text', 'tinytext',
          'mediumtext', 'longtext', 'binary', 'varbinary', 'blob', 'tinyblob', 'mediumblob',
          'longblob', 'date', 'datetime', 'timestamp', 'time', 'year', 'json', 'enum', 'set'}
_CONSTRAINT = re.compile(r'(?:PRIMARY\s+KEY|UNIQUE\s+(?:KEY|INDEX)|KEY|INDEX|FULLTEXT\s+(?:KEY|INDEX)|'
                         r'SPATIAL\s+(?:KEY|INDEX)|CONSTRAINT|CHECK|FOREIGN\s+KEY)\b', re.IGNORECASE)


def metadata_identifier(value: object) -> str:
    if not isinstance(value, str) or _NAME.fullmatch(value) is None:
        raise AlisdWireError('metadata contains an unsupported identifier')
    return value


def _rows(payload: dict) -> list:
    if not isinstance(payload, dict) or payload.get('success') is not True:
        raise AlisdWireError('metadata tool returned failure')
    rows = payload.get('data')
    truncated = payload.get('truncated', False)
    if not isinstance(rows, list) or len(rows) > 10000 or type(truncated) is not bool or truncated:
        raise AlisdWireError('metadata rows are invalid, oversized or truncated')
    return rows


def database_list(payload: dict) -> dict:
    names = [metadata_identifier(value) for value in _rows(payload)]
    if len(names) != len(set(names)):
        raise AlisdWireError('database metadata is ambiguous')
    return {'success': True, 'data': names}


def table_list(payload: dict, database: str) -> dict:
    key = 'Tables_in_' + metadata_identifier(database)
    names = []
    for row in _rows(payload):
        if not isinstance(row, dict) or set(row) != {key}:
            raise AlisdWireError('SHOW TABLES response is not bound to the requested database')
        names.append(metadata_identifier(row[key]))
    if len(names) != len(set(names)):
        raise AlisdWireError('table metadata is ambiguous')
    return {'success': True, 'data': names}


# Only the table options needed by the observed MySQL/OceanBase metadata are
# admitted. This extracts columns; it does not validate SQL for execution.
_OPTION = re.compile(
    r'(?:DEFAULT\s+(?:CHARSET|CHARACTER\s+SET|COLLATE)|ENGINE|AUTO_INCREMENT_MODE|'
    r'AUTO_INCREMENT|CHARSET|COLLATE|ROW_FORMAT|COMPRESSION|REPLICA_NUM|BLOCK_SIZE|'
    r'USE_BLOOM_FILTER|TABLET_SIZE|PCTFREE|COMMENT)\s*=\s*', re.IGNORECASE)
_BARE_OPTION_VALUE = re.compile(r'[A-Za-z0-9_]+(?=\s|;|$)')


def _validate_tail(tail: str) -> None:
    index = 0
    while index < len(tail):
        if tail[index].isspace():
            index += 1
            continue
        if tail[index] == ';' and not tail[index + 1:].strip():
            return
        option = _OPTION.match(tail, index)
        if option is None:
            raise AlisdWireError('DDL contains unsupported table options or trailing statements')
        index = option.end()
        if index < len(tail) and tail[index] in "'\"":
            quote = tail[index]
            index += 1
            while index < len(tail):
                if tail[index] == '\\':
                    index += 2
                    continue
                if tail[index] == quote:
                    index += 1
                    if index < len(tail) and tail[index] == quote:
                        index += 1
                        continue
                    break
                index += 1
            else:
                raise AlisdWireError('DDL table option quoting is incomplete')
            if index < len(tail) and not tail[index].isspace() and tail[index] != ';':
                raise AlisdWireError('DDL table option separator is invalid')
        else:
            value = _BARE_OPTION_VALUE.match(tail, index)
            if value is None:
                raise AlisdWireError('DDL table option value is unsupported')
            index = value.end()


def _definitions(ddl: str, start: int) -> list[str]:
    """Split only top-level table definitions, respecting quoted SQL literals."""
    depth, quote, begin, index = 1, None, start, start
    definitions = []
    while index < len(ddl):
        char = ddl[index]
        if quote is not None:
            if char == '\\' and quote != '`':
                index += 2
                continue
            if char == quote:
                if index + 1 < len(ddl) and ddl[index + 1] == quote:
                    index += 2
                    continue
                quote = None
        elif char in "'\"`":
            quote = char
        elif ddl.startswith(('/*', '--'), index) or char == '#':
            raise AlisdWireError('SQL comments outside literals are not admitted by the metadata parser')
        elif char == '(':
            depth += 1
            if depth > 32: raise AlisdWireError('DDL nesting exceeds limit')
        elif char == ')':
            depth -= 1
            if depth == 0:
                definitions.append(ddl[begin:index].strip())
                _validate_tail(ddl[index + 1:])
                return definitions
        elif char == ',' and depth == 1:
            definitions.append(ddl[begin:index].strip())
            begin = index + 1
            if len(definitions) > 512: raise AlisdWireError('DDL definition count exceeds limit')
        index += 1
    raise AlisdWireError('DDL quoting or table body is incomplete')


def describe_table(payload: dict, table: str) -> dict:
    table = metadata_identifier(table)
    rows = _rows(payload)
    if (len(rows) != 1 or not isinstance(rows[0], dict) or set(rows[0]) != {'Table', 'Create Table'}
            or rows[0]['Table'] != table):
        raise AlisdWireError('SHOW CREATE TABLE response does not identify the requested table')
    ddl = rows[0]['Create Table']
    if not isinstance(ddl, str) or not 1 <= len(ddl.encode('utf-8')) <= 1_048_576:
        raise AlisdWireError('DDL is missing or exceeds the metadata budget')
    header = _HEADER.match(ddl)
    if header is None or header.group(1) != table:
        raise AlisdWireError('DDL header differs from the requested table')
    columns = []
    for definition in _definitions(ddl, header.end()):
        column = _COLUMN.match(definition)
        if column is not None:
            if column.group(2).lower() not in _TYPES:
                raise AlisdWireError('DDL column type is outside the supported metadata grammar')
            columns.append(metadata_identifier(column.group(1)))
        elif _CONSTRAINT.match(definition) is None:
            raise AlisdWireError('DDL contains an unsupported table definition')
    if not columns or len({column.lower() for column in columns}) != len(columns):
        raise AlisdWireError('DDL columns are missing or ambiguous')
    return {'success': True, 'columns': columns,
            'schema_digest': hashlib.sha256(ddl.encode('utf-8')).hexdigest()}
