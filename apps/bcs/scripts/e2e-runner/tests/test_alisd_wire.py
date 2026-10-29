"""Synthetic metadata fixtures matching observed tool response structure."""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bcs_e2e.alisd_wire import AlisdWireError, database_list, table_list, describe_table


def described(ddl, table='bcs_groups'):
    return {'success': True, 'traceId': 'tool-trace-not-business-evidence',
            'data': [{'Table': table, 'Create Table': ddl}]}


class AlisdWireTests(unittest.TestCase):
    def test_real_wire_list_shapes_are_normalized_without_trace_or_message(self):
        self.assertEqual(database_list({'success': True, 'data': ['testdb'], 'traceId': 'tool'}),
                         {'success': True, 'data': ['testdb']})
        self.assertEqual(table_list({'success': True, 'data': [{'Tables_in_testdb': 'bcs_groups'}]}, 'testdb'),
                         {'success': True, 'data': ['bcs_groups']})
        self.assertEqual(table_list({'success': True, 'data': []}, 'testdb'), {'success': True, 'data': []})

    def test_columns_respect_literals_parentheses_enum_and_index_definitions(self):
        ddl = '''CREATE TABLE `bcs_groups` (
  `group_id` varchar(128) NOT NULL,
  `env` varchar(16) DEFAULT 'local',
  `status` enum('active','closed') COMMENT 'comma, and (`not_a_column`), -- text',
  `originator` varchar(128) DEFAULT NULL,
  `context` text DEFAULT 'escaped \\' quote, remains a string',
  `cost` decimal(10,2) DEFAULT '0.00',
  PRIMARY KEY (`group_id`,`env`),
  UNIQUE KEY `not_a_column` (`group_id`),
  CONSTRAINT `c` CHECK ((`cost` >= 0))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;'''
        result = describe_table(described(ddl), 'bcs_groups')
        self.assertEqual(result['columns'], ['group_id','env','status','originator','context','cost'])
        self.assertEqual(len(result['schema_digest']), 64)
        self.assertNotIn('Create Table', result)

    def test_failed_truncated_malformed_or_cross_database_metadata_is_rejected(self):
        for payload in ({'success': False, 'message': 'private-error'}, {'success': True, 'data': None},
                        {'success': True, 'data': [], 'truncated': True},
                        {'success': True, 'data': [], 'truncated': 1},
                        {'success': True, 'data': [], 'truncated': 'false'}):
            with self.subTest(payload=payload), self.assertRaises(AlisdWireError) as caught:
                database_list(payload)
            self.assertNotIn('private-error', str(caught.exception))
        for rows in ([{'Tables_in_otherdb': 'bcs_groups'}], ['bcs_groups'],
                     [{'Tables_in_testdb': 'bcs_groups', 'extra': 'ambiguous'}],
                     [{'Tables_in_testdb': 'bcs_groups'}] * 2):
            with self.assertRaises(AlisdWireError): table_list({'success': True, 'data': rows}, 'testdb')

    def test_description_must_match_requested_table_in_row_and_ddl(self):
        for payload in (described('CREATE TABLE `other` (`id` bigint)'),
                        described('CREATE TABLE `bcs_groups` (`id` bigint)', table='other'),
                        {'success': True, 'columns': ['id']},
                        {'success': True, 'data': []}):
            with self.assertRaises(AlisdWireError): describe_table(payload, 'bcs_groups')

    def test_unsupported_ambiguous_or_incomplete_ddl_is_never_guessed(self):
        for ddl in ('CREATE VIEW `bcs_groups` AS SELECT 1',
                    'CREATE TABLE `bcs_groups` (`id` bigint, `ID` bigint)',
                    'CREATE TABLE `bcs_groups` (id bigint)',
                    'CREATE TABLE `bcs_groups` (`id` mystery)',
                    'CREATE TABLE `bcs_groups` (`id` bigint,)',
                    "CREATE TABLE `bcs_groups` (`id` varchar(10) DEFAULT 'unterminated)",
                    'CREATE TABLE `bcs_groups` (`id` bigint /* hidden */)',
                    'CREATE TABLE `bcs_groups` (`id` bigint); CREATE TABLE x(id int);',
                    'CREATE TABLE `bcs_groups` (' + '(' * 40 + ')'*40 + ')'):
            with self.subTest(ddl=ddl), self.assertRaises(AlisdWireError):
                describe_table(described(ddl), 'bcs_groups')

    def test_table_options_are_bounded_and_quote_aware(self):
        head = 'CREATE TABLE `bcs_groups` (`id` bigint)'
        for tail in (" ENGINE=InnoDB COMMENT='first; second';",
                     " COMMENT='doubled '' quote; text'",
                     " AUTO_INCREMENT=1 AUTO_INCREMENT_MODE='ORDER' DEFAULT CHARSET=utf8mb4 "
                     "ROW_FORMAT=DYNAMIC COMPRESSION='zstd_1.0' REPLICA_NUM=3 BLOCK_SIZE=16384 "
                     "USE_BLOOM_FILTER=FALSE TABLET_SIZE=134217728 PCTFREE=0"):
            with self.subTest(tail=tail):
                self.assertEqual(describe_table(described(head + tail), 'bcs_groups')['columns'], ['id'])
        for tail in (" COMMENT='unterminated", ' ENGINE=InnoDB /* unfinished',
                     ' ENGINE=InnoDB -- comment', '; SELECT 1', ';;', ' ENGINE=',
                     " COMMENT='ok'COMMENT='missing separator'", ' UNKNOWN=value'):
            with self.subTest(tail=tail), self.assertRaises(AlisdWireError):
                describe_table(described(head + tail), 'bcs_groups')

    def test_budgets_and_duplicate_database_names_are_checked(self):
        with self.assertRaises(AlisdWireError): database_list({'success': True, 'data': ['testdb','testdb']})
        with self.assertRaises(AlisdWireError): database_list({'success': True, 'data': ['testdb'] * 10001})
        with self.assertRaises(AlisdWireError): describe_table(described('x' * 1_048_577), 'bcs_groups')
