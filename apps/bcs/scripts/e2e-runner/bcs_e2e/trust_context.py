"""Five-role owned-local Profile and immutable RunContext, not execution authority.

These artifacts precede business writes. They cannot be supplied to the old
pilot lifecycle; canonical report ingestion is a separate integration step.
"""
from __future__ import annotations

import json
import os
from urllib.parse import urlsplit

from .profile import ConfigError, fingerprint, identifier, load, object_keys, origin
from .run_state import RunStateError, read
from .trust_registration import ROLES, SUITE

PROFILE_CONTRACT = 'owned-local-trusted-profile-v1'
CONTEXT_CONTRACT = 'owned-local-trusted-run-context-v2'


def validate_profile(raw: object) -> dict:
    value = object_keys(raw, {'schema_version','contract','suite','environment','entrypoint','human',
                              'actors','auth','observer','relationships','policy'}, 'trusted profile')
    if type(value['schema_version']) is not int or value['schema_version'] != 2 or value['contract'] != PROFILE_CONTRACT or value['suite'] != SUITE:
        raise ConfigError('trusted profile: unsupported version or contract')
    env = object_keys(value['environment'], {'tier','id','expected_bcs_env'}, 'trusted environment')
    identifier(env['id'], 'trusted environment ID')
    if env['tier'] != 'local' or env['expected_bcs_env'] != 'local':
        raise ConfigError('trusted profile: only the owned local environment is supported')
    entry = object_keys(value['entrypoint'], {'kind','origin','api_face','manifest_face'}, 'trusted entrypoint')
    target = urlsplit(origin(entry['origin'], 'local'))
    if (entry['kind'] != 'singlebox' or entry['api_face'] != 'legacy' or entry['manifest_face'] != 'legacy'
            or target.scheme != 'http' or target.hostname != '127.0.0.1' or not target.port
            or entry['origin'] != 'http://127.0.0.1:'+str(target.port) or not 1024 <= target.port <= 65535):
        raise ConfigError('trusted profile: require the exact owned loopback legacy entrypoint')
    human = object_keys(value['human'], {'user_id'}, 'trusted Human')
    identifier(human['user_id'], 'trusted Human ID')
    actors = object_keys(value['actors'], set(ROLES), 'trusted actors')
    ids = []
    for role in ROLES:
        actor = object_keys(actors[role], {'bot_id','owner_id','dedicated','origin'}, 'trusted actor')
        ids.append(identifier(actor['bot_id'], 'trusted Bot ID'))
        if actor['owner_id'] != human['user_id'] or actor['dedicated'] is not True or actor['origin'] != 'created':
            raise ConfigError('trusted profile: actors must be distinct created owned-local Bots')
    if len(set(ids)) != len(ROLES):
        raise ConfigError('trusted profile: duplicate Bot binding')
    expected = {'auth':{'kind':'owned-local-roles'},
                'observer':{'kind':'local-sqlite','server':'owned-local','database':'owned-local'},
                'relationships':{'friendship':'owned-run-only'},
                'policy':{'lifecycle':'fresh-owned-runtime','finalization':'restore-visibility-retain-history',
                          'remote_attachment':False,'replay':False}}
    for key, required in expected.items():
        if fingerprint(value[key]) != fingerprint(required):
            raise ConfigError('trusted profile: fixed local policy, observer or credential boundary differs')
    return json.loads(json.dumps(value, allow_nan=False))


def _exclusive(path, value):
    data=(json.dumps(value,sort_keys=True,indent=2,allow_nan=False)+'\n').encode()
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'wb') as stream:
        stream.write(data);stream.flush();os.fsync(stream.fileno())
    parent=os.open(path.parent,os.O_RDONLY)
    try:os.fsync(parent)
    finally:os.close(parent)


class TrustRunContext:
    def __init__(self, registration, identities):
        from .local_role_identity import OwnedLocalRoleIdentity
        if (type(identities) is not OwnedLocalRoleIdentity or identities.fixtures.registration is not registration
                or identities.fixture_set != 'trusted-team' or set(identities._verified) != set(ROLES)):
            raise RunStateError('RunContext requires all freshly verified registered trusted-team identities')
        identities._require_roles(ROLES)
        fixtures=identities.fixtures
        verified=fixtures.check()
        self.runtime=identities.runtime
        self.root=self.runtime.root
        self.run_id=self.runtime.run_id
        self.fixture_digest=fingerprint(read(fixtures.path))
        self.profile=validate_profile({'schema_version':2,'contract':PROFILE_CONTRACT,'suite':SUITE,
            'environment':{'tier':'local','id':self.run_id,'expected_bcs_env':'local'},
            'entrypoint':{'kind':'singlebox','origin':identities.origin,'api_face':'legacy','manifest_face':'legacy'},
            'human':{'user_id':identities.human},
            'actors':{role:{key:verified['roles'][role][key] for key in ('bot_id','owner_id','dedicated','origin')} for role in ROLES},
            'auth':{'kind':'owned-local-roles'},
            'observer':{'kind':'local-sqlite','server':'owned-local','database':'owned-local'},
            'relationships':{'friendship':'owned-run-only'},
            'policy':{'lifecycle':'fresh-owned-runtime','finalization':'restore-visibility-retain-history',
                      'remote_attachment':False,'replay':False}})
        self.profile_digest=fingerprint(self.profile)
        self.context={'schema_version':1,'contract':CONTEXT_CONTRACT,'run_id':self.run_id,
            'runtime_dir':str(self.root),'profile_digest':self.profile_digest,'fixture_digest':self.fixture_digest,
            'registration_digest':fingerprint(registration.receipt),'runtime_config_digest':fixtures.config_digest,**registration.binding(),
            'canonical_reporting':'COMPLETION_REQUIRED','ready_to_execute':False}
        self.context_digest=fingerprint(self.context)
        self.run={'schema_version':1,'run_id':self.run_id,'status':'CONTEXT_BOUND','ready_to_execute':False,
            'adapter_kind':'singlebox','profile_digest':self.profile_digest,'fixture_digest':self.fixture_digest,
            'run_context_digest':self.context_digest,**registration.binding(),
            'runtime_dir':str(self.root),'canonical_reporting':'COMPLETION_REQUIRED',
            'note':'Identity and inventory binding only; not a completed canonical Run or remote authority.'}
        self.run_digest=fingerprint(self.run)
        paths=(self.root/'profile.json',self.root/'trust-run-context.json',self.root/'run.json')
        if any(path.exists() or path.is_symlink() for path in paths):
            raise RunStateError('trusted RunContext cannot overwrite or adopt existing artifacts')
        try:
            for path,value in zip(paths,(self.profile,self.context,self.run)):_exclusive(path,value)
        except OSError as error:
            from .errors import has_cancellation
            if has_cancellation(error):
                raise KeyboardInterrupt('trusted RunContext publication cancelled') from None
            raise RunStateError('trusted RunContext publication failed; retain partial artifacts') from None
        self.validate(self.runtime)

    def validate(self, runtime):
        from .owned_bcs import file_sha256
        if (runtime is not self.runtime or runtime.root != self.root or runtime.run_id != self.run_id
                or runtime.inputs is None or runtime.inputs.root != self.root
                or runtime.inputs.config_path != self.root/'bcs-config.json'
                or runtime.inputs.origin != self.profile['entrypoint']['origin']
                or runtime.inputs.human_id != self.profile['human']['user_id']
                or file_sha256(runtime.inputs.config_path) != self.context['runtime_config_digest']
                or fingerprint(self.profile) != self.profile_digest or fingerprint(self.context) != self.context_digest
                or fingerprint(self.run) != self.run_digest
                or (self.root/'profile.json').stat().st_size > 65_536
                or fingerprint(load(self.root/'profile.json')) != self.profile_digest
                or fingerprint(read(self.root/'trust-run-context.json')) != self.context_digest
                or fingerprint(read(self.root/'run.json')) != self.run_digest
                or fingerprint(read(self.root/'local-fixtures.json')) != self.fixture_digest):
            raise RunStateError('trusted Profile, RunContext or fixture binding changed')

    def binding(self):
        self.validate(self.runtime)
        return {'profile_digest':self.profile_digest,'run_context_digest':self.context_digest}
