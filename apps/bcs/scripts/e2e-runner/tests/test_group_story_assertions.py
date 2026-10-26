"""Shared Story assertions must inspect responses, not repeat profile claims."""
import contextlib
import copy
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bcs_e2e.adapters import LocalMockAdapter
from bcs_e2e.cli import main
from bcs_e2e.pilot import run_case
from bcs_e2e.profile import load


class GroupStoryAssertionTests(unittest.TestCase):
    def prepare(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        guard = patch('bcs_e2e.pool.pool_root', return_value=self.root / 'pools')
        guard.start()
        self.addCleanup(guard.stop)
        self.profile = load(ROOT / 'profiles/local.mock.json')
        self.state = self.root / 'state'
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(['prepare', '--profile', str(ROOT / 'profiles/local.mock.json'),
                                   '--state-dir', str(self.state), '--run-id', 'group-assert']), 0)
        self.adapter = LocalMockAdapter(self.profile)

    def run_case(self, case):
        return run_case(self.profile, self.state, 'group-assert', case,
                         adapter_factory=lambda *args, **kwargs: self.adapter)

    def test_wrong_originator_driver_or_roles_do_not_pass_from_correct_profile(self):
        for field in ('originator_actor_id', 'driver_bot_uuid', 'participants', 'roles'):
            self.prepare()
            original = self.adapter.group_create
            def wrong(body):
                data, record = original(body)
                result = copy.deepcopy(data)
                if field == 'roles':
                    result['participants'][0]['role'] = 'consultant'
                    result['participants'][1]['role'] = 'driver'
                else:
                    result[field] = [] if field == 'participants' else 'foreign'
                return result, record
            with patch.object(self.adapter, 'group_create', side_effect=wrong):
                report = self.run_case('PRE-01')
            self.assertEqual(report['functional'], 'FAIL')
            self.assertEqual(report['cleanup'], 'PASS')

    def test_persisted_group_owner_driver_and_state_must_match(self):
        for field in ('originator', 'driver_bot', 'status'):
            self.prepare()
            observer = self.adapter.semantic_observer()
            original = observer.observe
            def wrong(assertion, resource):
                result = original(assertion, resource)
                result['rows'][0][field] = 'wrong'
                return result
            with patch.object(self.adapter, 'semantic_observer', return_value=observer), \
                 patch.object(observer, 'observe', side_effect=wrong):
                report = self.run_case('PRE-01')
            self.assertEqual(report['status'], 'BLOCKED')
            self.assertEqual(report['functional'], 'PASS')
            self.assertEqual(report['cleanup'], 'PASS')

    def test_session_create_explicitly_uses_current_human_before_any_denial(self):
        for case in ('PRE-02', 'PRE-03'):
            self.prepare()
            with patch.object(self.adapter, 'session_create', wraps=self.adapter.session_create) as create:
                report = self.run_case(case)
            self.assertEqual(report['status'], 'PASS')
            self.assertEqual(create.call_count, 1)
            self.assertEqual(create.call_args.args[1]['acting_bot_id'], 'human_' + self.profile['human']['user_id'])

    def test_stale_session_read_after_patch_does_not_pass_from_patch_and_db(self):
        self.prepare()
        original_read, original_update = self.adapter.session_read, self.adapter.session_update
        updated = False
        def update(*args):
            nonlocal updated
            result = original_update(*args)
            updated = True
            return result
        def stale(session_id):
            data, record = original_read(session_id)
            return ({**data, 'title': 'old title'} if updated else data), record
        with patch.object(self.adapter, 'session_update', side_effect=update),              patch.object(self.adapter, 'session_read', side_effect=stale):
            report = self.run_case('PRE-02')
        self.assertEqual(report['functional'], 'FAIL')
        self.assertEqual(report['cleanup'], 'PASS')
        assertion = next(a for a in report['assertions'] if a['id'] == 'PRE-02.title_persisted')
        self.assertEqual(assertion['status'], 'FAIL')

    def test_session_database_title_and_status_mismatch_blocks_qualification(self):
        for field in ('session_title', 'status'):
            self.prepare()
            observer = self.adapter.semantic_observer()
            original = observer.observe
            def wrong(assertion, resource):
                result = original(assertion, resource)
                if assertion == 'session.persisted':
                    result['rows'][0][field] = 'wrong'
                return result
            with patch.object(self.adapter, 'semantic_observer', return_value=observer),                  patch.object(observer, 'observe', side_effect=wrong):
                report = self.run_case('PRE-02')
            self.assertEqual(report['status'], 'BLOCKED')
            self.assertEqual(report['functional'], 'PASS')
            self.assertEqual(report['cleanup'], 'PASS')
