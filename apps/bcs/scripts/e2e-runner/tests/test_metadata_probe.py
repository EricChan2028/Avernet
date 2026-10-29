"""Metadata-only preparation contract; no physical binding or business queries."""
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bcs_e2e.alisd_wire import AlisdWireError
from bcs_e2e.alisddbquery import AlisdDbQueryError, McporterAlisdDbQueryClient
from bcs_e2e.cli import main
from bcs_e2e.evidence import ASSERTIONS
from bcs_e2e.metadata_probe import EXECUTION_METADATA_FIELDS, discover_metadata, probe_db_metadata
from bcs_e2e.profile import ConfigError


class MetadataFixture:
    def __init__(self):
        self.calls = []
        self.databases = ['testdb', 'private_database']
        self.tables = [value[0] for value in ASSERTIONS.values()] + ['private_table']
        self.columns = {table: list(fields) for table, fields, _ in ASSERTIONS.values()}

    def db_list(self):
        self.calls.append(('db_list',))
        return {'success': True, 'data': self.databases}

    def table_list(self, database):
        self.calls.append(('table_list', database))
        return {'success': True, 'data': self.tables}

    def table_describe(self, database, table):
        self.calls.append(('table_describe', database, table))
        return {'success': True, 'columns': self.columns[table] + ['private_column'],
                'schema_digest': 'a' * 64, 'ddl': 'private DDL comment', 'traceId': 'private-trace'}

    def db_query(self, *args):
        raise AssertionError('business queries are forbidden')


class MetadataProbeTests(unittest.TestCase):
    def test_success_is_exactly_five_metadata_calls_without_admitting_environment(self):
        client = MetadataFixture()
        with patch('bcs_e2e.alisddbquery.AlisdDbQueryObserver') as observer:
            result = discover_metadata(client, server='test-observer', database='testdb')
        self.assertEqual(result['status'], 'METADATA_COMPATIBLE')
        self.assertFalse(result['ready_to_execute'])
        self.assertEqual(result['physical_environment_binding'], 'UNVERIFIED')
        self.assertEqual(result['business_queries'], 0)
        self.assertEqual(result['metadata_calls'], 5)
        self.assertEqual(client.calls[:2], [('db_list',), ('table_list', 'testdb')])
        self.assertEqual(client.calls[2:], [('table_describe', 'testdb', value[0]) for value in ASSERTIONS.values()])
        self.assertEqual(len(result['tables']), 3)
        self.assertNotIn('private', json.dumps(result))
        observer.assert_not_called()

    def test_missing_database_or_table_stops_without_guessing_fallbacks(self):
        for missing, count in (('database', 1), ('table', 2)):
            client = MetadataFixture()
            if missing == 'database': client.databases = ['private_database']
            else: client.tables = ['private_table']
            result = discover_metadata(client, server='test-observer', database='testdb')
            self.assertEqual(result['status'], 'BLOCKED')
            self.assertEqual(len(client.calls), count)
            self.assertEqual(result['metadata_calls'], count)
            self.assertEqual(result['tables'], [])
            self.assertNotIn('private', json.dumps(result))

    def test_missing_projection_stays_blocked_and_never_exports_extra_fields(self):
        client = MetadataFixture(); client.columns['bcs_groups'].remove('env')
        result = discover_metadata(client, server='test-observer', database='testdb')
        self.assertEqual(result['status'], 'BLOCKED')
        self.assertEqual(result['metadata_calls'], 3)
        self.assertEqual(result['tables'][0]['missing_required_fields'], ['env'])
        self.assertEqual(result['failed_table'], 'bcs_groups')
        self.assertNotIn('private', json.dumps(result))

    def test_tool_errors_and_malformed_normalized_results_are_not_success(self):
        for method, response in (('db_list', {'success': True, 'data': ['testdb'], 'truncated': 'false'}),
                                 ('table_list', {'success': False, 'message': 'private-error'}),
                                 ('table_describe', {'success': True, 'columns': ['env'], 'schema_digest': 'private'}),
                                 ('table_describe', {'success': True, 'columns': ['env','ENV'], 'schema_digest': 'a'*64})):
            client = MetadataFixture()
            with patch.object(client, method, return_value=response):
                result = discover_metadata(client, server='test-observer', database='testdb')
            self.assertEqual(result['status'], 'BLOCKED')
            self.assertFalse(result['ready_to_execute'])
            self.assertNotIn('private', json.dumps(result))
        client = MetadataFixture()
        error = AlisdDbQueryError('private-server-error'); error.child_shutdown_status = 'UNCONFIRMED'
        with patch.object(client, 'table_describe', side_effect=error):
            result = discover_metadata(client, server='test-observer', database='testdb')
        self.assertEqual(result['child_shutdown_status'], 'UNCONFIRMED')
        self.assertNotIn('private', json.dumps(result))

    def test_required_column_only_in_ddl_comment_does_not_satisfy_projection(self):
        import subprocess
        client = McporterAlisdDbQueryClient(server='test-observer', runner=Mock(return_value=subprocess.CompletedProcess([], 0,
            json.dumps({'success': True, 'data': [{'Table': 'bcs_groups', 'Create Table':
                "CREATE TABLE `bcs_groups` (`group_id` varchar(20) COMMENT 'env driver_bot originator status')"}]}), '')))
        fixture = MetadataFixture()
        with patch.object(fixture, 'table_describe', side_effect=client.table_describe):
            result = discover_metadata(fixture, server='test-observer', database='testdb')
        self.assertEqual(result['status'], 'BLOCKED')
        self.assertIn('env', result['tables'][0]['missing_required_fields'])

    def test_permission_and_all_arguments_are_checked_before_client_creation(self):
        for override in ({'allow_read': False}, {'server': 'invalid/server'}, {'server': '--help'},
                         {'server': 'test.db_query'}, {'server': 'endpoint:reference'}, {'database': 'db-with-dash'},
                         {'timeout_seconds': 0}, {'executable': Path('relative')},
                         {'executable': Path('/does-not-exist/metadata-tool')}):
            args = dict(server='test-observer', database='testdb', executable=Path(sys.executable), allow_read=True)
            with self.subTest(override=override), patch('bcs_e2e.metadata_probe.McporterAlisdDbQueryClient') as client:
                with self.assertRaises((ConfigError, AlisdWireError)):
                    probe_db_metadata(**{**args, **override})
                client.assert_not_called()

    def test_command_requires_no_profile_and_preserves_explicit_read_permission(self):
        argv = ['probe', 'db-metadata', '--server', 'test-observer', '--database', 'testdb',
                '--mcporter-binary', sys.executable]
        with patch('bcs_e2e.metadata_probe.McporterAlisdDbQueryClient') as client, contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main(argv), 2)
            client.assert_not_called()
        self.assertEqual(json.loads(output.getvalue())['status'], 'BLOCKED')
        with patch('bcs_e2e.metadata_probe.McporterAlisdDbQueryClient', return_value=MetadataFixture()), \
             patch('bcs_e2e.cli.load') as load, patch('bcs_e2e.cli.prepare_run') as prepare, \
             contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main([*argv, '--allow-read']), 0)
        load.assert_not_called(); prepare.assert_not_called()
        self.assertFalse(json.loads(output.getvalue())['ready_to_execute'])

    def test_chained_cancellation_propagates_instead_of_becoming_metadata_failure(self):
        def interrupted():
            try: raise KeyboardInterrupt()
            except KeyboardInterrupt: raise OSError('private-secondary-error')
        fixture = MetadataFixture()
        with patch.object(fixture, 'db_list', side_effect=interrupted), self.assertRaises(KeyboardInterrupt):
            discover_metadata(fixture, server='test-observer', database='testdb')

    def test_command_uses_real_bounded_subprocess_and_only_metadata_tool_arguments(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); executable = root / 'fake-mcporter'; calls = root / 'calls.jsonl'
            schemas = {table: list(fields) for table, fields, _ in ASSERTIONS.values()}
            script = f'''#!{sys.executable}
import json, sys
schemas = {schemas!r}
tool = sys.argv[2].split('.')[-1]
args = json.loads(sys.argv[sys.argv.index('--args')+1]) if '--args' in sys.argv else {{}}
with open({str(calls)!r}, 'a') as f: f.write(json.dumps([tool,args])+'\\n')
if tool == 'db_list': data = ['testdb']
elif tool == 'table_list' and args == {{'db':'testdb'}}: data = [{{'Tables_in_testdb': t}} for t in schemas]
elif tool == 'table_describe' and args['db'] == 'testdb':
    table = args['table']
    data = [{{'Table':table, 'Create Table':'CREATE TABLE `'+table+'` ('+', '.join('`'+f+'` varchar(128)' for f in schemas[table])+')'}}]
else: raise SystemExit(9)
print(json.dumps({{'success': True, 'data': data}}))
'''
            executable.write_text(script); executable.chmod(0o700)
            result = probe_db_metadata(server='test-observer', database='testdb', executable=executable, allow_read=True)
            self.assertEqual(result['status'], 'METADATA_COMPATIBLE')
            records = [json.loads(line) for line in calls.read_text().splitlines()]
            self.assertEqual([row[0] for row in records], ['db_list', 'table_list'] + ['table_describe']*3)
            self.assertEqual(set(root.iterdir()), {executable, calls})


class ExecutionMetadataProbeTests(unittest.TestCase):
    def fixture(self):
        client = MetadataFixture()
        client.columns = {table: list(fields) for table, fields in EXECUTION_METADATA_FIELDS.items()}
        client.tables = list(client.columns)
        return client

    def test_execution_scope_uses_eight_metadata_calls_and_grants_no_authority(self):
        client = self.fixture()
        result = discover_metadata(client, server='test-observer', database='testdb', scope='execution')
        self.assertEqual(result['status'], 'METADATA_COMPATIBLE')
        self.assertEqual(result['metadata_calls'], 8)
        self.assertEqual(result['maximum_metadata_calls'], 8)
        self.assertEqual(result['execution_status'], 'UNCONFIRMED')
        self.assertEqual(result['business_queries'], 0)
        self.assertEqual(result['tables_without_environment_column'], ['baas_bot_run'])
        for field in ('ready_to_execute', 'grants_write_authority', 'grants_business_query_authority'):
            self.assertIs(result[field], False)
        self.assertEqual(client.calls[2:], [('table_describe', 'testdb', t) for t in EXECUTION_METADATA_FIELDS])
        self.assertNotIn('private', json.dumps(result))

    def test_missing_correlation_column_is_a_gap_not_a_terminal_execution(self):
        client = self.fixture()
        client.columns['bcs_message_deliveries'].remove('downstream_run_id')
        result = discover_metadata(client, server='test-observer', database='testdb', scope='execution')
        self.assertEqual(result['status'], 'BLOCKED')
        self.assertEqual(result['failed_table'], 'bcs_message_deliveries')
        self.assertEqual(result['tables'][-1]['missing_required_fields'], ['downstream_run_id'])
        self.assertEqual(result['execution_status'], 'UNCONFIRMED')
        self.assertEqual(result['metadata_calls'], 5)

    def test_missing_table_prevents_all_describes_and_business_queries(self):
        client = self.fixture(); client.tables.remove('baas_bot_run_queue')
        result = discover_metadata(client, server='test-observer', database='testdb', scope='execution')
        self.assertEqual(result['status'], 'BLOCKED')
        self.assertEqual(result['metadata_calls'], 2)
        self.assertEqual(result['tables'], [])

    def test_unknown_scope_rejected_before_any_tool_or_client_construction(self):
        client = self.fixture()
        with self.assertRaises(ConfigError):
            discover_metadata(client, server='test-observer', database='testdb', scope='all')
        self.assertEqual(client.calls, [])
        with patch('bcs_e2e.metadata_probe.McporterAlisdDbQueryClient') as constructor, self.assertRaises(ConfigError):
            probe_db_metadata(server='test-observer', database='testdb', executable=Path(sys.executable),
                              allow_read=True, scope='all')
        constructor.assert_not_called()

    def test_execution_cli_keeps_profile_credentials_and_prepare_out_of_metadata(self):
        argv = ['probe', 'db-metadata', '--scope', 'execution', '--server', 'test-observer',
                '--database', 'testdb', '--mcporter-binary', sys.executable]
        with patch('bcs_e2e.metadata_probe.McporterAlisdDbQueryClient') as constructor, \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(argv), 2)
        constructor.assert_not_called()
        with patch('bcs_e2e.metadata_probe.McporterAlisdDbQueryClient', return_value=self.fixture()), \
             patch('bcs_e2e.cli.load') as load, patch('bcs_e2e.cli.prepare_run') as prepare, \
             contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(main([*argv, '--allow-read']), 0)
        load.assert_not_called(); prepare.assert_not_called()
        report = json.loads(out.getvalue())
        self.assertEqual(report['metadata_scope'], 'execution')
        self.assertEqual(report['execution_status'], 'UNCONFIRMED')
        self.assertFalse(report['ready_to_execute'])

    def test_error_or_truncation_never_becomes_empty_execution_success(self):
        for response in ({'success': False, 'message': 'private'},
                         {'success': True, 'columns': [], 'schema_digest': 'a' * 64, 'truncated': True}):
            client = self.fixture()
            with patch.object(client, 'table_describe', return_value=response):
                result = discover_metadata(client, server='test-observer', database='testdb', scope='execution')
            self.assertEqual(result['status'], 'BLOCKED')
            self.assertEqual(result['execution_status'], 'UNCONFIRMED')
            self.assertNotIn('private', json.dumps(result))
