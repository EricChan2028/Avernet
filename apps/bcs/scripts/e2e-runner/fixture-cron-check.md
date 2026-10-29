# Read-only fixture cron configuration evidence

`fixture-cron-check` is a bounded diagnostic for the existing two dedicated
preproduction Bots. It does not create, enable, run, cancel or delete a routine,
repair a fixture, acquire/release a pool, or establish whole-scope completion.

## Why not use an empty routines page as proof?

Reviewed public source `ee9f4f8af4dbe4b768469df9043215093a74da58`:

- `apps/backend/src/agentclaw/community/adapters/http/openapi_v1/routines/router.py`
  (`list_routines`) requests only draft runtimes and maps items into a Page. It
  does not carry the relay's failed_targets into that Page.
- The owner-level routines route aggregates the owner's fleet. It is not an
  appropriate way to inspect only two expressly bound fixtures.
- `community/adapters/http/cron/router.py` preserves failed_targets on its
  exact-Bot legacy list. `core/cron/services/cron_relay.py` lists configured
  runtime targets and can return success=true alongside failed targets.
- An unsupported engine can produce no targets. Even the legacy empty result
  therefore cannot prove a complete runtime inventory or absence of producers.

The frontend's `legacyCronController.ts` calls the relative `/api/cron` path with
an exact bot_id and `target=legacy-agentclaw`. `httpClient.ts` keeps that URL
relative; the target selects a dev-server/Tern proxy, not a browser-side alternate
hostname. Enterprise source `b4961722767e4778bb7e3d7df579ee1023a62107`,
`apps/frontend-nextgen/config/internal/runtime/config.ts`, declares the matching
proxy. Source configuration alone does not establish that the live page has that
proxy deployed. Never fall back to a different host after a rejected/non-API page.

## Identity and invocation

```bash
python3 runner.py fixture-cron-check \
  --profile /private/path/preprod-profile.json \
  --allow-read --confirm-human-id BOUND_HUMAN_ID \
  --confirm-frontend-origin "$REVIEWED_FRONTEND_ORIGIN"
```

The profile must be preprod/gateway, contain only its existing Cookie reference,
include an explicit browser_origin, and bind exactly driver/member to distinct
`backend-Bot:owner` identities whose owner matches the Human. This identity form
is supported by the reviewed Backend `bcs_bot_identity_resolver.py:28-54` and
`bot_service.py:3533-3549`; arbitrary Provider IDs and the reserved owner-wide `all` selector are rejected
before credential resolution rather than guessed or expanded.
Provider registration responses are not assumed to universally follow this form.

The separately confirmed frontend origin must equal the validated browser_origin
with its trailing slash removed (the canonical origin used by the transport).
Using that field as an Origin header in older commands does not by itself
authorize forwarding a Cookie to it as a new request destination. The explicit
confirmation is checked before resolving credentials or starting any read.

Before any cron read, reuse the existing gateway environment/Human/Bot preflight.
A failure stops the sequence. At most five preflight GETs and two exact fixture
GETs are made. The cron query carries only that Bot and owner, never bot_id=all.
Each request uses the configured finite timeout and response-byte budget. Requests
are GET with no body, use the authenticated opener with redirects disabled, and
never load a new identity or choose a fallback host/path. Missing permission,
wrong Human or unsupported identity mapping fails before credential resolution.

Only a JSON CronListApiResponse with explicit success, integer error_code, data
list and failed_targets list is accepted. HTML 200 is an unsupported response,
not an empty inventory or automatic evidence of an expired Cookie. Parser, size,
transport, top-level auth and HTTP failures stop subsequent target reads; no retry occurs.
Any target business failure remains BLOCKED regardless of whether it is the
first or second target. Cancellation returns 130 and retains the safe partial
call record in the output.

The cron projection preserves only job ID, enabled state and runtime stage.
The gateway preflight is separately projected onto fixed check/operation labels,
HTTP status, timing and request-ID digests; upstream response request IDs and
free-form text are never copied into this diagnostic, including cancellation. Commands,
prompts, names, arbitrary errors and credentials are omitted. Foreign Bot/owner
rows, conflicting job identities and invalid types reject the response; bounded
partial failures and duplicates remain INCOMPLETE. Maximum input is 100 jobs and
eight failed targets per fixture, without trimming or deduplication to make it fit.
Runtime stage draft/verify/online is not a deployment environment label. An absent
personal-Bot stage is preserved as absent, not invented from the selected server.

OBSERVED / exit 0 means both query projections were accepted. It does **not** prove
runtime inventory completeness, absence of running cron executions, external
callbacks or background work. Those are explicitly NOT_VERIFIED/NOT_QUERIED;
input_boundary stays NOT_PROVEN and pool_release_authorized/ready_to_execute are
false. Empty and disabled-only results retain the same limits. This is not a
replacement for the input-boundary evidence required by retained-closure-matrix.md.

## Side-effect and version boundary

The reviewed Backend path checks active device bindings and invokes the adapter
with GET. The reviewed builders/transport do not explicitly create/activate a Bot
or create/run/enable a job; Teclaw's list mapping also remains GET. Connection
resolution may call SDK/connection-info services and generate connection tokens.
This does not certify all downstream SDK/server/engine behavior as side-effect
free. The report states zero **runner business writes**, not an end-to-end proof
that no cache, lease, audit or lazy initialization changed. No speculative write
is used to investigate a missing read capability.

The enterprise gitlink does not necessarily identify the public checkout stamped
into the runtime build. The specified source pair is the earlier health-reported
pair, not a freshly validated frontend, backend and engine deployment combination.
The backend environment is labelled CONFIGURED_FRONTEND_ROUTE_ONLY rather than
independently probed. No native cookie, system credential or direct-engine route is
substituted to make the query pass.

## Actual read attempt (2026-10-12)

The existing Cookie passed two actual gateway preflight invocations; each made
five GETs with HTTP 200 and matched environment, Human and both owned online Bots.
The subsequent first exact frontend cron GET returned HTTP 200 but could not be
parsed as the reviewed API response. The collector returned BLOCKED, credited no
cron rows, did not query the second Bot and did not select another route.

One separately bounded read of that same target classified its wire shape:
text/html, 16,155 bytes, an HTML document rather than JSON. Only safe shape metadata
and a body hash were saved; no HTML body, prompt or Cookie was retained. This was
one diagnostic call, not a successful collection or a second business Run. These
reads total twelve HTTP calls across the two preflights, first cron read and shape
check. The original sixteen retained Run files remained byte-identical in the
collector attempt. No DB-platform query, routine trigger, resource deletion,
configuration mutation or pool release was performed by the runner.

The subsequent explicit media-type rejection is regression-tested; it was not
claimed as another live invocation. At this point the live legacy route is
**not validated**. HTML could be a page fallback or login interception; this
observation does not distinguish them. Resolve the actual deployed frontend
proxy/request route before retrying, rather than asking for credentials on the
unsupported assumption that all gateway authentication has failed.

### Page comparison and implementation validation (2026-10-13)

Two additional, separately bounded metadata-only GETs inspected the original
user-provided `/assistant` page, without following redirects: without credentials
it returned 302; with the existing Cookie it returned 200 and 17,202 bytes of HTML.
Its content hash differed from the cron HTML. This brings the recorded discovery/
diagnostic total to fourteen HTTP calls, not twelve. Neither body nor a redirect
URL was persisted. These observations do not identify the cron page as either a
login page or an application fallback and do not validate the legacy API route.
The remaining route input is an actual deployed frontend request/proxy binding,
not an assertion that the two Bots have no jobs.

Twenty-seven dedicated tests and sixty-two focused fixture/preflight/browser
regressions passed. The final complete runner suite passed **1057 tests in
164.958 seconds**, with owned BCS/CLI enabled and no failures/errors/skips. Case
guard, strict OpenSpec and diff checks passed. Independent read-only review found
and then closed three issues: the reserved all selector could expand scope, a
second-target business failure was downgraded to INCOMPLETE, and preflight response
IDs could carry arbitrary upstream text. Regression covers real preflight response
parsing (only the opener is fake), normal/cancelled digest-only output, and the
negative selector/second-failure paths. The initial test-harness import error was
corrected; no product assertion was weakened to make it pass.

The earlier real HTTP attempt preceded the final media-type and origin-confirmation
hardening; later success tests are synthetic contract tests, not a claimed new live
success. The legacy all-module singlebox coverage gate was not run. The original
retained execution remains UNCONFIRMED and its pool remains quarantined. This
configuration diagnostic does not complete the remaining input-boundary, callback,
background-work or retained-reuse requirements. Overall Spec remains 30/54.
