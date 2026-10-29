# Read the effective native BCN delivery policy

`policy-check` is independent read-only discovery for the two bound test actors.
It does not prepare a Run, acquire/release a fixture pool, send a Bot message,
change policy, renew an approval, or qualify any E2E Case. A successful read has
`status=POLICY_OBSERVED`, exit 0 and **ready_to_execute=false**. It is not a green
release or an execution-completion certificate.

## Why this is a separate native read, not a gateway fallback

Fixed enterprise OCB object `b4961722767e4778bb7e3d7df579ee1023a62107`,
`apps/gateway/configs/application.yaml:321-348` and its environment overlays,
declares the collaboration routes but no `/admin/message-delivery/policy` route.
Its `route_security` wildcard is authentication policy, not a forwarding route.
Public source object `ee9f4f8af4dbe4b768469df9043215093a74da58` mounts the exact
native path in `apps/bcs/crates/adapters/http/bcs-http/src/router.rs:388`.
The old `/openapi/v1/admin/message-delivery/policy` path is removed. Neither
source snapshot is proof of the current deployed routing configuration.

Therefore use a **separately reviewed native profile**, not an automatic rewrite
of the gateway Profile or its credential origin. It must select:

- environment tier `preprod`, the actual expected environment and Human;
- entrypoint `kind=bcs-direct`, the explicitly approved HTTPS BCN origin,
  `manifest_face=legacy` and the existing `api_face=public-v1` profile value;
- identity binding `GET /auth/user`, response `plain-user-info`;
- exactly one `Cookie` header environment reference, no Bearer/service/forged
  principal header; set the credential locally, never put its value in arguments;
- the same two explicitly bound test Bot identities whose policy you need to see.

The existing profile schema is retained. Unresolved observer configuration is not
used by this command, which does not invoke MCP. Native identity remains discovery-
only for the existing pilot: this new command does not enable native business or
WebSocket operations. Do not edit an old frozen Run Profile to run this check.

## Explicit read review

The private JSON review binds the complete native Profile fingerprint, Human,
fixed path, reviewed deployment/source evidence and a current window of at most
one hour. This template deliberately grants no authority:

```json
{
  "schema_version": 1,
  "contract": "bcs-effective-delivery-policy-read-v1",
  "profile_digest": "<complete-native-profile-canonical-sha256>",
  "reviewer": "<bound-human-id>",
  "reviewed_at": 0,
  "expires_at": 0,
  "path": "/admin/message-delivery/policy",
  "source_review": {
    "reference": "actual-endpoint-deployment-review",
    "sha256": "<actual-review-evidence-sha256>",
    "deployment_commit": "<reviewed-full-commit>"
  }
}
```

Obtain actual review evidence rather than filling in plausible timestamps/hashes.
The receipt is labelled OPERATOR_ATTESTED, not signed authorization or automatic
live deployment verification. Changes/expiry stop subsequent reads; no approval
is refreshed automatically. Reading policy does not authorize its replacement.

```bash
python3 runner.py policy-check \
  --profile /private/path/native-read-profile.json \
  --review /private/path/policy-read-review.json \
  --allow-read
```

The command emits JSON to stdout without creating a Run or report directory.
Archive it in a private operator-selected file if needed; protect the profile,
review and evidence with local file permissions. TLS validation remains enabled.

## Actual read sequence and failure semantics

At most three same-origin requests are attempted, with no redirects or retries:

1. `GET /manifest`: exact expected environment.
2. `GET /auth/user`: exact expected Human and non-local native provider.
3. `GET /admin/message-delivery/policy`: independently Human-authorized policy.

A successful identity response alone does not establish policy permission or
prove that Cookie was the authentication source. The policy endpoint independently
rejects non-Human credentials; the client supplies only the reviewed Cookie.
The native identity and policy bodies are plain objects, not V1 envelopes.
401/403 may have empty bodies: they block before JSON decoding. 503 can mean a
failed durable read or live/stored policy mismatch; it is not “policy disabled.”
No failure triggers another hostname, versioned path, identity or auth downgrade.
Missing/invalid review fails before credential resolution and network access.
Authority is checked around all reads and at the policy transport boundary.

Requests retain the existing per-call timeout and response-size caps. The native
policy GET performs one durable policy read under the source contract; the runner
adds no database query or mutation. The parser bounds the Bot map at 4096 entries,
rejects duplicate JSON keys, unknown fields, invalid modes/integers/flow switches,
and projects only the bound actors. It never publishes the full policy Bot map,
unrelated actor IDs, updater name, native user name/avatar, raw error or Cookie.
Cancellation returns 130; observation/configuration failure returns 2.

## What the result establishes—and does not

Source `services/bcs-message-flow/src/delivery_policy.rs:82-88` compares the
responding process's live snapshot to its durable policy and fails on mismatch.
The returned version/fingerprint and two resolved per-Bot policies are therefore
more useful than querying the stored policy row alone. Resolution follows the
source's defaults plus optional non-null overrides, not an invented effective
configuration. Version zero is the synthetic disabled policy when no row exists.

`managed_new_flows` and `both_actors_group_system_managed` describe policy for
**new work at the responding service**, not evidence of dispatch readiness or
complete historical coverage. A true `pause_dispatch` does not terminate active
work. Retry configuration applies to eligible definitely-not-sent failures, not
arbitrary replay. The result does not prove all replicas agree, identify the
transport of a previous Run, assert scheduler health, close callbacks/interaction,
retroactively change old observations, or permit pool reuse. Those remain explicit
NOT_PROVEN/UNCONFIRMED fields and separate acceptance requirements.

## Local validation record

2026-10-12: 21 focused policy tests and 28 existing identity tests passed; the
final 935-test runner regression passed with owned BCS/CLI runtime enabled
(134.553 seconds, no failures/errors/skips). Case guard, strict OpenSpec and diff
checks passed. Independent source/implementation review found no required
correction. No live preproduction request, policy mutation or legacy singlebox
coverage gate was performed for this increment.

Source audit did not alter either reference checkout. Enterprise OCB was
`dev@ce23b0effd10ce32d7f5855c9ab90fd30aab3244` with an existing Cargo.lock change;
public mirror was clean detached `990a8c59fe61a4a2bc2ff6aba90d959350eb413c`.
All deployment-era conclusions above use the explicit immutable objects, not
those checkout states as a production baseline.

## First real native policy observation (2026-10-12, 20:26 CST)

The continuing user Goal invoked the existing command once at runner commit
`1bea56ef0`, using the already bound native Profile and private Cookie reference.
A separate delegated source-review record bound the fixed source evidence and
this read; it did not alter or extend any historical Run receipt. The record
identifies the coding agent's review under the Human's delegation, not a manual
source review performed by the Human or new platform permissions. Its declared
source/deployment provenance remains historical, with live deployment verification
NOT_PERFORMED.

All three same-origin GETs returned 200; result POLICY_OBSERVED, exit 0. The
response's policy version was **22**, updated_at **2026-10-09 16:12:45.992 +08:00**.
Both bound actors resolved to mode `enforce`, Group/System managed, max_running 8,
max_queued 20 and min_send_interval_ms 1000. Dispatch was not paused; queue TTL was
300000 ms; safe_retry was null. No unrelated Bot policy or credentials were
published. The existing Cookie worked for this invocation; this does not promise
future credential validity.

This is current responding-instance evidence. The earlier update timestamp is
not proof that every replica used this policy throughout the original business
Run, and the command did not revalidate live build SHA. Historical managed
coverage, actual transport and complete execution closure remain unproven.
The original Run/ledger/reviews/reports/pool reservation/guard hashes were unchanged.
There were zero database calls, business writes, stops, deletes or releases.

Validation: 58 policy/identity/browser-context regressions passed (0.287s).
An initial test command named two nonexistent modules and failed discovery; the
corrected command above passed without changing implementation or test assertions.
The private wrapper also had one metadata-reader error before any HTTP call;
after correcting that local JSON reader, only the single three-GET invocation
ran. These local failures were not hidden as successful runs or retried remote
business operations. Private receipts, invocation and redacted report remain
outside Git. No full release or second retained Story is qualified by this read.
