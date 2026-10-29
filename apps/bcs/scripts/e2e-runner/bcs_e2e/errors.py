"""Preserve primary failures through secondary finalization errors."""
from __future__ import annotations


def primary_error(error: BaseException) -> BaseException:
    # Failure-marker persistence can raise while another error is unwinding.
    # Preserve that cause, but respect deliberate `raise ... from None`
    # sanitization boundaries used by the transports.
    seen = set()
    while (not error.__suppress_context__ and error.__context__ is not None
           and id(error) not in seen):
        seen.add(id(error))
        error = error.__context__
    return error



def has_cancellation(error: BaseException) -> bool:
    """Detect cancellation anywhere in the visible exception chain.

    A prior ordinary failure does not make a later KeyboardInterrupt safe to
    ignore. Respect explicit suppression, but follow an explicit cause; cycles
    must not hang the runner's finalization path.
    """
    seen = set()
    while error is not None and id(error) not in seen:
        seen.add(id(error))
        if isinstance(error, KeyboardInterrupt):
            return True
        error = (error.__cause__ if error.__cause__ is not None else
                 None if error.__suppress_context__ else error.__context__)
    return False
