# V1 search offload: bounded module size exception

Status: proposed bounded exception for review.
Owner: BCSFuse high-availability maintainers.
Review date: 2026-11-04.

The repository limits modified source files to 1,000 physical lines. This change
modifies only an existing oversized file, `src/app/bootstrap/oss_business_routes.py`
(1,553 lines before the change), to wrap the two blocking V1 search calls with
`run_in_threadpool`. No new route, dependency, stateful object, or public API is
introduced there.

The alternative was a broad route refactor during the HA hardening window. That
would combine concurrency behavior changes with legacy routing extraction and
increase regression risk without improving this bounded behavior contract. The
focused contract tests in `tests/contract/test_v1_request_offload.py` prove that
a blocking search and recommendation core call cannot hold the entire event loop.

Removal plan: extract OSS search into a dedicated transport module in a separate
cleanup change and remove this exception when that module is below 1,000 lines.
