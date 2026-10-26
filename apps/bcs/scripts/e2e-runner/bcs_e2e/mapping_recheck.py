"""Attach persistent environment facts only to a single owned-source read plan."""
from __future__ import annotations

from pathlib import Path

from .mapping_confirmation import EnvironmentMapping
from .profile import ConfigError, fingerprint
from .recheck_review import PLAN_CONTRACT, RetainedRecheckReview
from .remote_observer import ReviewedMcpClient, _mcp_client


class ScopedMappingClient(ReviewedMcpClient):
    def __init__(self, client, binding: EnvironmentMapping, review: RetainedRecheckReview):
        if type(binding) is not EnvironmentMapping or type(review) is not RetainedRecheckReview:
            raise ConfigError('stable mapping requires the exact owned-source recheck composition')
        self._read_plan = review
        super().__init__(client, binding)
        self._check_plan()

    def _check_plan(self) -> tuple[dict, dict]:
        value = self._binding.validate()
        plan = self._read_plan.validate()
        if (plan['contract'] != PLAN_CONTRACT or self._binding.profile != self._read_plan.profile
                or plan['mapping_confirmation_digest'] != fingerprint(value)):
            raise ConfigError('mapping facts do not match this pinned source-bound read plan')
        return value, plan

    def _database(self, database):
        value, _ = self._check_plan()
        if database != value['target']['database']:
            raise ConfigError('recheck database differs from the confirmed environment')
        # Runtime context only. The stored mapping intentionally does not bind
        # actors/credentials; the independent plan pins the complete Run Profile.
        return {**value, 'profile_digest': fingerprint(self._read_plan.profile)}

    def db_list(self):
        raise ConfigError('environment confirmation alone does not permit catalogue reads')

    def table_list(self, database):
        raise ConfigError('read plan does not permit broad table discovery')

    def db_query(self, plan):
        raise ConfigError('stable mapping cannot authorize generic resource or arbitrary SQL queries')

    def table_describe(self, database, table):
        from .delivery_observation import TABLE
        from .scope_observation import GROUP_TABLE, SESSION_TABLE
        from .producer_inventory import CONTRACT as PRODUCERS, TABLE as PRODUCER_TABLE
        from .downstream_observation import QUEUE_TABLE, RESULT_TABLE
        _, plan = self._check_plan()
        allowed = {TABLE}
        if 'scope_observation' in plan:
            allowed.update((GROUP_TABLE, SESSION_TABLE))
            if plan['scope_observation'] == PRODUCERS:
                allowed.add(PRODUCER_TABLE)
        if 'downstream_observation' in plan:
            allowed.update((QUEUE_TABLE, RESULT_TABLE))
        if table not in allowed:
            raise ConfigError('schema read is outside the selected recheck projections')
        result = super().table_describe(database, table)
        self._check_plan()
        return result


def mapping_dependencies(profile: dict, path: Path, review: RetainedRecheckReview, *,
                         executable: Path, allow_read: bool):
    if allow_read is not True:
        raise ConfigError('mapped recheck requires explicit --allow-observer-read')
    binding = EnvironmentMapping(profile, path)
    # Reuse existing executable validation and bounded MCP process construction;
    # no tool is called until the specialized read-only client is returned.
    return ScopedMappingClient(_mcp_client(profile, executable), binding, review), binding
