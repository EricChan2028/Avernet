"""Environment-neutral adapter interface for the slow-Judge case rewrite.

The interface intentionally mirrors the 22 original obligations rather than
the transport used by any one deployment. A concrete adapter must always
return both business data and an evidence record; neither side may silently
downgrade an error or replace an unavailable capability with success.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .case_rewrite import (
    CaseRewritePlan,
    fixed_loop_case_rewrite,
    slow_judge_case_rewrite,
)
from .edge_friend_mutation_case import CASE_IDS as EDGE_FRIEND_MUTATION_CASE_IDS
from .local_fixed_loop_contract import REQUIREMENTS as FIXED_LOOP_REQUIREMENTS
from .local_judge_contract import REQUIREMENTS as SLOW_JUDGE_REQUIREMENTS
from .run_state import RunStateError


ADAPTER_METHOD_NAMES = (
    'ensure_empty_provider_sandbox',
    'register_provider',
    'publish_provider_bot',
    'publish_provider_bot_visibility',
    'request_provider_bot_friendship',
    'provider_bot_friendship_list',
    'create_judged_group',
    'bind_explicit_run_receipt_boundary',
    'start_judged_run',
    'deliver_review_request',
    'await_review_response',
    'bind_publish_receipt_boundary',
    'submit_review_callback_before_deadline',
    'observe_judge_started',
    'await_judging_state',
    'read_judging_review_node',
    'release_owned_judge',
    'deliver_publish_request',
    'submit_publish_callback',
    'await_judged_run_completion',
    'delete_judged_group',
    'retire_provider_bot',
)


@dataclass(frozen=True)
class AdapterCall:
    """The common return shape for each environment-neutral case operation."""

    data: object
    evidence: dict

    def validate(self) -> None:
        if not isinstance(self.evidence, dict):
            raise RunStateError('case adapter evidence must be a dictionary')


class SlowJudgeCaseAdapter(Protocol):
    """The environment-neutral seam for all 22 slow-Judge obligations."""

    def ensure_empty_provider_sandbox(self) -> AdapterCall: ...
    def register_provider(self) -> AdapterCall: ...
    def publish_provider_bot(self) -> AdapterCall: ...
    def publish_provider_bot_visibility(self) -> AdapterCall: ...
    def request_provider_bot_friendship(self) -> AdapterCall: ...
    def provider_bot_friendship_list(self) -> AdapterCall: ...
    def create_judged_group(self) -> AdapterCall: ...
    def bind_explicit_run_receipt_boundary(self) -> AdapterCall: ...
    def start_judged_run(self) -> AdapterCall: ...
    def deliver_review_request(self) -> AdapterCall: ...
    def await_review_response(self) -> AdapterCall: ...
    def bind_publish_receipt_boundary(self) -> AdapterCall: ...
    def submit_review_callback_before_deadline(self) -> AdapterCall: ...
    def observe_judge_started(self) -> AdapterCall: ...
    def await_judging_state(self) -> AdapterCall: ...
    def read_judging_review_node(self) -> AdapterCall: ...
    def release_owned_judge(self) -> AdapterCall: ...
    def deliver_publish_request(self) -> AdapterCall: ...
    def submit_publish_callback(self) -> AdapterCall: ...
    def await_judged_run_completion(self) -> AdapterCall: ...
    def delete_judged_group(self) -> AdapterCall: ...
    def retire_provider_bot(self) -> AdapterCall: ...


FIXED_LOOP_ADAPTER_METHOD_NAMES = (
    'provision_per_run_sandbox',
    'establish_human_provider_fixture',
    'start_approved_loop_run',
    'start_exhausted_loop_run',
    'verify_one_shot_publication',
    'poll_loop_history_graph',
    'delete_loop_groups_exactly',
    'verify_shared_reset_replacement',
)


class FixedLoopCaseAdapter(Protocol):
    """The environment-neutral seam for all 8 fixed-Loop obligations."""

    def provision_per_run_sandbox(self) -> AdapterCall: ...
    def establish_human_provider_fixture(self) -> AdapterCall: ...
    def start_approved_loop_run(self) -> AdapterCall: ...
    def start_exhausted_loop_run(self) -> AdapterCall: ...
    def verify_one_shot_publication(self) -> AdapterCall: ...
    def poll_loop_history_graph(self) -> AdapterCall: ...
    def delete_loop_groups_exactly(self) -> AdapterCall: ...
    def verify_shared_reset_replacement(self) -> AdapterCall: ...


def _interface_names(plan: CaseRewritePlan) -> tuple[str, ...]:
    """Return the interface seam names bound to one case rewrite plan."""
    if plan.requirement_ids == tuple(SLOW_JUDGE_REQUIREMENTS):
        return ADAPTER_METHOD_NAMES
    if plan.requirement_ids == tuple(FIXED_LOOP_REQUIREMENTS):
        return FIXED_LOOP_ADAPTER_METHOD_NAMES
    if plan.case_id in EDGE_FRIEND_MUTATION_CASE_IDS:
        return tuple(plan.adapters[name] for name in plan.requirement_ids)
    raise RunStateError('case rewrite plan has no environment-neutral adapter interface')


def validate_adapter_contract(adapter: object,
                              plan: CaseRewritePlan | None = None) -> None:
    """Verify a concrete adapter implements every seam of its case plan."""
    active = slow_judge_case_rewrite() if plan is None else plan
    interface_names = _interface_names(active)
    expected = tuple(active.adapters[name] for name in active.requirement_ids)
    if expected != interface_names:
        raise RunStateError('case rewrite plan and adapter interface differ')
    missing = [name for name in interface_names
               if not callable(getattr(adapter, name, None))]
    if missing:
        raise RunStateError('case adapter is missing methods: ' + ', '.join(missing))
