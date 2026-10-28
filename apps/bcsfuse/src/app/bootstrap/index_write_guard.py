"""Shared route guard for owner-fenced index writes."""

from fastapi import HTTPException, Request, status


def ensure_writable_index(request: Request) -> None:
    """Reject updates before durable providers when this process is a reader."""
    vector_store = request.app.state.context.registry.get("vector_store")
    if vector_store is None:
        return
    if getattr(vector_store, "index_state_unavailable", False):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "INDEX_STATE_UNAVAILABLE",
                "message": (
                    "Shared index state is unavailable; updates failed closed "
                    "without a durable lease"
                ),
            },
        )
    if getattr(vector_store, "read_only", False):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "INDEX_READ_ONLY",
                "message": "This replica is read-only; send updates to the index owner",
            },
        )


__all__ = ["ensure_writable_index"]
