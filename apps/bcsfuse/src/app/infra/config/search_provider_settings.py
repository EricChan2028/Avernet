"""Load legacy TC provider settings at construction, outside shared execution."""

import os


def qdrant_path(path):
    return path or os.getenv("QDRANT_LOCAL_PATH", "./qdrant_storage")


def embedding_options(
    max_retries, base_delay_ms, request_interval_ms=0, retry_budget_ms=2000
):
    default_backoff_seconds = base_delay_ms / 1000
    backoff_seconds = float(
        os.environ.get(
            "EMBEDDING_RETRY_BACKOFF_SECONDS", str(default_backoff_seconds)
        )
    )
    return dict(
        max_retries=int(os.environ.get("EMBEDDING_MAX_RETRIES", max_retries)),
        base_delay_ms=int(backoff_seconds * 1000),
        request_interval_ms=int(
            os.environ.get("EMBEDDING_REQUEST_INTERVAL_MS", request_interval_ms)
        ),
        retry_budget_ms=int(
            os.environ.get("EMBEDDING_RETRY_BUDGET_MS", retry_budget_ms)
        ),
    )


def reranker_options(model):
    return dict(
        base_url=os.getenv("RERANKER_BASE_URL", os.getenv("EMBEDDING_BASE_URL", "")),
        api_key=os.getenv("RERANKER_API_KEY", os.getenv("EMBEDDING_AUTH_TOKEN", "")),
        model=model
        if model is not None
        else os.getenv("RERANKER_MODEL", "Qwen3-Reranker-4B"),
        timeout=int(os.getenv("RERANKER_TIMEOUT", "30")),
    )

def reranker_circuit_options(
    *,
    failure_threshold: int = 3,
    recovery_seconds: float = 10.0,
) -> dict[str, int | float]:
    """Read process-local reranker breaker failure and recovery bounds."""
    return {
        "failure_threshold": int(
            os.environ.get(
                "RERANKER_CIRCUIT_FAILURE_THRESHOLD", str(failure_threshold)
            )
        ),
        "recovery_timeout_seconds": float(
            os.environ.get(
                "RERANKER_CIRCUIT_RECOVERY_SECONDS", str(recovery_seconds)
            )
        ),
    }
