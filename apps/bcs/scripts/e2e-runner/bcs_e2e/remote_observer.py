"""Explicit preprod MCP composition with a frozen, operator-reviewed mapping.

This is a review receipt, not cryptographic approval or live deployment proof.
The independent-review variant requires deployment and datasource evidence;
the owner-confirmed variant records a shared database without claiming
independent inspection or a deployed commit.
The runner validates the receipt's scope, integrity and validity interval; it
never infers that table presence or a profile boolean proves physical mapping.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import re
import time
from typing import Protocol

from .alisddbquery import McporterAlisdDbQueryClient
from .evidence import ASSERTIONS, EvidenceError, EvidencePlan, _make_plan
from .metadata_probe import _server_name
from .profile import ConfigError, fingerprint, identifier, object_keys, require_resolved
from .run_state import read, require_profile_binding

OWNER_MODE = 'OWNER_CONFIRMED_SHARED_DATABASE'
MAX_REVIEW_SECONDS = 3600


def validate_owner_confirmation(value: dict) -> None:
    confirmation = object_keys(value['owner_confirmation'], {
        'reference', 'sha256', 'database_sharing', 'mapping_confirmed',
        'tables_without_env_excluded', 'read_scope', 'allowed_assertions',
    }, 'owner confirmation')
    identifier(confirmation['reference'], 'owner confirmation reference')
    if (not isinstance(confirmation['sha256'], str)
            or re.fullmatch('[0-9a-f]{64}', confirmation['sha256']) is None):
        raise ConfigError('owner confirmation: invalid evidence digest')
    if (confirmation['database_sharing'] != 'preprod-and-production'
            or confirmation['mapping_confirmed'] is not True
            or confirmation['tables_without_env_excluded'] is not True
            or confirmation['read_scope'] != 'current-case-resource-id-and-env'):
        raise ConfigError('owner confirmation requires explicit shared-database mapping and exact scoped reads')
    assertions = confirmation['allowed_assertions']
    if (not isinstance(assertions, list) or not assertions
            or any(not isinstance(item, str) or item not in ASSERTIONS for item in assertions)
            or len(assertions) != len(set(assertions))):
        raise ConfigError('owner confirmation: only distinct fixed pilot evidence assertions are allowed')
    # Do not make up a deployed SHA from the checkout. This mode deliberately
    # does not establish deployment provenance, even if the caller knows a SHA.
    if value['deployment_commit'] != 'unknown':
        raise ConfigError('owner confirmation cannot assert an independently verified deployment commit')


def require_shared_query(value: dict, plan: EvidencePlan) -> None:
    if value['verification_mode'] != OWNER_MODE:
        return
    confirmation = value['owner_confirmation']
    if plan.assertion_id not in confirmation['allowed_assertions']:
        raise ConfigError('MCP assertion is outside the owner-confirmed read scope')
    key = ASSERTIONS[plan.assertion_id][2]
    if not isinstance(plan.filters, dict) or set(plan.filters) != {'env', key}:
        raise ConfigError('shared database reads require exact resource ID and environment')
    try:
        expected = _make_plan(plan.assertion_id, plan.filters[key], value['target']['database'],
                              value['target']['env'])
    except (EvidenceError, TypeError):
        raise ConfigError('invalid shared database resource scope') from None
    if plan != expected:
        raise ConfigError('shared database plan differs from the fixed two-row pilot projection')

FILENAME = 'observer-binding.json'


def _hex(value: object, length: int, field: str) -> None:
    if not isinstance(value, str) or re.fullmatch('[0-9a-f]{' + str(length) + '}', value) is None:
        raise ConfigError(f'observer binding: invalid {field}')


def binding_digest(value: dict) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def validate_binding(value: dict, profile: dict, *, execution: bool = True) -> dict:
    require_resolved(profile)
    if (profile['environment']['tier'] != 'preprod'
            or profile['entrypoint']['kind'] not in {'gateway', 'bcs-direct'}
            or profile['observer']['kind'] != 'alisddbquery'
            or profile['observer']['environment_binding_confirmed'] is not True):
        raise ConfigError('observer binding requires a confirmed preprod remote profile')
    if profile['policy']['max_response_bytes'] < 1024:
        raise ConfigError('reviewed MCP execution requires max_response_bytes of at least 1024; budget is never increased automatically')
    if (not isinstance(value, dict) or type(value.get('schema_version')) is not int
            or value['schema_version'] != 1):
        raise ConfigError('observer binding: schema version must be 1')
    if (not isinstance(value.get('verification_mode'), str)
            or value['verification_mode'] not in {'OPERATOR_ATTESTED', OWNER_MODE}):
        raise ConfigError('observer binding requires an explicit supported review mode')
    shared = value['verification_mode'] == OWNER_MODE
    source_field = 'owner_confirmation' if shared else 'source_reviews'
    object_keys(value, {'schema_version', 'verification_mode', 'receipt_id', 'reviewer',
                       'reviewed_at', 'expires_at', 'profile_digest', 'target',
                       'deployment_commit', source_field, 'query_authorized'}, 'observer binding')
    if value['query_authorized'] is not True:
        raise ConfigError('observer binding requires an explicit operator review and query authorization')
    identifier(value['receipt_id'], 'binding receipt ID')
    identifier(value['reviewer'], 'binding reviewer')
    if value['profile_digest'] != fingerprint(profile):
        raise ConfigError('observer binding does not match the complete profile')
    target = object_keys(value['target'], {'environment_id', 'api_origin', 'server', 'database', 'env'}, 'binding target')
    expected = {'environment_id': profile['environment']['id'], 'api_origin': profile['entrypoint']['origin'],
                'server': profile['observer']['server'], 'database': profile['observer']['database'],
                'env': profile['environment']['expected_bcs_env']}
    if target != expected:
        raise ConfigError('observer binding target differs from the configured API/MCP environment')
    _server_name(target['server'])
    if not shared:
        _hex(value['deployment_commit'], 40, 'operator-recorded deployment commit')
        reviews = object_keys(value['source_reviews'], {'deployment_config', 'mcp_datasource'}, 'binding source reviews')
        for source in reviews.values():
            object_keys(source, {'reference', 'sha256'}, 'binding source review')
            identifier(source['reference'], 'binding source reference')
            _hex(source['sha256'], 64, 'reviewed evidence SHA256')
        if reviews['deployment_config']['reference'] == reviews['mcp_datasource']['reference']:
            raise ConfigError('observer binding requires distinct deployment and datasource evidence references')
    else:
        validate_owner_confirmation(value)
    start, end = value['reviewed_at'], value['expires_at']
    if type(start) is not int or type(end) is not int or not 0 < start < end:
        raise ConfigError('observer binding: invalid review validity interval')
    if shared and end - start > MAX_REVIEW_SECONDS:
        raise ConfigError('owner-confirmed shared database review is limited to one hour')
    if execution and not start <= int(time.time()) < end:
        raise ConfigError('observer binding review is expired or not yet valid')
    # Return a detached value: mutable caller objects cannot widen a frozen review.
    return json.loads(json.dumps(value))


def binding_summary(value: dict) -> dict:
    """Public provenance; no reviewer ID, private refs, endpoint or source text."""
    shared = value['verification_mode'] == OWNER_MODE
    return {'verification_mode': value['verification_mode'], 'live_verification': 'NOT_PERFORMED',
            'receipt_digest': binding_digest(value), 'reviewed_at': value['reviewed_at'],
            'expires_at': value['expires_at'], 'declared_deployment_commit': value['deployment_commit'],
            'deployment_version_source': 'UNVERIFIED' if shared else 'OPERATOR_REVIEW_NOT_LIVE_PROBE',
            **({'review_basis': 'OWNER_CONFIRMED_SHARED_DATABASE',
                'independent_config_review': 'NOT_PERFORMED', 'shared_database': True,
                'read_scope': 'current-case-resource-id-and-env',
                'allowed_assertions': list(value['owner_confirmation']['allowed_assertions'])} if shared else {})}


def frozen_binding(state_dir: Path, state: dict, profile: dict, *, execution: bool = True) -> dict:
    require_profile_binding(state, profile)
    value = validate_binding(read(state_dir / FILENAME), profile, execution=execution)
    if state.get('observer_binding') != binding_summary(value):
        raise ConfigError('frozen observer binding differs from the prepared Run')
    return value


class ReviewedBinding:
    def __init__(self, profile: dict, state_dir: Path, run_id: str):
        self.profile = json.loads(json.dumps(profile))
        self.state_dir, self.run_id = state_dir, run_id
        state = read(state_dir / 'run.json')
        if state.get('run_id') != run_id:
            raise ConfigError('observer binding Run ID mismatch')
        self.summary = binding_summary(frozen_binding(state_dir, state, self.profile))

    def validate(self) -> dict:
        state = read(self.state_dir / 'run.json')
        if state.get('run_id') != self.run_id or state.get('observer_binding') != self.summary:
            raise ConfigError('observer binding Run changed during execution')
        return frozen_binding(self.state_dir, state, self.profile)

    def __call__(self, database: str, environment: str) -> dict:
        value = self.validate()
        return _bound_result(value, self.summary, database, environment)


def _bound_result(value: dict, summary: dict, database: str, environment: str) -> dict:
    if database != value['target']['database'] or environment != value['target']['env']:
        raise ConfigError('physical binding request differs from reviewed target')
    # BOUND is provider admission, not a live deployment-verification claim.
    return {'status': 'BOUND', 'database': database, 'environment': environment, **summary}


class MappingReview(Protocol):
    def validate(self) -> dict: ...


class InputReviewedBinding:
    """A pinned input receipt for read-only checks before any Run exists."""
    def __init__(self, profile: dict, path: Path):
        self.profile = json.loads(json.dumps(profile))
        self.path = path
        self.summary = binding_summary(validate_binding(read(path), self.profile))

    def validate(self) -> dict:
        value = validate_binding(read(self.path), self.profile)
        if binding_summary(value) != self.summary:
            raise ConfigError('observer input review changed during preflight')
        return value

    def __call__(self, database: str, environment: str) -> dict:
        return _bound_result(self.validate(), self.summary, database, environment)


class ReviewedMcpClient:
    """Recheck the frozen review before *every* metadata or business tool call."""
    def __init__(self, client, binding: MappingReview):
        from .mapping_confirmation import EnvironmentMapping
        if type(binding) is EnvironmentMapping and type(self) is ReviewedMcpClient:
            raise ConfigError('environment facts alone cannot authorize a generic MCP client')
        self._client, self._binding = client, binding
        self._delivery_evidence = {}
        self._downstream_reads = 0

    def db_list(self):
        before = self._binding.validate()
        result = self._client.db_list()
        if self._binding.validate() != before:
            raise ConfigError('MCP binding changed during database discovery')
        return result

    def _database(self, database):
        value = self._binding.validate()
        if database != value['target']['database']:
            raise ConfigError('MCP database differs from reviewed target')
        return value

    def table_list(self, database):
        before = self._database(database)
        result = self._client.table_list(database)
        if self._database(database) != before:
            raise ConfigError('MCP binding changed during table discovery')
        return result

    def table_describe(self, database, table):
        before = self._database(database)
        result = self._client.table_describe(database, table)
        if self._database(database) != before:
            raise ConfigError('MCP binding changed during schema discovery')
        return result

    def delivery_query(self, plan, *, review, scope, timeout_seconds: float, deadline_monotonic: float | None = None):
        from .delivery_observation import CONTRACTS, DeliveryReadScope, query_arguments
        from .observation_budget import BUDGETED_WAIT_CONTRACT
        query_arguments(plan)
        if type(scope) is not DeliveryReadScope:
            raise ConfigError('delivery query needs an explicit ledger and held-lease scope')
        permission = review.validate()
        scope.validate(plan, review)
        value = self._database(plan.database)
        if (permission.get('execution_observation') not in CONTRACTS
                or permission.get('profile_digest') != value['profile_digest']
                or plan.environment != value['target']['env']):
            raise ConfigError('delivery observation requires separate explicit scoped read authorization')
        self._delivery_evidence.pop(plan, None)
        if permission.get('execution_observation') == BUDGETED_WAIT_CONTRACT and deadline_monotonic is None:
            raise ConfigError('wait-v2 delivery query requires the shared absolute deadline')
        if deadline_monotonic is not None:
            if (type(deadline_monotonic) not in (int, float) or not math.isfinite(deadline_monotonic)
                    or type(timeout_seconds) not in (int, float) or not math.isfinite(timeout_seconds)
                    or timeout_seconds <= 0):
                raise ConfigError('delivery observation requires a finite deadline and positive remaining budget')
            timeout_seconds = min(timeout_seconds, deadline_monotonic - time.monotonic())
            if timeout_seconds <= 0:
                raise TimeoutError('delivery deadline expired during authorization validation')
        result = self._client.delivery_query(plan, timeout_seconds=timeout_seconds)
        scope.validate(plan, review)
        # A query whose review expired/changed in flight cannot be credited.
        if review.validate() != permission or self._database(plan.database) != value:
            raise ConfigError('delivery observation authorization changed during query')
        if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
            raise TimeoutError('late delivery evidence is not accepted')
        from .delivery_observation import project_rows
        # Keep only the latest authorized projected delivery snapshot. A stale
        # caller cannot invent an alias or reuse one no longer observed.
        rows = project_rows(plan, result, frozenset(plan.target_bot_ids))
        self._delivery_evidence[plan] = (scope, rows)
        return result

    def delivery_scan(self, plan, *, review, scope, deadline_monotonic: float, before_page, progress: dict):
        from .delivery_observation import DeliveryReadScope
        from .delivery_pagination import collect, validate_limits
        from .recheck_review import RetainedRecheckReview
        if type(review) is not RetainedRecheckReview or type(scope) is not DeliveryReadScope:
            raise ConfigError('delivery pagination requires an exact retained recheck and owned scope')
        permission = review.validate()
        limits = validate_limits(permission.get('delivery_pagination'))
        binding = self._database(plan.database)
        if (permission.get('profile_digest') != binding['profile_digest']
                or plan.environment != binding['target']['env']):
            raise ConfigError('delivery pagination differs from the reviewed environment')
        if type(deadline_monotonic) not in (int, float) or not math.isfinite(deadline_monotonic):
            raise ConfigError('delivery pagination needs the original finite absolute deadline')
        self._delivery_evidence.pop(plan, None)

        def validate():
            scope.validate(plan, review)
            if review.validate() != permission or self._database(plan.database) != binding:
                raise ConfigError('delivery pagination authority changed during traversal')
            if time.monotonic() >= deadline_monotonic:
                raise TimeoutError('delivery pagination deadline expired')

        def fetch(after_id, limit):
            validate()
            before_page()  # Shared phase allowance: one debit per actual page attempt.
            validate()
            progress['queries'] += 1
            raw = self._client.delivery_page_query(plan, after_id=after_id, limit=limit,
                timeout_seconds=deadline_monotonic - time.monotonic())
            validate()
            return raw

        try:
            rows = collect(plan, limits, fetch, deadline=deadline_monotonic, progress=progress)
            validate()
        except BaseException:
            if progress.get('status') == 'COMPLETE':
                progress.update(status='INCOMPLETE', reason='POST_SCAN_VALIDATION_FAILED')
            raise
        self._delivery_evidence[plan] = (scope, rows)
        return rows

    def scope_query(self, plan, *, group: bool, review, scope, deadline_monotonic: float):
        from .scope_observation import CONTRACTS, query_arguments, project_row
        from .recheck_review import RetainedRecheckReview
        from .delivery_observation import DeliveryReadScope
        query_arguments(plan, group=group)
        if type(review) is not RetainedRecheckReview or type(scope) is not DeliveryReadScope:
            raise ConfigError('scope facts require a dedicated quarantined-Run read review and owned scope')
        permission = review.validate()
        binding = self._database(plan.database)
        if (permission.get('scope_observation') not in CONTRACTS
                or permission.get('profile_digest') != binding['profile_digest']
                or plan.environment != binding['target']['env']):
            raise ConfigError('scope facts are outside the explicit review/database environment')
        if type(deadline_monotonic) not in (int, float) or not math.isfinite(deadline_monotonic):
            raise ConfigError('scope facts need the shared absolute observation deadline')
        scope.validate(plan, review)
        remaining = deadline_monotonic - time.monotonic()
        if remaining <= 0:
            raise TimeoutError('scope observation deadline expired')
        raw = self._client.scope_query(plan, group=group, timeout_seconds=remaining, contract=permission['scope_observation'])
        scope.validate(plan, review)
        if review.validate() != permission or self._database(plan.database) != binding:
            raise ConfigError('scope facts authority changed during the query')
        if time.monotonic() >= deadline_monotonic:
            raise TimeoutError('late scope facts are not accepted')
        return project_row(plan, raw, group=group, profile=review.profile, contract=permission['scope_observation'])

    def producer_query(self, plan, *, review, scope, deadline_monotonic: float):
        from .producer_inventory import CONTRACT, query_arguments, project_rows
        from .recheck_review import RetainedRecheckReview
        from .delivery_observation import DeliveryReadScope
        query_arguments(plan)
        if type(review) is not RetainedRecheckReview or type(scope) is not DeliveryReadScope:
            raise ConfigError('producer inventory requires a quarantined-Run review and owned scope')
        permission = review.validate()
        binding = self._database(plan.database)
        if (permission.get('scope_observation') != CONTRACT
                or permission.get('profile_digest') != binding['profile_digest']
                or plan.environment != binding['target']['env']):
            raise ConfigError('producer inventory is outside the explicit review/database environment')
        if type(deadline_monotonic) not in (int, float) or not math.isfinite(deadline_monotonic):
            raise ConfigError('producer inventory needs the shared absolute observation deadline')
        scope.validate(plan, review)
        remaining = deadline_monotonic - time.monotonic()
        if remaining <= 0:
            raise TimeoutError('producer observation deadline expired')
        raw = self._client.producer_query(plan, timeout_seconds=remaining)
        scope.validate(plan, review)
        if review.validate() != permission or self._database(plan.database) != binding:
            raise ConfigError('producer authority changed during the query')
        if time.monotonic() >= deadline_monotonic:
            raise TimeoutError('late producer inventory is not accepted')
        return project_rows(plan, raw)

    def downstream_observe(self, plan, *, review, scope, deadline_monotonic: float):
        from .delivery_observation import DeliveryReadScope
        from .downstream_observation import CONTRACT, MAX_RUNS, query_arguments, project_row, correlate
        query_arguments(plan)
        if (type(deadline_monotonic) not in (int, float) or not math.isfinite(deadline_monotonic) or deadline_monotonic <= 0):
            raise ConfigError('downstream observation requires a positive finite absolute deadline')
        if type(scope) is not DeliveryReadScope:
            raise ConfigError('downstream observation requires an active owned delivery scope')
        permission = review.validate()
        downstream = permission.get('downstream_observation', {})
        if (downstream.get('contract') != CONTRACT
                or downstream.get('bot_bindings', {}).get(plan.target_bot_id) != plan.baas_bot_id):
            raise ConfigError('downstream observation lacks its explicit runtime mapping/read authorization')
        binding = self._database(plan.parent.database)
        if permission.get('profile_digest') != binding['profile_digest'] or plan.parent.environment != binding['target']['env']:
            raise ConfigError('downstream mapping differs from the reviewed environment')
        run_key = 'downstream_run_id' if downstream.get('run_id_source') == 'accepted-alias' else 'run_id'
        source = self._delivery_evidence.get(plan.parent)
        if source is None or source[0] is not scope or not any(
                r['delivery_id'] == plan.delivery_id and r['target_bot_id'] == plan.target_bot_id
                and r['kind'] == 'send' and r[run_key] == plan.downstream_run_id for r in source[1]):
            raise ConfigError('downstream run ID was not observed in this authorized delivery scope')
        if self._downstream_reads >= MAX_RUNS:
            raise ConfigError('downstream run observation budget exhausted')
        scope.validate(plan.parent, review)
        self._downstream_reads += 1

        def check():
            scope.validate(plan.parent, review)
            if review.validate() != permission or self._database(plan.parent.database) != binding:
                raise ConfigError('downstream authorization changed during observation')
            remaining = deadline_monotonic - time.monotonic()
            if remaining <= 0:
                raise TimeoutError('downstream observation deadline expired')
            return remaining

        raw = self._client.downstream_query(plan, result=False, timeout_seconds=check())
        check()
        queue = project_row(plan, raw)
        result = None
        try:
            if queue is not None:
                # This table has no env: only the just-verified exact queue binding
                # permits following the already-observed downstream Run identity.
                raw = self._client.downstream_query(plan, result=True, timeout_seconds=check())
                check()
                result = project_row(plan, raw, result=True)
        except (Exception, KeyboardInterrupt) as error:
            error.downstream_observation = correlate(plan, queue, None)
            raise

        return correlate(plan, queue, result)

    def db_query(self, plan: EvidencePlan):
        if not isinstance(plan, EvidencePlan):
            raise ConfigError('MCP query requires a semantic evidence plan')
        value = self._database(plan.database)
        require_shared_query(value, plan)
        if plan.filters.get('env') != value['target']['env']:
            raise ConfigError('MCP query environment differs from reviewed target')
        result = self._client.db_query(plan)
        if self._database(plan.database) != value:
            raise ConfigError('MCP binding changed during resource observation')
        return result


def remote_dependencies(profile: dict, state_dir: Path, run_id: str, *,
                        executable: Path, allow_read: bool) -> tuple[ReviewedMcpClient, ReviewedBinding]:
    if allow_read is not True:
        raise ConfigError('remote run requires --allow-observer-read for MCP metadata and scoped rows')
    binding = ReviewedBinding(profile, state_dir, run_id)
    return _reviewed_client(profile, executable, binding), binding


def remote_check_dependencies(profile: dict, receipt: Path, *, executable: Path,
                              allow_read: bool) -> tuple[ReviewedMcpClient, InputReviewedBinding]:
    if allow_read is not True:
        raise ConfigError("remote check requires --allow-observer-read for MCP metadata")
    binding = InputReviewedBinding(profile, receipt)
    return _reviewed_client(profile, executable, binding), binding


def _reviewed_client(profile: dict, executable: Path, binding: MappingReview) -> ReviewedMcpClient:
    from .mapping_confirmation import EnvironmentMapping
    if type(binding) is EnvironmentMapping:
        raise ConfigError('persistent mapping requires the separate source-bound recheck composition')
    return ReviewedMcpClient(_mcp_client(profile, executable), binding)


def _mcp_client(profile: dict, executable: Path) -> McporterAlisdDbQueryClient:
    try:
        if not executable.is_absolute():
            raise ConfigError('mcporter executable must be an explicit absolute path')
        executable = executable.resolve(strict=True)
        if not executable.is_file() or not os.access(executable, os.X_OK):
            raise ConfigError('mcporter executable is not an executable file')
    except OSError:
        raise ConfigError('mcporter executable is unavailable') from None
    return McporterAlisdDbQueryClient(server=profile['observer']['server'], executable=(str(executable),),
                                      timeout_seconds=profile['policy']['timeout_seconds'],
                                      max_output_bytes=profile['policy']['max_response_bytes'])
