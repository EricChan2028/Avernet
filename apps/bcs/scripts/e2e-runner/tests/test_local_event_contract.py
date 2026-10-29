"""Callback and control obligations cannot disappear behind the HTTP-only map."""
import copy
from pathlib import Path
import re
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from bcs_e2e.local_event_contract import ROOT,PARENT,REQUIREMENTS,event_source_binding,source_body
from bcs_e2e.run_state import RunStateError
from bcs_e2e.inventory import current_manifest


class EventContractTests(unittest.TestCase):
    def test_original20_obligations_include13_api_operations_and_four_callbacks(self):
        binding=event_source_binding();rows=binding['requirements']
        self.assertEqual(len(rows),20)
        self.assertEqual(sum(r['kind']=='http' for r in rows),13)
        self.assertEqual(sum(r['kind']=='delivery' for r in rows),4)
        self.assertEqual(rows[0]['kind'],'fixture');self.assertEqual(rows[1]['kind'],'identity')
        self.assertEqual(rows[14]['kind'],'observation')
        self.assertEqual(rows[12]['actor'],'HUMAN_LOCAL')
        self.assertEqual(rows[17]['method'],'DELETE')
        self.assertIn('revision=3',rows[17]['path'])

    def test_required_callback_identity_order_and_source_drift_rejected(self):
        original=event_source_binding()
        for change in ('callback','order','actor','method','path','source','kind'):
            binding=copy.deepcopy(original)
            if change=='callback':binding['requirements'].pop(3)
            elif change=='order':binding['requirements'].reverse()
            elif change=='actor':binding['requirements'][12]['actor']='HUMAN_PRINCIPAL'
            elif change=='method':binding['requirements'][17]['method']='POST'
            elif change=='path':binding['requirements'][4]['path']='/event-subscriptions'
            elif change=='source':binding['sources']['http_provider_judge_mock.py']='0'*64
            else:binding['requirements'][14]['kind']='http'
            with self.subTest(change=change),patch('bcs_e2e.local_event_contract.read',return_value=binding):
                with self.assertRaises(RunStateError):event_source_binding()

    def test_embedded_python_does_not_truncate_subscription_delete_or_group_cleanup(self):
        text=(ROOT.parent/'e2e-test/stories.sh').read_text()
        body=source_body(text,PARENT,'story_provider_callback_survives_slow_judge')
        self.assertIn('revision=3',body);self.assertIn('${public_prefix}/groups/${group_id}"',body)
        self.assertIn('len(values) == len(set(values))',body)
        for changed in (text.replace(PARENT+'() {',PARENT+'() {\n'+PARENT+'() {',1),
                        text.replace('story_provider_callback_survives_slow_judge() {','unexpected_story() {',1)):
            with self.assertRaises(ValueError):source_body(changed,PARENT,'story_provider_callback_survives_slow_judge')

    def test_source_plan_registers_local_executor_without_remote_release_credit(self):
        from bcs_e2e.migration_inventory import migration_plan
        plan=migration_plan()
        row=next(row for row in plan['additional_source_maps'] if row['parent']==PARENT)
        self.assertEqual([r['id'] for r in row['requirements']],list(REQUIREMENTS))
        registered=next(r for r in plan['local_registered_executors'] if r['legacy_id']==PARENT)
        self.assertFalse(registered['grants_release_credit'])
        self.assertEqual(registered['required_assertion_count'],20)
        self.assertEqual(plan['legacy_inventory_count'],len(current_manifest()['legacy_inventory']));self.assertEqual(plan['migrated_story_count'],0)
