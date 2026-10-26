"""Five-role profile validation grants neither pilot nor remote authority."""
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from bcs_e2e.cli import main
from bcs_e2e.prepare import prepare_run
from bcs_e2e.profile import ConfigError, fingerprint, load, require_resolved, validate
from bcs_e2e.reporting import build_report
from bcs_e2e.trust_context import PROFILE_CONTRACT
from bcs_e2e.trust_registration import ROLES, SUITE


def example():
    return {'schema_version':2,'contract':PROFILE_CONTRACT,'suite':SUITE,
        'environment':{'tier':'local','id':'owned-context','expected_bcs_env':'local'},
        'entrypoint':{'kind':'singlebox','origin':'http://127.0.0.1:31000','api_face':'legacy','manifest_face':'legacy'},
        'human':{'user_id':'local-human'},
        'actors':{role:{'bot_id':'local-'+role,'owner_id':'local-human','dedicated':True,'origin':'created'} for role in ROLES},
        'auth':{'kind':'owned-local-roles'},'observer':{'kind':'local-sqlite','server':'owned-local','database':'owned-local'},
        'relationships':{'friendship':'owned-run-only'},
        'policy':{'lifecycle':'fresh-owned-runtime','finalization':'restore-visibility-retain-history',
                  'remote_attachment':False,'replay':False}}


class TrustProfileTests(unittest.TestCase):
    def test_profile_is_detached_and_preserves_five_created_roles_without_credentials(self):
        profile=example();validated=validate(profile)
        self.assertEqual(validated,profile);self.assertEqual(len(validated['actors']),5)
        validated['actors']['CEO']['bot_id']='another'
        self.assertNotEqual(fingerprint(validated),fingerprint(profile))
        self.assertNotIn('cookie',json.dumps(profile).lower())
        self.assertNotIn('token',json.dumps(profile).lower())
        self.assertEqual(validate(load(ROOT/'profiles/local.mock.json'))['schema_version'],1)

    def test_remote_origin_auth_observer_and_policy_escape_are_rejected(self):
        edits=[lambda p:p['environment'].update(tier='preprod'),lambda p:p['environment'].update(expected_bcs_env='pre'),
               lambda p:p['entrypoint'].update(origin='https://example.test'),
               lambda p:p['entrypoint'].update(origin='http://localhost:31000'),
               lambda p:p['entrypoint'].update(origin='http://127.0.0.1:31000/path'),
               lambda p:p['entrypoint'].update(origin='http://127.0.0.1:80'),
               lambda p:p['entrypoint'].update(kind='bcs-direct'),lambda p:p['entrypoint'].update(api_face='public-v1'),
               lambda p:p.update(auth={'header_env':{'Cookie':'PRIVATE_COOKIE'}}),
               lambda p:p['observer'].update(kind='alisddbquery'),lambda p:p['policy'].update(remote_attachment=True),
               lambda p:p['policy'].update(replay=0),lambda p:p['relationships'].update(friendship='unchanged')]
        for edit in edits:
            value=example();edit(value)
            with self.subTest(value=value),self.assertRaises(ConfigError):validate(value)

    def test_actor_scope_and_strict_shape_reject_borrowed_or_malformed_bindings(self):
        edits=[lambda p:p['actors'].pop('CS'),lambda p:p['actors'].update(driver=p['actors']['CEO']),
               lambda p:p['actors']['PM'].update(bot_id=p['actors']['CEO']['bot_id']),
               lambda p:p['actors']['PM'].update(owner_id='foreign'),lambda p:p['actors']['PM'].update(dedicated=1),
               lambda p:p['actors']['PM'].update(origin='borrowed'),lambda p:p['actors']['PM'].update(token='not-allowed'),
               lambda p:p['human'].update(user_id='bad id'),lambda p:p.update(schema_version=True),
               lambda p:p.update(extra='unknown'),lambda p:p.update(contract='unreviewed')]
        for edit in edits:
            value=example();edit(value)
            with self.subTest(value=value),self.assertRaises(ConfigError):validate(value)

    def test_loaded_typed_profile_cannot_enter_existing_cli_commands(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);profile=root/'profile.json';profile.write_text(json.dumps(example()))
            self.assertEqual(load(profile)['schema_version'],2)
            commands=[['plan'],['check'],
                      ['prepare','--state-dir',str(root/'new'),'--run-id','test'],
                      ['run','--state-dir',str(root/'missing'),'--run-id','test','--case','PRE-01','--confirm-run-id','test'],
                      ['cleanup','--state-dir',str(root/'missing')]]
            for command in commands:
                with self.subTest(command=command),contextlib.redirect_stdout(io.StringIO()) as out, \
                     patch('bcs_e2e.cli.ReadClient') as network,patch('bcs_e2e.cli.prepare_run') as prepare:
                    self.assertEqual(main(command+['--profile',str(profile)]),2)
                    network.assert_not_called();prepare.assert_not_called()
                    self.assertIn('command integration is not yet available',json.loads(out.getvalue())['reason'])
            self.assertFalse((root/'new').exists())

    def test_programmatic_pilot_preparation_reporting_and_resolution_are_denied(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'state';profile=example()
            with patch('bcs_e2e.prepare.PoolLease.acquire') as lease,self.assertRaises(ConfigError):prepare_run(profile,root,'test')
            lease.assert_not_called();self.assertFalse(root.exists())
            from bcs_e2e.run_state import RunStateError
            with self.assertRaises((ConfigError,RunStateError)):build_report(profile,root)
            with self.assertRaises(ConfigError):require_resolved(profile)

class TrustContextDriftTests(unittest.TestCase):
    def test_in_memory_profile_context_run_and_runtime_config_drift_are_rejected(self):
        from types import SimpleNamespace
        from bcs_e2e.owned_bcs import file_sha256
        from bcs_e2e.run_state import RunStateError, write
        from bcs_e2e.trust_context import TrustRunContext
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);config=root/'bcs-config.json';config.write_text('{}')
            ctx=TrustRunContext.__new__(TrustRunContext)
            ctx.root=root;ctx.run_id='owned-context';ctx.profile=validate(example())
            ctx.profile_digest=fingerprint(ctx.profile)
            ctx.context={'schema_version':1,'runtime_config_digest':file_sha256(config),'ready_to_execute':False}
            ctx.context_digest=fingerprint(ctx.context)
            ctx.run={'schema_version':1,'status':'CONTEXT_BOUND'};ctx.run_digest=fingerprint(ctx.run)
            fixture={'schema_version':1};ctx.fixture_digest=fingerprint(fixture)
            ctx.runtime=SimpleNamespace(root=root,run_id=ctx.run_id,inputs=SimpleNamespace(
                root=root,config_path=config,origin=ctx.profile['entrypoint']['origin'],human_id='local-human'))
            for name,value in (('profile.json',ctx.profile),('trust-run-context.json',ctx.context),('run.json',ctx.run),('local-fixtures.json',fixture)):
                write(root/name,value)
            ctx.validate(ctx.runtime)
            for obj,key,value in ((ctx.profile['actors']['CEO'],'bot_id','foreign'),
                                  (ctx.context,'ready_to_execute',True),(ctx.run,'status','COMPLETED')):
                original=obj[key];obj[key]=value
                with self.assertRaises(RunStateError):ctx.validate(ctx.runtime)
                obj[key]=original
            config.write_text('{"changed":true}')
            with self.assertRaises(RunStateError):ctx.validate(ctx.runtime)
