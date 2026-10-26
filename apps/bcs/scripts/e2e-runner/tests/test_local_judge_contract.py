"""The slow Judge's blocking/callback/continuation assertions stay in scope."""
import copy
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from bcs_e2e.local_judge_contract import PARENT,REQUIREMENTS,judge_source_binding
from bcs_e2e.run_state import RunStateError


class JudgeContractTests(unittest.TestCase):
    def test_complete22_requirements_include_timed_callback_both_running_phases_and_completion(self):
        binding=judge_source_binding();rows=binding['requirements']
        self.assertEqual(len(rows),22);self.assertEqual(sum(r['kind']=='http' for r in rows),12)
        self.assertEqual(sum(r['kind']=='poll' for r in rows),3)
        self.assertIn('one-second',rows[12]['expectation']);self.assertEqual(rows[12]['path'],'/bot/events')
        self.assertEqual(rows[10]['path'],rows[14]['path'])
        self.assertEqual(rows[-2]['method'],'DELETE');self.assertEqual(rows[-1]['actor'],'PROVIDER_ADMIN')

    def test_omitted_poll_wrong_actor_or_modified_source_never_pass(self):
        original=judge_source_binding()
        for change in ('missing','reorder','actor','method','path','kind','source','callback-path','poll-path'):
            value=copy.deepcopy(original)
            if change=='missing':value['requirements'].pop(10)
            elif change=='reorder':value['requirements'].reverse()
            elif change=='actor':value['requirements'][12]['actor']='HUMAN_LOCAL'
            elif change=='method':value['requirements'][20]['method']='GET'
            elif change=='path':value['requirements'][15]['path']='/state-machine-runs'
            elif change=='kind':value['requirements'][14]['kind']='http'
            elif change=='callback-path':value['requirements'][12]['path']='/not-events'
            elif change=='poll-path':value['requirements'][10]['path']='/not-graph'
            else:value['sources']['stories.sh']='0'*64
            with self.subTest(change=change),patch('bcs_e2e.local_judge_contract.read',return_value=value):
                with self.assertRaises(RunStateError):judge_source_binding()

    def test_owned_local_executor_does_not_grant_release_credit(self):
        from bcs_e2e.migration_inventory import migration_plan
        plan=migration_plan();row=next(r for r in plan['additional_source_maps'] if r['parent']==PARENT)
        self.assertEqual([r['id'] for r in row['requirements']],list(REQUIREMENTS))
        registered=next(r for r in plan['local_registered_executors'] if r['legacy_id']==PARENT)
        self.assertEqual(registered['execution_command'],'local-slow-judge-run')
        self.assertEqual(registered['report_command'],'local-slow-judge-report')
        self.assertEqual(registered['canonical_reporting'],'INTEGRATED_LOCAL')
        self.assertFalse(registered['grants_release_credit'])
        self.assertEqual(plan['legacy_inventory_count'],34);self.assertEqual(plan['migrated_story_count'],0)
