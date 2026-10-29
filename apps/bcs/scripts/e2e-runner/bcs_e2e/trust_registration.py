"""Source-bound registration of the complete local trusted-team business contract.

Registration is not migration credit, execution evidence or remote admission.
The manifest and executor are frozen before fixture writes, and checked again
through each existing owned-fixture/operation authority check.
"""
from __future__ import annotations

import os

from .profile import fingerprint
from .run_state import RunStateError, read, write

SUITE = 'owned-local-trusted-team-v1'
CASE_ID = 'LOCAL-TRUST-API-CLI-01'
LEGACY_ID = 'story_user_builds_trusted_team'
PROGRAMMATIC_EXECUTION_CONTRACT = 'owned-local-trust-api-cli-v1'
ROLES = ('CEO', 'ENG', 'PM', 'QA', 'CS')
EFFECTS = {'mutate:owned_visibility', 'create:owned_friend_request', 'accept:owned_friend_request',
           'reject:owned_friend_request', 'restore:owned_visibility', 'retain:owned_friend_history', 'stop:owned_runtime'}
STEPS = ['public-auto-accept', 'protected-accept', 'protected-reject', 'friend-list',
         'cli-request-list-accept-list', 'restore-visibility']


def expected_requirements():
    requirements = {}
    pairs = [('public-accept','CEO','ENG','accepted'), ('protected-accept','QA','PM','accepted'),
             ('protected-reject','ENG','CS','rejected'), ('cli-accept','CEO','PM','accepted')]
    for name,sender,receiver,terminal in pairs:
        pair = sender+'-'+receiver+'-'
        ids = [pair+'created', pair+terminal+'-stored', pair+terminal+'-sent', pair+terminal+'-received',
               pair+'friends', receiver+'-'+sender+'-friends']
        if name != 'public-accept':
            ids += [pair+'pending-stored', pair+'pending-sent', pair+'pending-received', pair+'pending-cleared']
        if name != 'cli-accept':
            ids += [receiver+'-visibility-readback', receiver+'-target-visibility-stored-'+('public' if receiver=='ENG' else 'protected')]
        else:
            ids += ['cli-request-output', 'cli-received-exact-request', 'cli-accept-output', 'cli-ceo-list-includes-pm']
        requirements['FRIEND.'+name] = [CASE_ID+'.'+value for value in ids]
    requirements['FRIEND.list'] = [CASE_ID+'.CEO-ENG-friends']
    return requirements


def finalization_ids():
    return [CASE_ID+'.'+role+suffix for role in ROLES
            for suffix in ('-final-original-visibility', '-final-visibility-stored')] + [
                CASE_ID+'.complete-owned-request-inventory', CASE_ID+'.complete-owned-friend-inventory']


def validate_registration(manifest: dict) -> None:
    """No omission/remapping can convert a subset into the registered parent."""
    from .inventory import ROOT, validate_manifest
    from .migration_inventory import validate_contract
    try:
        if manifest['suite'] != SUITE or len(manifest['cases']) != 1:
            raise ValueError
        case = manifest['cases'][0]
        requirements = expected_requirements()
        expected_ids = sorted(set(sum(requirements.values(), []) + finalization_ids()))
        reference = validate_manifest(read(ROOT/'manifest.json'))
        contract = validate_contract(read(ROOT/'migration-contracts.json'), source_root=ROOT.parent/'e2e-test')
        parent = next(item for item in contract['stories'] if item['legacy_id']==LEGACY_ID)
        if (set(case) != {'id','version','title','legacy_id','roles','steps','effects','required_evidence',
                          'diagnostic_evidence','assertions','requirements','finalization_assertions',
                          'finalization','remote_validation','allowed_environments','registration_status'}
                or case['id'] != CASE_ID or type(case['version']) is not int or case['version'] != 1
                or case['legacy_id'] != LEGACY_ID or case['roles'] != list(ROLES) or case['steps'] != STEPS
                or case['requirements'] != requirements or case['assertions'] != expected_ids
                or case['finalization_assertions'] != finalization_ids()
                or set(case['effects']) != EFFECTS or len(case['effects']) != len(EFFECTS)
                or case['required_evidence'] != ['api','cli','sqlite'] or case['diagnostic_evidence'] != []
                or case['finalization'] != 'owned-local-retain-history'
                or case['allowed_environments'] != ['owned-local'] or case['remote_validation'] != 'NOT_RUN'
                or case['registration_status'] != 'LOCAL_EXECUTOR_ONLY'
                or fingerprint(manifest['legacy_inventory']) != fingerprint(reference['legacy_inventory'])
                or manifest['baseline_commit'] != reference['baseline_commit']
                or len(reference['legacy_inventory']) != 34
                or {r['id'] for r in parent['requirements']} != set(requirements)
                or parent['migration_status'] != 'NOT_MIGRATED'):
            raise ValueError
    except (KeyError, TypeError, ValueError, StopIteration):
        raise RunStateError('trusted-team registration differs from its complete reviewed local contract') from None


class TrustRegistration:
    def __init__(self, *, cli_sha256: str):
        from .inventory import ROOT, current_manifest, executor_digest
        if (not isinstance(cli_sha256, str) or len(cli_sha256) != 64
                or any(char not in '0123456789abcdef' for char in cli_sha256)):
            raise RunStateError('trusted-team registration requires an approved CLI digest')
        self.migration_digest = fingerprint(read(ROOT/'migration-contracts.json'))
        self.manifest = current_manifest(SUITE)
        self.manifest_digest = fingerprint(self.manifest)
        self.executor_digest = executor_digest()
        self.cli_sha256 = cli_sha256
        self.runtime = None
        self.receipt = None
        self.context = None
        self._context_attempted = False
        self._context_digest = ''

    def attach(self, runtime):
        from .inventory import ROOT, current_manifest, executor_digest, freeze_manifest
        if self.runtime is not None or runtime.state != 'RUNNING' or not runtime.ready:
            raise RunStateError('trusted-team registration requires one fresh live owned runtime')
        if (executor_digest() != self.executor_digest or fingerprint(current_manifest(SUITE)) != self.manifest_digest
                or fingerprint(read(ROOT/'migration-contracts.json')) != self.migration_digest):
            raise RunStateError('trusted-team executor or manifest changed before attachment')
        path = runtime.root/'trust-registration.json'
        if path.exists() or path.is_symlink() or (runtime.root/'local-fixtures.json').exists():
            raise RunStateError('registration must precede fixture creation and cannot be replayed')
        freeze_manifest(runtime.root, self.manifest)
        self.receipt = {'schema_version':1, 'contract':'owned-local-trust-registration-v1',
                        'run_id':runtime.run_id, 'runtime_dir':str(runtime.root), 'suite':SUITE,
                        'case_id':CASE_ID, 'case_version':1, 'manifest_digest':self.manifest_digest,
                        'executor_digest':self.executor_digest, 'migration_contract_digest':self.migration_digest, 'bcs_binary_sha256':runtime.binary_sha256,
                        'cli_binary_sha256':self.cli_sha256, 'legacy_id':LEGACY_ID,
                        'registration_status':'LOCAL_EXECUTOR_ONLY', 'ready_to_execute':False}
        fd = os.open(path, os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW, 0o600)
        os.close(fd)
        write(path, self.receipt)
        self.runtime = runtime
        self.validate(runtime)

    def _require_contract(self):
        if fingerprint(self.manifest) != self.manifest_digest:
            raise RunStateError('in-memory trusted-team qualification contract changed')

    def validate(self, runtime):
        self._require_contract()
        from .inventory import ROOT, current_manifest, executor_digest
        if (runtime is not self.runtime or self.receipt is None or runtime.run_id != self.receipt['run_id']
                or str(runtime.root) != self.receipt['runtime_dir']
                or runtime.binary_sha256 != self.receipt['bcs_binary_sha256']
                or self.cli_sha256 != self.receipt['cli_binary_sha256']
                or executor_digest() != self.executor_digest
                or fingerprint(read(ROOT/'migration-contracts.json')) != self.migration_digest
                or fingerprint(current_manifest(SUITE)) != self.manifest_digest
                or fingerprint(read(runtime.root/'trust-registration.json')) != fingerprint(self.receipt)
                or fingerprint(read(runtime.root/'manifest.json')) != self.manifest_digest):
            raise RunStateError('trusted-team Run registration, source or frozen manifest changed')
        if self.context is not None or self._context_digest:
            if self.context is None or self.context.context_digest != self._context_digest:
                raise RunStateError('trusted RunContext binding was removed or replaced')
            self.context.validate(runtime)

    def bind_context(self, identities):
        if self._context_attempted or self.runtime is None:
            raise RunStateError('trusted RunContext requires a fresh single binding attempt')
        self.validate(self.runtime)
        self._context_attempted = True
        from .trust_context import TrustRunContext
        self.context = TrustRunContext(self, identities)
        self._context_digest = self.context.context_digest
        self.validate(self.runtime)

    def binding(self):
        self._require_contract()
        return {key:self.receipt[key] for key in ('suite','case_id','case_version','manifest_digest','executor_digest','migration_contract_digest')}

    def requirement_results(self, assertions):
        self._require_contract()
        if (not isinstance(assertions, list) or len(assertions) > 1000
                or any(not isinstance(item, dict) or set(item) != {'id','status'}
                       or not isinstance(item['id'], str) or not item['id'].startswith(CASE_ID+'.')
                       or item['status'] not in {'PASS','FAIL','BLOCKED','SKIPPED','ERROR','CANCELLED'}
                       for item in assertions)
                or len({item['id'] for item in assertions}) != len(assertions)):
            raise RunStateError('trusted-team requirement projection needs unique valid assertion outcomes')
        states = {item['id']:item['status'] for item in assertions}
        return [{'id':name, 'status':'PASS' if all(states.get(key)=='PASS' for key in ids) else 'INCOMPLETE',
                 'assertion_ids':list(ids)} for name,ids in self.manifest['cases'][0]['requirements'].items()]
