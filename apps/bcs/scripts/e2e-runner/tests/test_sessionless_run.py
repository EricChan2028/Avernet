import copy
import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from test_sessionless_native import profile, review
from bcs_e2e.evidence import _make_plan
from bcs_e2e.pool import pool_path, pool_scope, inspect_pool, PoolLease
from bcs_e2e.profile import ConfigError, fingerprint
from bcs_e2e.run_state import write, read
from bcs_e2e.sessionless_run import sessionless_run, assert_created, assert_group


def created():
    return {'id':'group-unique', 'created':True, 'group_kind':'normal', 'driver_bot':'bot-a',
            'participants':['bot-a','bot-b','human_owner-1'], 'context_injected':0,
            'session_id':None,'initial_session_id':None,'initial_run':None}


def detail():
    return {'id':'group-unique','originator':'human_owner-1','driver_bot':'bot-a',
            'driver_bot_owner':'human_owner-1','group_kind':'normal','group_strategy':'chat',
            'visibility':'private','status':'active','latest_running_session_id':None,
            'participants':[{'bot_uuid':x} for x in ('bot-a','bot-b','human_owner-1')],
            'label':'E2E slice-test renamed'}


class RunTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup);self.root=Path(tmp.name)
        pool=patch('bcs_e2e.pool.pool_root',return_value=self.root/'pools');pool.start();self.addCleanup(pool.stop)
        self.p=profile();r=review(self.p)
        source=self.root/'source.md';source.write_text('explicit synthetic source review, not live evidence')
        r['source_review_sha256']=hashlib.sha256(source.read_bytes()).hexdigest()
        write(self.root/'review.json',r)
        receipt={'schema_version':1,'verification_mode':'OWNER_CONFIRMED_SHARED_DATABASE',
          'receipt_id':'test-review','reviewer':'owner-1','reviewed_at':r['reviewed_at'],
          'expires_at':r['expires_at'],'profile_digest':fingerprint(self.p),'deployment_commit':'unknown',
          'target':{'environment_id':self.p['environment']['id'],'api_origin':self.p['entrypoint']['origin'],
                    'server':self.p['observer']['server'],'database':self.p['observer']['database'],'env':'pre'},
          'query_authorized':True,'owner_confirmation':{'reference':'synthetic-test-owner', 'sha256':'a'*64,
              'database_sharing':'preprod-and-production','mapping_confirmed':True,
              'tables_without_env_excluded':True,'read_scope':'current-case-resource-id-and-env',
              'allowed_assertions':['group.persisted']}}
        write(self.root/'receipt.json',receipt)
        self.args=dict(state_dir=self.root/'run',run_id='slice-test',review_path=self.root/'review.json',
             source_review=source,receipt=self.root/'receipt.json',executable=Path('/unused/mcporter'),
             allow_read=True,allow_observer_read=True,allow_write=True,confirm_run_id='slice-test')
        self.client=Mock();self.client.create.return_value=created();self.client.read.return_value=detail()
        self.client.rename.return_value={'group_id':'group-unique','name':'E2E slice-test renamed'}
        self.client.delete.return_value={'deleted':True,'id':'group-unique'}
        self.observer=Mock();self.observer.observe.return_value={'status':'OBSERVED','source':'alisddbquery',
             'rows':[{'group_id':'group-unique','env':'pre','driver_bot':'bot-a',
                      'originator':'human_owner-1','status':'active'}]}
        self.mcp=Mock();self.mcp.db_query.return_value={'success':True,'data':[]}
        for name,obj in [('NativeSessionlessClient',self.client),('AlisdDbQueryObserver',self.observer),
                         ('_reviewed_client',self.mcp)]:
            p=patch('bcs_e2e.sessionless_run.'+name,return_value=obj);p.start();self.addCleanup(p.stop)
        # A fake create still emits the correlation metadata required by the ledger.
        def create(_name, request_id):
            self.client.evidence.append({'operation':'create','submitted_request_id':request_id,'method':'POST','path':'/groups'})
            return created()
        self.client.create.side_effect=create

    def run_slice(self): return sessionless_run(self.p,**self.args)

    def test_pass_is_separate_and_deletes_exact_api_and_db_resource(self):
        report=self.run_slice()
        self.assertEqual(report['status'],'PASS',report)
        self.assertEqual(report['full_release_gate'],'NOT_QUALIFIED')
        self.assertEqual(report['counts']['total'],1)
        self.assertEqual(report['pool'],'RELEASED')
        self.client.delete.assert_called_once_with('group-unique')
        self.mcp.db_query.assert_called_once_with(_make_plan('group.persisted','group-unique','agentclawdb','pre'))
        self.assertEqual([r['kind'] for r in report['resources']],['group'])
        self.assertTrue((self.root/'run/sessionless-junit.xml').is_file())
        self.assertFalse((self.root/'run/report-PRE-01.json').exists())

    def test_no_write_authorization_is_rejected_before_state(self):
        self.args['allow_write']=False
        with self.assertRaises(ConfigError):self.run_slice()
        self.assertFalse(self.args['state_dir'].exists());self.client.create.assert_not_called()

    def test_preflight_failure_releases_empty_pool_without_create(self):
        self.client.preflight.side_effect=ConfigError('synthetic denial')
        report=self.run_slice()
        self.assertEqual(report['status'],'BLOCKED');self.assertEqual(report['pool'],'RELEASED')
        self.assertEqual(report['resources'],[]);self.client.create.assert_not_called()

    def test_ambiguous_create_never_retried_deleted_or_released(self):
        self.client.create.side_effect=TimeoutError('lost response')
        report=self.run_slice()
        self.assertEqual(report['status'],'FAIL');self.assertEqual(report['pool'],'QUARANTINED')
        self.client.create.assert_called_once();self.client.delete.assert_not_called()
        self.assertEqual(report['resources'][0]['request_id'], self.client.create.call_args.args[1])
        self.assertEqual(report['resources'][0]['origin'],'unknown')
        self.assertEqual(inspect_pool(pool_path(pool_scope(self.p)))['status'],'LEASED')

    def test_unexpected_session_retains_exact_known_group(self):
        original = self.client.create.side_effect
        self.client.create.side_effect=lambda name, request_id: {**original(name, request_id),'session_id':'unexpected'}
        report=self.run_slice()
        self.assertEqual(report['status'],'FAIL');self.assertEqual(report['pool'],'QUARANTINED')
        self.client.delete.assert_not_called();self.client.rename.assert_not_called()
        self.assertEqual(report['resources'][0]['resource_id'],'group-unique')

    def test_required_db_failure_keeps_functional_pass_but_no_cleanup(self):
        self.observer.observe.side_effect=RuntimeError('query error')
        report=self.run_slice()
        self.assertEqual(report['functional'],'PASS');self.assertEqual(report['status'],'FAIL')
        self.assertNotEqual(report['evidence_status'],'PASS');self.client.delete.assert_not_called()

    def test_delete_error_does_not_mark_deleted_or_release(self):
        self.client.delete.side_effect=TimeoutError('may have deleted')
        report=self.run_slice()
        self.assertEqual(report['cleanup'],'BLOCKED');self.assertEqual(report['pool'],'QUARANTINED')
        self.assertEqual(report['resources'][0]['state'],'live')
        self.client.delete.assert_called_once()

    def test_db_error_is_not_empty_result(self):
        self.mcp.db_query.return_value={'success':False,'data':[]}
        report=self.run_slice();self.assertEqual(report['cleanup'],'BLOCKED')
        self.assertEqual(report['resources'][0]['state'],'live')

    def test_cancel_after_intent_is_quarantined(self):
        self.client.create.side_effect=KeyboardInterrupt()
        report=self.run_slice();self.assertEqual(report['status'],'CANCELLED')
        self.assertEqual(report['pool'],'QUARANTINED');self.client.delete.assert_not_called()

    def test_wrong_owner_or_extra_participant_prevents_rename_and_delete(self):
        for change in ({'originator':'other'},{'participants':[{'bot_uuid':'outsider'}]},
                       {'latest_running_session_id':'unexpected'}):
            with self.subTest(change=change),self.assertRaises(ConfigError):
                assert_group({**detail(),**change},'group-unique',self.p)

    def test_create_contract_does_not_accept_false_as_zero_or_missing_session_keys(self):
        for field in ('context_injected','initial_session_id','initial_run'):
            value=created()
            if field=='context_injected':value[field]=False
            else:del value[field]
            with self.subTest(field=field),self.assertRaises(ConfigError):assert_created(value,self.p)

    def test_preexisting_pool_not_released(self):
        lease=PoolLease.acquire(pool_path(pool_scope(self.p)),'other-run',fingerprint(self.p));lease.close()
        report=self.run_slice();self.assertEqual(report['status'],'BLOCKED')
        self.assertEqual(inspect_pool(pool_path(pool_scope(self.p)))['run_id'],'other-run')
        self.client.preflight.assert_not_called()

    def test_source_review_mismatch_blocks_before_state(self):
        self.args['source_review'].write_text('tampered')
        with self.assertRaises(ConfigError):self.run_slice()
        self.assertFalse(self.args['state_dir'].exists())
