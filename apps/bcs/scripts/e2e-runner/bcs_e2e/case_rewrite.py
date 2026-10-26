"""Environment-neutral case rewrite contracts for the reviewed case parents.

This module is the first explicit case layer for the 22 reviewed slow-Judge
obligations and the 8 reviewed fixed-Loop obligations. It does **not**
execute a story, start a runtime, mutate a fixture, or replace the existing
owned-local runner. Its purpose is to translate each frozen source contract
into reusable method signatures that three future adapters can implement
without changing the business assertion set.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from .local_fixed_loop_contract import (
    CASE_ID as FIXED_LOOP_CASE_ID,
    PARENT as FIXED_LOOP_PARENT,
    REQUIREMENTS as FIXED_LOOP_REQUIREMENTS,
    fixed_loop_source_binding,
)
from .local_judge_contract import CASE_ID, PARENT, REQUIREMENTS, judge_source_binding
from .run_state import RunStateError


@dataclass(frozen=True)
class CaseRewriteEnvironment:
    """One realization contract for the same case obligation set."""

    name: str
    adapter_family: str
    status: str
    blocker: str

    def validate(self) -> None:
        if (self.name not in {'owned-local', 'singlebox', 'preprod'}
                or self.adapter_family not in {'OwnedLocalCaseAdapter', 'SingleboxCaseAdapter', 'RemoteCaseAdapter'}
                or self.status not in {'LOCAL_FULL_EXECUTOR', 'PENDING'}
                or not isinstance(self.blocker, str) or not self.blocker):
            raise RunStateError('Case rewrite environment contract is invalid')


@dataclass(frozen=True)
class CaseRewriteBinding:
    """The case identity and obligation IDs one plan is bound to."""

    case_id: str
    parent_id: str
    requirement_ids: tuple[str, ...]

    def validate(self) -> None:
        if (not isinstance(self.case_id, str) or not self.case_id
                or not isinstance(self.parent_id, str) or not self.parent_id
                or not isinstance(self.requirement_ids, tuple)
                or not all(isinstance(item, str) and item
                           for item in self.requirement_ids)
                or len(set(self.requirement_ids)) != len(self.requirement_ids)):
            raise RunStateError('Case rewrite binding is invalid')


@dataclass(frozen=True)
class CaseRewriteRequirement:
    """A single original obligation and its environment-neutral adapter seam."""

    requirement_id: str
    kind: str
    method: str
    path: str
    actor: str
    adapter_method: str
    expectation: str

    def validate(self, binding: CaseRewriteBinding) -> None:
        if (self.requirement_id not in binding.requirement_ids
                or self.kind not in {'fixture', 'http', 'delivery', 'poll', 'observation'}
                or not isinstance(self.method, str)
                or not isinstance(self.path, str)
                or not isinstance(self.actor, str)
                or not isinstance(self.adapter_method, str)
                or not isinstance(self.expectation, str)
                or not all([self.method, self.path, self.actor, self.adapter_method, self.expectation])):
            raise RunStateError(f'Case rewrite requirement {self.requirement_id!r} is invalid')


@dataclass(frozen=True)
class CaseRewritePlan:
    """A frozen case plus adapter seams for every original obligation."""

    case_id: str
    parent_id: str
    requirements: tuple[CaseRewriteRequirement, ...]
    environments: tuple[CaseRewriteEnvironment, ...]
    binding: CaseRewriteBinding

    @property
    def requirement_ids(self) -> tuple[str, ...]:
        return tuple(row.requirement_id for row in self.requirements)

    @property
    def adapters(self) -> Mapping[str, str]:
        return {row.requirement_id: row.adapter_method for row in self.requirements}

    def validate(self) -> None:
        self.binding.validate()
        if (self.case_id != self.binding.case_id
                or self.parent_id != self.binding.parent_id
                or self.requirement_ids != self.binding.requirement_ids
                or set(self.adapters) != set(self.binding.requirement_ids)
                or len(set(self.adapters.values())) != len(self.adapters)
                or [environment.name for environment in self.environments]
                   != ['owned-local', 'singlebox', 'preprod']):
            raise RunStateError('Case rewrite plan is incomplete or not case-bound')
        for requirement in self.requirements:
            requirement.validate(self.binding)
        for environment in self.environments:
            environment.validate()


_ADAPTER_METHODS = {
    REQUIREMENTS[0]: 'ensure_empty_provider_sandbox',
    REQUIREMENTS[1]: 'register_provider',
    REQUIREMENTS[2]: 'publish_provider_bot',
    REQUIREMENTS[3]: 'publish_provider_bot_visibility',
    REQUIREMENTS[4]: 'request_provider_bot_friendship',
    REQUIREMENTS[5]: 'provider_bot_friendship_list',
    REQUIREMENTS[6]: 'create_judged_group',
    REQUIREMENTS[7]: 'bind_explicit_run_receipt_boundary',
    REQUIREMENTS[8]: 'start_judged_run',
    REQUIREMENTS[9]: 'deliver_review_request',
    REQUIREMENTS[10]: 'await_review_response',
    REQUIREMENTS[11]: 'bind_publish_receipt_boundary',
    REQUIREMENTS[12]: 'submit_review_callback_before_deadline',
    REQUIREMENTS[13]: 'observe_judge_started',
    REQUIREMENTS[14]: 'await_judging_state',
    REQUIREMENTS[15]: 'read_judging_review_node',
    REQUIREMENTS[16]: 'release_owned_judge',
    REQUIREMENTS[17]: 'deliver_publish_request',
    REQUIREMENTS[18]: 'submit_publish_callback',
    REQUIREMENTS[19]: 'await_judged_run_completion',
    REQUIREMENTS[20]: 'delete_judged_group',
    REQUIREMENTS[21]: 'retire_provider_bot',
}

_ENVIRONMENTS = (
    CaseRewriteEnvironment(
        name='owned-local',
        adapter_family='OwnedLocalCaseAdapter',
        status='LOCAL_FULL_EXECUTOR',
        blocker='Owned-local case execution is complete; singlebox and preprod adapters remain pending.',
    ),
    CaseRewriteEnvironment(
        name='singlebox',
        adapter_family='SingleboxCaseAdapter',
        status='PENDING',
        blocker='Singlebox lifecycle and run-owned process ownership are still blocked.',
    ),
    CaseRewriteEnvironment(
        name='preprod',
        adapter_family='RemoteCaseAdapter',
        status='PENDING',
        blocker='Preprod route, callback, and effect admission remain independent gates.',
    ),
)


_FIXED_LOOP_ADAPTER_METHODS = {
    FIXED_LOOP_REQUIREMENTS[0]: 'provision_per_run_sandbox',
    FIXED_LOOP_REQUIREMENTS[1]: 'establish_human_provider_fixture',
    FIXED_LOOP_REQUIREMENTS[2]: 'start_approved_loop_run',
    FIXED_LOOP_REQUIREMENTS[3]: 'start_exhausted_loop_run',
    FIXED_LOOP_REQUIREMENTS[4]: 'verify_one_shot_publication',
    FIXED_LOOP_REQUIREMENTS[5]: 'poll_loop_history_graph',
    FIXED_LOOP_REQUIREMENTS[6]: 'delete_loop_groups_exactly',
    FIXED_LOOP_REQUIREMENTS[7]: 'verify_shared_reset_replacement',
}


def fixed_loop_case_rewrite() -> CaseRewritePlan:
    """Build the declarative rewrite contract for the fixed-Loop parent."""
    source = fixed_loop_source_binding()
    rows = [
        CaseRewriteRequirement(
            requirement_id=row['id'],
            kind=row['kind'],
            method=row['method'],
            path=row['path'],
            actor=row['actor'],
            adapter_method=_FIXED_LOOP_ADAPTER_METHODS[row['id']],
            expectation=row['expectation'],
        )
        for row in source['requirements']
    ]
    plan = CaseRewritePlan(
        case_id=FIXED_LOOP_CASE_ID,
        parent_id=FIXED_LOOP_PARENT,
        requirements=tuple(rows),
        environments=_ENVIRONMENTS,
        binding=CaseRewriteBinding(
            case_id=FIXED_LOOP_CASE_ID,
            parent_id=FIXED_LOOP_PARENT,
            requirement_ids=tuple(FIXED_LOOP_REQUIREMENTS),
        ),
    )
    plan.validate()
    if (source['parent'] != plan.parent_id
            or plan.requirement_ids != FIXED_LOOP_REQUIREMENTS
            or set(plan.adapters) != set(FIXED_LOOP_REQUIREMENTS)):
        raise RunStateError('fixed-Loop source binding and case rewrite plan differ')
    return plan


def slow_judge_case_rewrite() -> CaseRewritePlan:
    """Build a declarative rewrite contract from the reviewed source binding."""
    binding = judge_source_binding()
    rows = [
        CaseRewriteRequirement(
            requirement_id=row['id'],
            kind=row['kind'],
            method=row['method'],
            path=row['path'],
            actor=row['actor'],
            adapter_method=_ADAPTER_METHODS[row['id']],
            expectation=row['expectation'],
        )
        for row in binding['requirements']
    ]
    plan = CaseRewritePlan(
        case_id=CASE_ID,
        parent_id=PARENT,
        requirements=tuple(rows),
        environments=_ENVIRONMENTS,
        binding=CaseRewriteBinding(
            case_id=CASE_ID,
            parent_id=PARENT,
            requirement_ids=tuple(REQUIREMENTS),
        ),
    )
    plan.validate()
    if (binding['parent'] != plan.parent_id
            or plan.requirement_ids != REQUIREMENTS
            or set(plan.adapters) != set(REQUIREMENTS)):
        raise RunStateError('Slow Judge source binding and case rewrite plan differ')
    return plan
