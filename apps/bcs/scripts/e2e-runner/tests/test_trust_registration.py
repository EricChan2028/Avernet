"""Complete parent registration is frozen and cannot confer remote/release credit."""
import copy
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from bcs_e2e.inventory import current_manifest
from bcs_e2e.migration_inventory import migration_plan
from bcs_e2e.prepare import prepare_run
from bcs_e2e.profile import ConfigError, load
from bcs_e2e.run_state import RunStateError, read, write
from bcs_e2e.trust_registration import TrustRegistration, SUITE, CASE_ID, validate_registration


class TrustRegistrationTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name)
        self.runtime=SimpleNamespace(state='RUNNING',ready=True,root=self.root,run_id='registered-local',binary_sha256='a'*64)
        self.manifest=current_manifest(SUITE)

    def test_complete_static_contract_maps_five_requirements_and_preserves_denominator(self):
        case=self.manifest['cases'][0]
        self.assertEqual(len(case['assertions']),58)
        self.assertEqual(len(case['requirements']),5)
        self.assertEqual(len(self.manifest['legacy_inventory']),34)
        self.assertEqual(self.manifest['legacy_inventory'],current_manifest()['legacy_inventory'])
        self.assertEqual(set(case['assertions']),set(sum(case['requirements'].values(),[])+case['finalization_assertions']))
        self.assertEqual(case['allowed_environments'],['owned-local'])
        result=migration_plan()
        self.assertEqual(result['migrated_story_count'],0)
        self.assertEqual(result['passed_assertion_count'],0)
        self.assertFalse(result['local_registered_executors'][0]['grants_release_credit'])

    def test_missing_remapped_extra_and_remote_contract_fields_are_rejected(self):
        edits=[lambda c:c['assertions'].pop(),lambda c:c['requirements'].pop('FRIEND.list'),
               lambda c:c['requirements']['FRIEND.cli-accept'].pop(),lambda c:c.update(version=True),
               lambda c:c['roles'].pop(),lambda c:c.update(allowed_environments=['preprod']),
               lambda c:c.update(remote_validation='PASS'),lambda c:c['effects'].append('delete:owned_group'),
               lambda c:c['required_evidence'].remove('cli'),lambda c:c['steps'].reverse(),
               lambda c:c.update(unreviewed_route='/foreign')]
        for edit in edits:
            value=copy.deepcopy(self.manifest);edit(value['cases'][0])
            with self.subTest(value=value['cases'][0]),self.assertRaises(RunStateError):validate_registration(value)
        for change in ('remove-parent','change-baseline'):
            value=copy.deepcopy(self.manifest)
            if change=='remove-parent':value['legacy_inventory'].pop()
            else:value['baseline_commit']='b'*40
            with self.assertRaises(RunStateError):validate_registration(value)

    def test_freeze_before_fixtures_receipt_binds_artifact_and_runtime(self):
        registration=TrustRegistration(cli_sha256='b'*64);registration.attach(self.runtime)
        receipt=read(self.root/'trust-registration.json')
        self.assertEqual(receipt['manifest_digest'],registration.manifest_digest)
        self.assertEqual(receipt['executor_digest'],registration.executor_digest)
        self.assertEqual(read(self.root/'manifest.json'),self.manifest)
        self.assertEqual((self.root/'trust-registration.json').stat().st_mode&0o777,0o600)
        self.assertEqual(registration.binding()['case_id'],CASE_ID)
        with self.assertRaises(RunStateError):registration.attach(self.runtime)

    def test_receipt_manifest_runtime_and_executor_drift_are_blocked_without_overwrite(self):
        registration=TrustRegistration(cli_sha256='b'*64);registration.attach(self.runtime)
        receipt=read(self.root/'trust-registration.json')
        for field,value in (('run_id','foreign'),('case_version',True),('cli_binary_sha256','c'*64)):
            changed={**receipt,field:value};write(self.root/'trust-registration.json',changed)
            before=(self.root/'trust-registration.json').read_bytes()
            with self.assertRaises(RunStateError):registration.validate(self.runtime)
            self.assertEqual((self.root/'trust-registration.json').read_bytes(),before)
        write(self.root/'trust-registration.json',receipt)
        with patch('bcs_e2e.inventory.executor_digest',return_value='0'*64),self.assertRaises(RunStateError):
            registration.validate(self.runtime)
        self.runtime.run_id='foreign'
        with self.assertRaises(RunStateError):registration.validate(self.runtime)
        self.runtime.run_id='registered-local'
        changed=copy.deepcopy(self.manifest);changed['cases'][0]['assertions'].pop()
        write(self.root/'manifest.json',changed)
        with self.assertRaises(RunStateError):registration.validate(self.runtime)
        self.assertEqual(read(self.root/'manifest.json'),changed)

    def test_registration_after_fixture_creation_and_artifact_drift_before_attach_rejected(self):
        registration=TrustRegistration(cli_sha256='b'*64)
        with patch('bcs_e2e.inventory.executor_digest',return_value='0'*64),self.assertRaises(RunStateError):
            registration.attach(self.runtime)
        self.assertFalse((self.root/'manifest.json').exists())
        write(self.root/'local-fixtures.json',{'schema_version':1})
        with self.assertRaises(RunStateError):registration.attach(self.runtime)
        self.assertFalse((self.root/'trust-registration.json').exists())

    def test_shared_prepare_cannot_admit_registered_friend_mutations_with_pilot_profile(self):
        profile=load(ROOT/'profiles/local.mock.json')
        with patch('bcs_e2e.prepare.PoolLease.acquire') as lease,self.assertRaises(ConfigError):
            prepare_run(profile,self.root/'prepared','foreign-context',manifest=self.manifest)
        lease.assert_not_called();self.assertFalse((self.root/'prepared').exists())

    def test_requirements_are_not_pass_without_every_mapped_assertion(self):
        registration=TrustRegistration(cli_sha256='b'*64)
        assertions=[{'id':key,'status':'PASS'} for key in self.manifest['cases'][0]['assertions']]
        self.assertTrue(all(row['status']=='PASS' for row in registration.requirement_results(assertions)))
        assertions=[row for row in assertions if not row['id'].endswith('cli-ceo-list-includes-pm')]
        results={row['id']:row['status'] for row in registration.requirement_results(assertions)}
        self.assertEqual(results['FRIEND.cli-accept'],'INCOMPLETE')
        self.assertEqual(results['FRIEND.public-accept'],'PASS')

    def test_duplicate_or_malformed_assertions_cannot_project_requirement_pass(self):
        registration=TrustRegistration(cli_sha256='b'*64)
        item={'id':CASE_ID+'.CEO-ENG-friends','status':'PASS'}
        for assertions in ([item,item], [{'id':item['id'],'status':True}], [{'id':'foreign','status':'PASS'}]):
            with self.assertRaises(RunStateError):registration.requirement_results(assertions)

    def test_migration_contract_change_is_detected_even_when_manifest_is_unchanged(self):
        registration=TrustRegistration(cli_sha256='b'*64);registration.attach(self.runtime)
        original_read=read
        def changed(path):
            value=original_read(path)
            if path.name=='migration-contracts.json':value['source_revision']='f'*40
            return value
        with patch('bcs_e2e.trust_registration.read',side_effect=changed),self.assertRaises(RunStateError):
            registration.validate(self.runtime)
        with self.assertRaises(RunStateError):TrustRegistration(cli_sha256='not-a-digest')

    def test_in_memory_denominator_drift_and_projection_alias_are_rejected(self):
        registration=TrustRegistration(cli_sha256='b'*64);registration.attach(self.runtime)
        assertions=[{'id':key,'status':'PASS'} for key in registration.manifest['cases'][0]['assertions']]
        projected=registration.requirement_results(assertions)
        projected[0]['assertion_ids'].clear()
        registration.validate(self.runtime)
        registration.manifest['cases'][0]['assertions'].pop()
        with self.assertRaises(RunStateError):registration.validate(self.runtime)
        with self.assertRaises(RunStateError):registration.requirement_results(assertions)
