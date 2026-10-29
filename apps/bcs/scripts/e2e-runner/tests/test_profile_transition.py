"""Storage-origin-only handoff; all state and adapters are isolated local mocks."""
import copy
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from bcs_e2e.adapters import LocalMockAdapter
from bcs_e2e.cli import main
from bcs_e2e.pool import pool_path,pool_scope
from bcs_e2e.profile import ConfigError,fingerprint,load
from bcs_e2e.profile_transition import CONTRACT,ProfileTransition,validate_transition
from bcs_e2e.reporting import build_report
from bcs_e2e.retained import retained_run
from bcs_e2e.retained_serial import policy_view
from bcs_e2e.run_state import read,write,RunStateError


class ProfileTransitionTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name)
        registry=patch('bcs_e2e.pool.pool_root',return_value=self.root/'pools')
        registry.start();self.addCleanup(registry.stop)
        self.old=load(ROOT/'profiles/local.mock.json')
        self.new=copy.deepcopy(self.old)
        self.new['file_transfer']={'allowed_storage_origins':['https://storage.example.test']}
        self.old_path=self.root/'old-profile.json';write(self.old_path,self.old)
        self.review_path=self.root/'review.json'
        now=int(time.time())
        self.review={'schema_version':1,'contract':CONTRACT,'run_id':'new','previous_run_id':'old',
            'previous_profile_digest':fingerprint(self.old),'profile_digest':fingerprint(self.new),
            'storage_origins':self.new['file_transfer']['allowed_storage_origins'],
            'supervisor':self.new['human']['user_id'],'reviewed_at':now-1,'expires_at':now+300,
            'reference':'source-storage-review','evidence_sha256':'a'*64}
        write(self.review_path,self.review)
        self.lock=pool_path(pool_scope(self.old))

    def start_old(self):
        result=retained_run(self.old,state_dir=self.root/'old',run_id='old',case_id='RETAIN-GROUP-01',
            allow_write=True,confirm_run_id='old',serial_preflight=True)
        self.assertEqual(result['status'],'PASS')

    def run_new(self,**overrides):
        args=dict(profile=self.new,state_dir=self.root/'new',run_id='new',case_id='RETAIN-FILE-01',
            allow_write=True,confirm_run_id='new',serial_preflight=True,previous_state_dir=self.root/'old',
            previous_profile=self.old_path,profile_transition_review=self.review_path)
        args.update(overrides)
        return retained_run(**args)

    def snapshot_old(self):
        return {str(p.relative_to(self.root/'old')):p.read_bytes()
                for p in (self.root/'old').rglob('*') if p.is_file()}

    def validate(self,review=None,old=None,new=None,live=True):
        return validate_transition(review or self.review,old or self.old,new or self.new,
            run_id='new',previous_run_id='old',live=live)

    def test_serial_transition_preserves_old_profile_run_and_lease_scope(self):
        self.start_old();before=self.snapshot_old();old_input=self.old_path.read_bytes()
        result=self.run_new()
        self.assertEqual((result['status'],result['canonical_summary_status']),('PASS','RETAINED_CASE_PASS'))
        self.assertEqual(self.snapshot_old(),before);self.assertEqual(self.old_path.read_bytes(),old_input)
        self.assertEqual(read(self.lock)['profile_digest'],fingerprint(self.new))
        self.assertEqual(read(self.lock)['run_id'],'new')
        receipt=read(self.root/'new/profile-transition.json')
        self.assertEqual(receipt['previous_profile'],self.old)
        self.assertEqual(receipt['review'],self.review)
        self.assertEqual((self.root/'new/profile-transition.json').stat().st_mode & 0o777,0o600)
        summary=build_report(self.new,self.root/'new')
        self.assertEqual(summary['retention']['serial_admission']['profile_transition'],CONTRACT)
        self.assertEqual(summary['scope']['full_release_status'],'NOT_QUALIFIED')

    def test_cli_uses_explicit_old_profile_and_review(self):
        self.start_old();path=self.root/'new-profile.json';write(path,self.new)
        args=['retained-run','--profile',str(path),'--state-dir',str(self.root/'new'),
            '--run-id','new','--case','RETAIN-FILE-01','--allow-write','--confirm-run-id','new',
            '--serial-preflight','--previous-state-dir',str(self.root/'old'),
            '--previous-profile',str(self.old_path),'--profile-transition-review',str(self.review_path)]
        with contextlib.redirect_stdout(io.StringIO()) as output:
            rc=main(args)
        self.assertEqual(rc,0);self.assertEqual(json.loads(output.getvalue())['status'],'PASS')

    def test_unreviewed_profile_change_keeps_existing_reservation(self):
        self.start_old();before=self.lock.read_bytes()
        with self.assertRaises(RunStateError):
            self.run_new(previous_profile=None,profile_transition_review=None)
        self.assertEqual(self.lock.read_bytes(),before);self.assertFalse((self.root/'new').exists())

    def test_rejects_other_changes_even_with_matching_new_digest(self):
        edits=[('policy','timeout_seconds',31),('human','user_id','other'),
            ('entrypoint','origin','mock://other'),('observer','database','other'),
            ('policy','fixture_pool','other'),('auth','header_env',{'Cookie':'OTHER_COOKIE'})]
        for section,key,value in edits:
            new=copy.deepcopy(self.new);new[section][key]=value
            review={**self.review,'profile_digest':fingerprint(new)}
            with self.subTest(section=section,key=key),self.assertRaises(ConfigError):self.validate(review,new=new)
        new=copy.deepcopy(self.new);new['actors']['member']['bot_id']='another-bot'
        with self.assertRaises(ConfigError):self.validate({**self.review,'profile_digest':fingerprint(new)},new=new)

    def test_only_first_nonempty_exact_origin_addition_is_supported(self):
        for origins in ([],['https://*.example.test'],['https://storage.example.test/path'],
                        ['https://storage.example.test','https://storage.example.test/']):
            new=copy.deepcopy(self.new);new['file_transfer']['allowed_storage_origins']=origins
            review={**self.review,'profile_digest':fingerprint(new),'storage_origins':origins}
            with self.subTest(origins=origins),self.assertRaises(ConfigError):self.validate(review,new=new)
        old=copy.deepcopy(self.new)
        with self.assertRaises(ConfigError):self.validate({**self.review,'previous_profile_digest':fingerprint(old)},old=old)
        old=copy.deepcopy(self.old);old['file_transfer']={'allowed_storage_origins':[]}
        self.validate({**self.review,'previous_profile_digest':fingerprint(old)},old=old)

    def test_review_identity_scope_and_time_are_strict(self):
        for edit in ({'run_id':'other'},{'previous_run_id':'other'},{'supervisor':'other'},
                     {'schema_version':True},{'contract':'other'},{'storage_origins':[]},
                     {'evidence_sha256':'bad'},{'expires_at':True},{'expires_at':1},
                     {'reviewed_at':1,'expires_at':4000},{'extra':1}):
            with self.subTest(edit=edit),self.assertRaises(ConfigError):self.validate({**self.review,**edit})

    def test_paired_inputs_and_previous_state_required_before_preflight(self):
        for edit in ({'previous_profile':None},{'profile_transition_review':None},
                     {'previous_state_dir':None},{'serial_preflight':False}):
            with self.subTest(edit=edit),patch.object(LocalMockAdapter,'preflight') as preflight:
                with self.assertRaises(ConfigError):self.run_new(**edit)
                preflight.assert_not_called()
        self.assertFalse(self.lock.exists())

    def test_wrong_old_profile_or_missing_reservation_does_not_create_run(self):
        with self.assertRaises(ConfigError):self.run_new()
        self.assertFalse(self.lock.exists());self.assertFalse((self.root/'new').exists())
        self.start_old();before=self.lock.read_bytes()
        write(self.old_path,self.new)
        with self.assertRaises(ConfigError):self.run_new()
        self.assertEqual(self.lock.read_bytes(),before)

    def test_failed_dependencies_leave_all_old_artifacts_unchanged(self):
        self.start_old();before=self.snapshot_old();lock=self.lock.read_bytes()
        with patch.object(LocalMockAdapter,'preflight',return_value={'status':'BLOCKED'}):
            result=self.run_new()
        self.assertEqual(result['functional'],'NOT_RUN');self.assertFalse((self.root/'new').exists())
        self.assertEqual(self.lock.read_bytes(),lock);self.assertEqual(self.snapshot_old(),before)

    def test_review_changes_or_expires_during_preflight_cannot_transfer(self):
        self.start_old();lock=self.lock.read_bytes();original=LocalMockAdapter.preflight
        for change in ('drift','expiry'):
            write(self.review_path,self.review)
            def drift(adapter):
                result=original(adapter)
                if change=='drift':write(self.review_path,{**self.review,'reference':'changed'})
                else:clock.return_value=self.review['expires_at']+1
                return result
            with self.subTest(change=change),patch('bcs_e2e.profile_transition.time.time',return_value=self.review['reviewed_at']+2) as clock,\
                 patch.object(LocalMockAdapter,'preflight',drift),self.assertRaises(ConfigError):
                self.run_new()
            self.assertEqual(self.lock.read_bytes(),lock);self.assertFalse((self.root/'new').exists())

    def test_offline_reports_do_not_require_live_review_or_source_profile(self):
        self.start_old();self.run_new()
        self.old_path.unlink();self.review_path.unlink()
        with patch('bcs_e2e.profile_transition.time.time',return_value=self.review['expires_at']+1):
            self.assertEqual(build_report(self.new,self.root/'new')['status'],'RETAINED_CASE_PASS')
        snapshot=read(self.root/'new/profile-transition.json');snapshot['review']['reference']='drifted'
        write(self.root/'new/profile-transition.json',snapshot)
        self.assertNotEqual(build_report(self.new,self.root/'new')['status'],'RETAINED_CASE_PASS')

    def test_snapshot_write_failure_after_transfer_never_executes_business_or_retries(self):
        self.start_old()
        from bcs_e2e import retained_serial
        original=retained_serial.write
        def fail(path,value):
            if path.name=='profile-transition.json':raise OSError('synthetic disk failure')
            return original(path,value)
        with patch('bcs_e2e.retained_serial.write',fail),\
             patch('bcs_e2e.retained.run_retained_case') as business,self.assertRaises(OSError):
            self.run_new()
        business.assert_not_called();self.assertEqual(read(self.lock)['run_id'],'new')
        self.assertFalse((self.root/'new/run.sqlite').exists())
        with self.assertRaises(Exception):self.run_new()


if __name__=='__main__':unittest.main()
