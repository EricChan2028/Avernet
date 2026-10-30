"""Full17-step source mapping, not execution or release coverage."""
import copy
from pathlib import Path
import re
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from bcs_e2e.local_structured_contract import PARENT,REQUIREMENTS,ROOT,original_body,structured_source_binding
from bcs_e2e.run_state import RunStateError


class StructuredContractTests(unittest.TestCase):
    def test_embedded_python_braces_do_not_truncate_remaining15_operations(self):
        text=(ROOT.parent/'e2e-test/stories.sh').read_text();body=original_body(text)
        self.assertIn('json.dumps({',body)
        self.assertIn('api_delete "/groups/${group_id}?bot_id=${BOT_CEO_UUID}"',body)
        self.assertIn('must be failed before rerun',body)
        self.assertEqual(len(re.findall(r'^    (?:api|bot)_(?:get|post|patch|delete) ',body,re.M)),17)
        start=text.index(PARENT+'() {');old=text[start:text.index('\n}',start)]
        self.assertEqual(len(re.findall(r'^    (?:api|bot)_(?:get|post|patch|delete) ',old,re.M)),2)

    def test_complete_original_order_authentication_and_source_hash(self):
        original=structured_source_binding();self.assertEqual([r['id'] for r in original['requirements']],list(REQUIREMENTS))
        self.assertEqual([r['actor'] for r in original['requirements']],['HUMAN','HUMAN','CEO']+['HUMAN']*14)
        for edit in ('missing','reverse','auth','method','path','hash'):
            value=copy.deepcopy(original)
            if edit=='missing':value['requirements'].pop()
            elif edit=='reverse':value['requirements'].reverse()
            elif edit=='auth':value['requirements'][2]['actor']='HUMAN'
            elif edit=='method':value['requirements'][-1]['method']='GET'
            elif edit=='path':value['requirements'][7]['path']='/runs'
            else:value['sources']['stories.sh']='0'*64
            with self.subTest(edit=edit),patch('bcs_e2e.local_structured_contract.read',return_value=value):
                with self.assertRaises(RunStateError):structured_source_binding()

    def test_source_boundary_missing_duplicate_or_next_parent_drift_rejected(self):
        text=(ROOT.parent/'e2e-test/stories.sh').read_text()
        for value in (text.replace(PARENT+'() {',PARENT+'() {\n'+PARENT+'() {',1),
                      text.replace('story_user_receives_group_event_webhooks() {','unexpected_next_story() {',1),
                      text.replace(PARENT+'() {','unexpected_parent() {',1)):
            with self.assertRaises(ValueError):original_body(value)

    def test_plan_registers_local_parent_without_release_credit(self):
        from bcs_e2e.migration_inventory import migration_plan
        plan=migration_plan();mapped=next(r for r in plan['additional_source_maps'] if r['parent']==PARENT)
        self.assertEqual(len(mapped['requirements']),17)
        row=next(r for r in plan['local_registered_executors'] if r['legacy_id']==PARENT)
        self.assertEqual(row['required_assertion_count'],17)
        self.assertFalse(row['grants_release_credit'])
        self.assertEqual(plan['legacy_inventory_count'],41);self.assertEqual(plan['migrated_story_count'],0)
