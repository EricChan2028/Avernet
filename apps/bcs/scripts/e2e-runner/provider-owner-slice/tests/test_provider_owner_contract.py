"""Offline guards for the Provider owner-isolation owned-local slice candidate.

Staged under provider-owner-slice/tests/ and landed verbatim into tests/ by the
orchestrator landing recipe; the runner root is located dynamically so the file
needs no edit when it moves.
"""
import ast
import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


def _runner_root() -> Path:
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / 'provider-owner-migration-contract.json').is_file() and (parent / 'bcs_e2e').is_dir():
            return parent
    raise AssertionError('provider-owner runner root not found')


ROOT = _runner_root()
sys.path.insert(0, str(ROOT))
from bcs_e2e.run_state import RunStateError
from bcs_e2e.provider_owner_contract import (ANCHORS, DOCUMENTED_GAPS, MARKER_KEYS,
                                              MARKER_PATTERNS, MARKER_PREFIX, MARKER_SUFFIX,
                                              OWNER_ID, REQUIREMENTS, SOURCE_REVISION,
                                              SHELL_ANCHORS, SHELL_SOURCE, STORY_CHECKS,
                                              STORY_SOURCE, provider_owner_adaptability,
                                              provider_owner_source_binding)
from bcs_e2e.inventory import (KNOWN_UNMAPPED_LEGACY_IDS, REVIEWED_RELEASE_PARENT_COUNT,
                               current_manifest, reviewed_release_inventory)

GOOD_MARKER_LINE = 'Provider scope story: 45 live API assertions passed'


def _count_frozen_story_checks(path: Path) -> int:
    """Re-derive the frozen marker accounting from the upstream story text.

    request() and ws_connect() are the only Story.checks increment sites;
    bot() and group() perform exactly one request each; literal-tuple For
    loops in run() multiply their body's execution count.
    """
    tree = ast.parse(path.read_text(encoding='utf-8'))
    story = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == 'Story')
    run = next(node for node in story.body
               if isinstance(node, ast.FunctionDef) and node.name == 'run')
    counted = {'request', 'ws_connect', 'bot', 'group'}
    total = 0

    def walk(statements, multiplier):
        nonlocal total
        for statement in statements:
            if (isinstance(statement, ast.For)
                    and isinstance(statement.iter, (ast.Tuple, ast.List))):
                walk(statement.body, multiplier * len(statement.iter.elts))
                continue
            for node in ast.walk(statement):
                if (isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Attribute)
                        and isinstance(node.func.value, ast.Name)
                        and node.func.value.id == 'self'
                        and node.func.attr in counted):
                    total += multiplier

    walk(run.body, 1)
    return total


class MarkerTests(unittest.TestCase):
    def test_marker_pattern_parses_the_checks_count(self):
        match = MARKER_PATTERNS[0].match(GOOD_MARKER_LINE)
        self.assertIsNotNone(match)
        self.assertEqual(match.group(1), '45')
        self.assertIsNone(MARKER_PATTERNS[0].match(GOOD_MARKER_LINE[:-len(' passed')]))
        self.assertIsNone(MARKER_PATTERNS[0].match('Provider scope story: 45 checks passed'))

    def test_marker_literals_remain_in_the_upstream_story(self):
        text = STORY_SOURCE.read_text(encoding='utf-8')
        self.assertIn(MARKER_PREFIX, text)
        self.assertIn(MARKER_SUFFIX, text)

    def test_shell_anchors_remain_in_the_frozen_shell_entry(self):
        shell = SHELL_SOURCE.read_text(encoding='utf-8')
        for anchor in SHELL_ANCHORS:
            self.assertIn(anchor, shell, anchor)


class StaticAccountingTests(unittest.TestCase):
    def test_frozen_story_checks_derive_from_the_upstream_text(self):
        self.assertEqual(_count_frozen_story_checks(STORY_SOURCE), STORY_CHECKS)
        self.assertEqual(STORY_CHECKS, 45)

    def test_marker_accounting_derivation_table(self):
        # 33 direct request executions (28 straight-line + 3 visibility-loop
        # + 2 member-removal-loop), 4 provider Bot mints, 5 Group creations
        # and 3 Workbench WebSocket connect projections.
        self.assertEqual(33 + 4 + 5 + 3, STORY_CHECKS)

    def test_explicit_asserts_stay_outside_the_marker_count(self):
        text = STORY_SOURCE.read_text(encoding='utf-8')
        self.assertIn('assert metadata["collaboration_scope"] == "standard" and metadata["name"] == "Scope story"', text)
        self.assertIn('assert self.request("GET", f"/providers/{pid}", token=admin)["collaboration_scope"] == "same_owner"', text)


class ContractTests(unittest.TestCase):
    def test_binding_freezes_the_complete_upstream_map(self):
        value = provider_owner_source_binding()
        self.assertEqual(set(value), {'schema_version', 'source_revision', 'source_file_revisions',
                                      'parent', 'suite', 'case_id', 'environment', 'sources',
                                      'story_checks', 'owned_runtime_binary', 'requirements',
                                      'story_markers', 'documented_gaps', 'adaptability',
                                      'remote_validation', 'grants_release_credit',
                                      'release_parent_denominator', 'independent_slice',
                                      'evidence_granularity'})
        self.assertEqual(value['schema_version'], 1)
        self.assertEqual(value['parent'], 'story_provider_owner_isolation')
        self.assertEqual(value['suite'], 'owned-local-provider-owner-v1')
        self.assertEqual(value['case_id'], 'LOCAL-PROVIDER-OWNER-01')
        self.assertEqual(len(value['requirements']), 24)
        self.assertEqual([row['id'] for row in value['requirements']], list(REQUIREMENTS))
        self.assertEqual(value['story_checks'], STORY_CHECKS)
        self.assertEqual(value['story_markers'],
                         ['Provider scope story: {checks} live API assertions passed'])
        self.assertEqual(set(value['sources']), {'provider_scope_story.py', 'provider_scope_story.sh'})
        for digest in value['sources'].values():
            self.assertEqual(len(digest), 64)
        for digest in value['source_file_revisions'].values():
            self.assertEqual(len(digest), 40)
        self.assertEqual(value['source_revision'], SOURCE_REVISION)
        runtime = value['owned_runtime_binary']
        self.assertEqual(runtime['package'], 'bcs')
        self.assertFalse(runtime['auto_build'])
        self.assertEqual(runtime['required_arguments'], ['--binary', '--binary-sha256'])
        self.assertEqual(runtime['opt_in_env'], ['BCS_E2E_TEST_BINARY', 'BCS_E2E_TEST_BINARY_SHA256'])

    def test_unmapped_backlog_slice_stays_out_of_the_release_denominator(self):
        value = provider_owner_source_binding()
        self.assertTrue(value['release_parent_denominator'] is False)
        self.assertTrue(value['independent_slice'] is True)
        self.assertTrue(value['grants_release_credit'] is False)
        self.assertEqual(value['remote_validation'], 'NOT_RUN')
        self.assertEqual(value['evidence_granularity'], 'OWNED_CHILD_RECEIPT')
        self.assertNotIn('mock_child', value)
        self.assertNotIn('human_id', value)

    def test_contract_tamper_matrix_is_rejected(self):
        original = provider_owner_source_binding()
        mutations = {
            'drop-requirement': lambda value: value['requirements'].pop(),
            'markers': lambda value: value['story_markers'].__setitem__(0, 'Provider scope story: {checks}'),
            'runtime-binary-auto-build': lambda value: value['owned_runtime_binary'].__setitem__('auto_build', True),
            'runtime-binary-arguments': lambda value: value['owned_runtime_binary'].__setitem__('required_arguments', ['--binary']),
            'revision': lambda value: value.__setitem__('source_revision', 'z' * 40),
            'file-revision': lambda value: value['source_file_revisions'].__setitem__(
                'provider_scope_story.sh', 'f' * 40),
            'file-revision-drop': lambda value: value['source_file_revisions'].pop('provider_scope_story.py'),
            'denominator': lambda value: value.__setitem__('release_parent_denominator', True),
            'independent': lambda value: value.__setitem__('independent_slice', False),
            'credit': lambda value: value.__setitem__('grants_release_credit', True),
            'granularity': lambda value: value.__setitem__('evidence_granularity', 'PER_OBLIGATION_RECEIPT'),
            'environment': lambda value: value['environment'].__setitem__('preprod', 'SUPPORTED'),
            'owner': lambda value: value.__setitem__('owner_id', '002'),
            'sources-drop': lambda value: value['sources'].pop('provider_scope_story.py'),
            'sources-extra': lambda value: value['sources'].__setitem__('rogue.py', '0' * 64),
            'story-checks': lambda value: value.__setitem__('story_checks', 44),
            'gap-drop': lambda value: value['documented_gaps'].pop('OWNED_LOCAL_PROVIDER_BOT_OWNER_EDGE_DRIFT'),
            'adaptability-envs': lambda value: value['adaptability'].__setitem__(
                'environments', ['owned-local-full-story', 'preprod-native-slice']),
            'requirement-markers': lambda value: value['requirements'][0].__setitem__('markers', ['wrong-key']),
            'requirement-lines': lambda value: value['requirements'][0].__setitem__('story_lines', [102, 999]),
            'requirement-extra-key': lambda value: value['requirements'][0].__setitem__('method', 'POST'),
        }
        for mutation, apply in mutations.items():
            value = copy.deepcopy(original)
            apply(value)
            with self.subTest(mutation=mutation), \
                    patch('bcs_e2e.provider_owner_contract.read', return_value=value), \
                    self.assertRaises(RunStateError):
                provider_owner_source_binding()

    def test_upstream_drift_is_rejected_before_any_executor_exists(self):
        with tempfile.TemporaryDirectory() as temporary:
            drifted = Path(temporary) / 'drifted.bin'
            drifted.write_bytes(b'drifted upstream source\n')
            for name, attribute in (('story', 'STORY_SOURCE'), ('shell-entry', 'SHELL_SOURCE')):
                with self.subTest(source=name), \
                        patch('bcs_e2e.provider_owner_contract.' + attribute, drifted), \
                        self.assertRaises(RunStateError):
                    provider_owner_source_binding()


class BacklogFenceTests(unittest.TestCase):
    def test_parent_is_a_named_known_unmapped_backlog_story(self):
        self.assertIn('story_provider_owner_isolation', KNOWN_UNMAPPED_LEGACY_IDS)
        self.assertNotIn('story_provider_owner_isolation',
                         {row['id'] for row in reviewed_release_inventory()})
        self.assertEqual(REVIEWED_RELEASE_PARENT_COUNT, 31)
        row = next(item for item in current_manifest()['legacy_inventory']
                   if item['id'] == 'story_provider_owner_isolation')
        self.assertEqual(row['source'], 'provider_scope_story.sh')
        self.assertFalse(row['conditional'])
        self.assertEqual(row['migration_status'], 'NOT_MIGRATED')
        self.assertEqual(len(current_manifest()['legacy_inventory']), 41)


class ObligationTests(unittest.TestCase):
    def setUp(self):
        self.text = STORY_SOURCE.read_text(encoding='utf-8')
        self.rows = provider_owner_source_binding()['requirements']

    def test_anchors_and_disjoint_line_ranges_cover_the_frozen_story(self):
        self.assertEqual([row['id'] for row in self.rows], list(REQUIREMENTS))
        previous_end = 0
        covered = 0
        for row in self.rows:
            start, end = row['story_lines']
            self.assertGreaterEqual(start, 1)
            self.assertGreaterEqual(start, previous_end + 1)
            self.assertLessEqual(end, len(self.text.splitlines()))
            covered += end - start + 1
            previous_end = end
            for literal in ANCHORS[row['id']]:
                self.assertIn(literal, self.text, row['id'])
        self.assertGreaterEqual(covered, 54)
        self.assertEqual(previous_end, 166)

    def test_owner_boundary_matrix_covers_both_principals(self):
        text = self.text
        # The owner principal drives every default call through the loopback
        # mock header (owner or self.owner); the foreign principal only enters
        # through explicit owner= overrides or the visibility loop's per-Bot
        # conditional.
        for literal in ('"X-Mock-User-Id": owner or self.owner',
                        'owner=self.foreign if bot == outsider else self.owner'):
            self.assertIn(literal, text)
        self.assertGreater(text.count('owner=self.foreign'), 5)
        self.assertIn('"human_" + self.owner', text)


class PreprodFaceTests(unittest.TestCase):
    def test_preprod_profiles_declare_no_provider_admin_face(self):
        profile = json.loads((ROOT / 'profiles' / 'preprod.example.json').read_text(encoding='utf-8'))
        routes = profile.get('route_bindings', {})
        self.assertTrue(routes)
        self.assertTrue(all(name.startswith(('group.', 'session.')) for name in routes),
                        sorted(routes))
        serialized = json.dumps(profile)
        self.assertNotIn('/providers', serialized)
        self.assertNotIn('provider_admin_token', serialized)
        binding = provider_owner_source_binding()
        self.assertEqual(binding['environment'], {'local': 'LOCAL_EXECUTOR_ONLY',
                                                  'preprod': 'REMOTE_UNSUPPORTED'})
        gaps = binding['documented_gaps']
        self.assertIn('Provider registration/admin surface', gaps['PREPROD_PROVIDER_ADMIN_FACE_ABSENT'])


class GapTests(unittest.TestCase):
    def test_documented_gaps_freeze_the_live_drift_and_the_preprod_absence(self):
        value = provider_owner_source_binding()
        self.assertEqual(set(value['documented_gaps']), set(DOCUMENTED_GAPS))
        drift = value['documented_gaps']['OWNED_LOCAL_PROVIDER_BOT_OWNER_EDGE_DRIFT']
        self.assertIn('owner or manager role required', drift)
        self.assertIn('f181ba44', drift)
        self.assertIn('c6f001209', drift)
        ws = value['documented_gaps']['WORKBENCH_WS_MOCK_ADMISSION']
        self.assertIn('X-Mock-User-Id', ws)
        self.assertEqual(len(value['documented_gaps']), 3)


class AdaptabilityTests(unittest.TestCase):
    def test_adaptability_row_declares_owned_local_only_without_release_credit(self):
        row = provider_owner_adaptability()
        self.assertEqual(row['case_id'], 'LOCAL-PROVIDER-OWNER-01')
        self.assertEqual(row['legacy_id'], 'story_provider_owner_isolation')
        self.assertEqual(row['requirement_ids'], list(REQUIREMENTS))
        self.assertEqual(row['selected_obligations'], [name.split('.', 1)[1] for name in REQUIREMENTS])
        self.assertEqual(row['public_paths'], [])
        self.assertEqual(row['environments'], ['owned-local-full-story'])
        self.assertEqual(row['local_executor_status'], 'LOCAL_EXECUTOR_ONLY')
        self.assertEqual(row['remote_validation'], 'NOT_RUN')
        self.assertFalse(row['grants_release_credit'])
        self.assertIn('OWNED_LOCAL_PROVIDER_BOT_OWNER_EDGE_DRIFT', row['blocker'])
        self.assertIn('PREPROD_PROVIDER_ADMIN_FACE_ABSENT', row['blocker'])
        self.assertEqual(row, provider_owner_source_binding()['adaptability'])


if __name__ == '__main__':
    unittest.main()
