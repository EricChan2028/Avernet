"""Explicit retained-observation schedule, separate from transport timeouts.

The v2 receipt opts into larger finite polling; old receipts never acquire more
queries or time by implication. This does not extend authority or qualify a Run.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

from .profile import ConfigError, object_keys

BUDGETED_WAIT_CONTRACT = 'bcs-session-deliveries-wait-v2'


@dataclass(frozen=True)
class ObservationBudget:
    timeout_seconds: int
    max_rounds: int
    poll_interval_seconds: int

    def public(self) -> dict:
        return asdict(self)


def reviewed_budget(contract: str, value: object) -> ObservationBudget | None:
    if contract != BUDGETED_WAIT_CONTRACT:
        if value is not None:
            raise ConfigError('independent observation budget requires the explicit wait-v2 contract')
        return None  # Legacy contracts deliberately inherit their existing limits.
    budget = object_keys(value, {'timeout_seconds', 'max_rounds', 'poll_interval_seconds'},
                         'execution observation budget')
    for field, minimum, maximum in (('timeout_seconds', 1, 300), ('max_rounds', 2, 6),
                                    ('poll_interval_seconds', 1, 30)):
        if type(budget[field]) is not int or not minimum <= budget[field] <= maximum:
            raise ConfigError('execution observation budget has an invalid bounded integer')
    return ObservationBudget(**budget)
