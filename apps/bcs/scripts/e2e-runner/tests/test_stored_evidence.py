"""Storage-to-semantic parity; no live remote business queries are performed."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bcs_e2e.evidence import ASSERTIONS, AlisdDbEvidenceProvider, EvidenceError, LocalSemanticEvidenceProvider
from bcs_e2e.file_assertions import FileExpectation


class StoredEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.row = {'file_id': 'f1', 'env': 'pre', 'session_id': 's1', 'status': 'Ready',
                    'size': 12, 'sha256': None, 'storage_backend': 'baas'}
        self.expected = FileExpectation('f1', 's1', 'sample.txt', 'text/plain', 12,
                                        'a' * 64, 'human_owner', 'baas')
        self.calls = []

    def provider(self, row, *, confirmed=True):
        def describe(database, table):
            self.calls.append('describe')
            fields = next(fields for name, fields, _ in ASSERTIONS.values() if name == table)
            return {'success': True, 'columns': list(fields)}
        def query(plan):
            self.calls.append(plan.public())
            return {'success': True, 'data': [row]}
        return AlisdDbEvidenceProvider(database='testdb', environment='pre',
            environment_binding_confirmed=confirmed, describe=describe, query=query)

    def test_four_persisted_statuses_map_without_modifying_input(self):
        for raw, expected in (('Pending', 'pending'), ('Ready', 'ready'),
                              ('Deleting', 'deleting'), ('Failed', 'failed')):
            row = {**self.row, 'status': raw}
            result = self.provider(row).observe('file.persisted', 'f1')['rows'][0]
            self.assertEqual(result, {**row, 'status': expected})
            self.assertEqual(row['status'], raw)
            self.assertIsNot(result, row)
            self.assertEqual(self.expected.matches_persisted(result), raw == 'Ready')
        queries = [call for call in self.calls if isinstance(call, dict)]
        self.assertEqual(len(queries), 4)
        self.assertTrue(all(call['filters'] == {'env': 'pre', 'file_id': 'f1'} and
                            call['limit'] == 2 for call in queries))

    def test_unknown_non_string_and_already_semantic_raw_statuses_fail_closed(self):
        for status in ('ready', 'READY', '', 'Unknown', None, 1, [], {}):
            with self.subTest(status=status), self.assertRaisesRegex(EvidenceError, 'persisted file status'):
                self.provider({**self.row, 'status': status}).observe('file.persisted', 'f1')

    def test_hash_size_parent_and_backend_are_observed_not_repaired(self):
        for patch, matches in (({'sha256': None}, True), ({'sha256': 'a' * 64}, True),
                               ({'sha256': ''}, False), ({'sha256': 'NULL'}, False),
                               ({'sha256': 'b' * 64}, False), ({'size': '12'}, False),
                               ({'size': True}, False), ({'size': 13}, False),
                               ({'session_id': 'foreign'}, False),
                               ({'storage_backend': 'local'}, False)):
            with self.subTest(patch=patch):
                row = {**self.row, **patch}
                result = self.provider(row).observe('file.persisted', 'f1')['rows'][0]
                self.assertEqual(result, {**row, 'status': 'ready'})
                self.assertEqual(self.expected.matches_persisted(result), matches)

    def test_exact_environment_resource_and_required_projection_precede_conversion(self):
        for row in ({**self.row, 'env': 'prod'}, {**self.row, 'file_id': 'foreign'},
                    {key: value for key, value in self.row.items() if key != 'sha256'}):
            with self.subTest(row=row), self.assertRaises(EvidenceError):
                self.provider(row).observe('file.persisted', 'f1')
        self.calls.clear()
        with self.assertRaises(EvidenceError):
            self.provider(self.row, confirmed=False).observe('file.persisted', 'f1')
        self.assertEqual(self.calls, [])

    def test_group_and_session_storage_values_are_not_rewritten(self):
        for assertion, resource, row in (
                ('group.persisted', 'g1', {'group_id': 'g1', 'env': 'pre', 'driver_bot': 'actual-driver',
                                         'originator': 'actual-owner', 'status': 'active'}),
                ('session.persisted', 's1', {'session_id': 's1', 'env': 'pre', 'group_id': 'actual-parent',
                                           'status': 'running', 'session_title': 'actual-title'})):
            result = self.provider(row).observe(assertion, resource)['rows'][0]
            self.assertEqual(result, row)
            self.assertIsNot(result, row)

    def test_semantic_provider_is_not_treated_as_raw_storage(self):
        row = {**self.row, 'status': 'ready'}
        provider = LocalSemanticEvidenceProvider(environment='pre',
            query=lambda _plan: {'success': True, 'data': [row]})
        result = provider.observe('file.persisted', 'f1')['rows'][0]
        self.assertEqual(result, row)
        self.assertTrue(self.expected.matches_persisted(result))
