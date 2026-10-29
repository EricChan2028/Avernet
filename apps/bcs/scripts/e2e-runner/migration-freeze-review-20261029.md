# Legacy harness re-freeze review — 2026-10-29

New frozen base: `8559a7a5258a2cb6750befec187e50aa85060cc6` (worktree `e2e-dev-fresh`, branch codex/e2e-dev-fresh-31bbfdc).
Previous freeze generations: `48b23001e` (umbrella + 15 families) and `71860eae` (8 families), `74dd751b` (caller-execution).

## Drift classes found (frozen pin -> dev tip)

1. **Upstream #3173 (0873001b6) retired service session policies and callbacks.**
   - `stories.sh`: service_spec 200-persistence legs replaced by 410 Gone + `service_policy_retired`;
     the release-invocation fixture patch leg was removed from `_story_complete_and_invoke_sessions`.
   - `cli-stories.sh`: `story_cli_operator_runs_sessions_and_services` lost its service fixture patch leg.
   - `e2e.sh`: now sources `service_policy_story.sh` (new story `story_retired_service_policy`).
   - Disposition: **adopt retirement semantics**; obligations updated; new story registered.
2. **Our enabling harness patch** (`8559a7a52`, committed before this review):
   `common.sh` bcs-cli binary path `src/bcs` -> `apps/bcs` + local loopback admission.
   Disposition: harness deployment-portability lineage (#3043); obligations unchanged.
3. All other pinned source files are byte-identical to their freeze generation
   (authority/draft/fixed-loop/edge/webhook*session* harness files unchanged at dev tip).

## Per-contract pin table

### caller-execution-migration-contract.json
- old source_revision: `74dd751b623b7701608374fc4c7c4a6cf58278da` -> new: `8559a7a5258a2cb6750befec187e50aa85060cc6`
- `caller_execution_story.py` **DIGEST-MISMATCH-AT-FREEZE**: old 7d7f565b0b9c… -> new 7d7f565b0b9c…, hunks=0
- `caller_e2e_coverage.sh` **DIGEST-MISMATCH-AT-FREEZE**: old 4e4ee24551ec… -> new 4e4ee24551ec…, hunks=0

### channel-cli-migration-contract.json
- old source_revision: `48b23001e2edf63fbedc80acdf1474e5c105ad23` -> new: `8559a7a5258a2cb6750befec187e50aa85060cc6`
- `cli-stories.sh` **DIGEST-MISMATCH-AT-FREEZE**: old 779672ec0d0c… -> new 576031c02600…, hunks=8
  --- frozen:cli-stories.sh
  +++ dev-tip:cli-stories.sh
  @@ -451,5 +451,2 @@
   
  -    api_patch "/groups/${group_id}/settings" '{"service_spec":{"max_concurrency":2}}'
  -    require_status "CLI service fixture enables group invocation" "200" || return
  -
       _cli_story_run "release pipeline invokes the group service" PM service invoke \
- `common.sh` **DIGEST-MISMATCH-AT-FREEZE**: old ca4d2571f58f… -> new 44f5445031e8…, hunks=12
  --- frozen:common.sh
  +++ dev-tip:common.sh
  @@ -443,3 +443,3 @@
   #   1. $BCS_CLI_BIN (coverage script injects the instrumented build)
  -#   2. src/bcs/target/debug/bcs-cli
  +#   2. apps/bcs/target/debug/bcs-cli
   #   3. fallback: cargo run -p bcs-cli --quiet -- (with warn)
  @@ -453,3 +453,3 @@
       repo_root="$(cd "$SCRIPT_DIR/../../../.." && pwd)"
  -    local debug_bin="$repo_root/src/bcs/target/debug/bcs-cli"
  +    local debug_bin="$repo_root/apps/bcs/target/debug/bcs-cli"
       if [[ -x "$debug_bin" ]]; then
- `stories.sh` **DIGEST-MISMATCH-AT-FREEZE**: old 68fff1b3a519… -> new 3025c6ee245a…, hunks=17
  --- frozen:stories.sh
  +++ dev-tip:stories.sh
  @@ -461,5 +461,4 @@
       api_patch "/groups/${group_id}/settings" '{"service_spec":{"max_concurrency":2}}'
  -    require_status "group is exposed as a bounded service" "200" || return
  -    assert_json_eq "service group max concurrency is persisted" "$RESPONSE" "service_spec.max_concurrency" "2"
  -    assert_json_eq "group settings update returns stable status" "$RESPONSE" "status" "ok"
  +    require_status "retired group policy settings return Gone" "410" || return
  +    assert_json_eq "retired policy is identified" "$RESPONSE" "error" "service_policy_retired"
   
  @@ -733,6 +732,2 @@
       assert_json_eq "read-back keeps completion output" "$RESPONSE" "output.summary" "release approved"
  -
  -    api_patch "/groups/${group_id}/settings" '{"service_spec":{"max_concurrency":2}}'
  -    require_status "release group is configured for service invocations" "200" || return
  -    assert_json_eq "service invocation concurrency is two" "$RESPONSE" "service_spec.max_concurrency" "2"
   

### channel-http-migration-contract.json
- old source_revision: `48b23001e2edf63fbedc80acdf1474e5c105ad23` -> new: `8559a7a5258a2cb6750befec187e50aa85060cc6`
- `stories.sh` **DIGEST-MISMATCH-AT-FREEZE**: old 68fff1b3a519… -> new 3025c6ee245a…, hunks=17
  --- frozen:stories.sh
  +++ dev-tip:stories.sh
  @@ -461,5 +461,4 @@
       api_patch "/groups/${group_id}/settings" '{"service_spec":{"max_concurrency":2}}'
  -    require_status "group is exposed as a bounded service" "200" || return
  -    assert_json_eq "service group max concurrency is persisted" "$RESPONSE" "service_spec.max_concurrency" "2"
  -    assert_json_eq "group settings update returns stable status" "$RESPONSE" "status" "ok"
  +    require_status "retired group policy settings return Gone" "410" || return
  +    assert_json_eq "retired policy is identified" "$RESPONSE" "error" "service_policy_retired"
   
  @@ -733,6 +732,2 @@
       assert_json_eq "read-back keeps completion output" "$RESPONSE" "output.summary" "release approved"
  -
  -    api_patch "/groups/${group_id}/settings" '{"service_spec":{"max_concurrency":2}}'
  -    require_status "release group is configured for service invocations" "200" || return
  -    assert_json_eq "service invocation concurrency is two" "$RESPONSE" "service_spec.max_concurrency" "2"
   

### collaboration-team-migration-contract.json
- old source_revision: `71860eae99c62d7bf1349a0f46562f6395acbf1e` -> new: `8559a7a5258a2cb6750befec187e50aa85060cc6`
- `cli-stories.sh` **DIGEST-MISMATCH-AT-FREEZE**: old 779672ec0d0c… -> new 576031c02600…, hunks=8
  --- frozen:cli-stories.sh
  +++ dev-tip:cli-stories.sh
  @@ -451,5 +451,2 @@
   
  -    api_patch "/groups/${group_id}/settings" '{"service_spec":{"max_concurrency":2}}'
  -    require_status "CLI service fixture enables group invocation" "200" || return
  -
       _cli_story_run "release pipeline invokes the group service" PM service invoke \

### custom-collaboration-migration-contract.json
- old source_revision: `71860eae99c62d7bf1349a0f46562f6395acbf1e` -> new: `8559a7a5258a2cb6750befec187e50aa85060cc6`
- `cli-stories.sh` **DIGEST-MISMATCH-AT-FREEZE**: old 779672ec0d0c… -> new 576031c02600…, hunks=8
  --- frozen:cli-stories.sh
  +++ dev-tip:cli-stories.sh
  @@ -451,5 +451,2 @@
   
  -    api_patch "/groups/${group_id}/settings" '{"service_spec":{"max_concurrency":2}}'
  -    require_status "CLI service fixture enables group invocation" "200" || return
  -
       _cli_story_run "release pipeline invokes the group service" PM service invoke \

### delivery-policy-migration-contract.json
- old source_revision: `48b23001e2edf63fbedc80acdf1474e5c105ad23` -> new: `8559a7a5258a2cb6750befec187e50aa85060cc6`
- `stories.sh` **DIGEST-MISMATCH-AT-FREEZE**: old 68fff1b3a519… -> new 3025c6ee245a…, hunks=17
  --- frozen:stories.sh
  +++ dev-tip:stories.sh
  @@ -461,5 +461,4 @@
       api_patch "/groups/${group_id}/settings" '{"service_spec":{"max_concurrency":2}}'
  -    require_status "group is exposed as a bounded service" "200" || return
  -    assert_json_eq "service group max concurrency is persisted" "$RESPONSE" "service_spec.max_concurrency" "2"
  -    assert_json_eq "group settings update returns stable status" "$RESPONSE" "status" "ok"
  +    require_status "retired group policy settings return Gone" "410" || return
  +    assert_json_eq "retired policy is identified" "$RESPONSE" "error" "service_policy_retired"
   
  @@ -733,6 +732,2 @@
       assert_json_eq "read-back keeps completion output" "$RESPONSE" "output.summary" "release approved"
  -
  -    api_patch "/groups/${group_id}/settings" '{"service_spec":{"max_concurrency":2}}'
  -    require_status "release group is configured for service invocations" "200" || return
  -    assert_json_eq "service invocation concurrency is two" "$RESPONSE" "service_spec.max_concurrency" "2"
   

### direct-chat-migration-contract.json
- old source_revision: `48b23001e2edf63fbedc80acdf1474e5c105ad23` -> new: `8559a7a5258a2cb6750befec187e50aa85060cc6`
- `stories.sh` **DIGEST-MISMATCH-AT-FREEZE**: old 68fff1b3a519… -> new 3025c6ee245a…, hunks=17
  --- frozen:stories.sh
  +++ dev-tip:stories.sh
  @@ -461,5 +461,4 @@
       api_patch "/groups/${group_id}/settings" '{"service_spec":{"max_concurrency":2}}'
  -    require_status "group is exposed as a bounded service" "200" || return
  -    assert_json_eq "service group max concurrency is persisted" "$RESPONSE" "service_spec.max_concurrency" "2"
  -    assert_json_eq "group settings update returns stable status" "$RESPONSE" "status" "ok"
  +    require_status "retired group policy settings return Gone" "410" || return
  +    assert_json_eq "retired policy is identified" "$RESPONSE" "error" "service_policy_retired"
   
  @@ -733,6 +732,2 @@
       assert_json_eq "read-back keeps completion output" "$RESPONSE" "output.summary" "release approved"
  -
  -    api_patch "/groups/${group_id}/settings" '{"service_spec":{"max_concurrency":2}}'
  -    require_status "release group is configured for service invocations" "200" || return
  -    assert_json_eq "service invocation concurrency is two" "$RESPONSE" "service_spec.max_concurrency" "2"
   
- `actor.sh` **DIGEST-MISMATCH-AT-FREEZE**: old 9b7679964e0a… -> new 9b7679964e0a…, hunks=0
- `common.sh` **DIGEST-MISMATCH-AT-FREEZE**: old ca4d2571f58f… -> new 44f5445031e8…, hunks=12
  --- frozen:common.sh
  +++ dev-tip:common.sh
  @@ -443,3 +443,3 @@
   #   1. $BCS_CLI_BIN (coverage script injects the instrumented build)
  -#   2. src/bcs/target/debug/bcs-cli
  +#   2. apps/bcs/target/debug/bcs-cli
   #   3. fallback: cargo run -p bcs-cli --quiet -- (with warn)
  @@ -453,3 +453,3 @@
       repo_root="$(cd "$SCRIPT_DIR/../../../.." && pwd)"
  -    local debug_bin="$repo_root/src/bcs/target/debug/bcs-cli"
  +    local debug_bin="$repo_root/apps/bcs/target/debug/bcs-cli"
       if [[ -x "$debug_bin" ]]; then

### draft-group-migration-contract.json
- old source_revision: `48b23001e2edf63fbedc80acdf1474e5c105ad23` -> new: `8559a7a5258a2cb6750befec187e50aa85060cc6`
- `collaboration_draft_group_story.py` **DIGEST-MISMATCH-AT-FREEZE**: old f0df15f704b9… -> new f0df15f704b9…, hunks=0
- `indentation-regression.json` **DIGEST-MISMATCH-AT-FREEZE**: old 23e0a22ea7f1… -> new 23e0a22ea7f1…, hunks=0
- `http_provider_judge_mock.py` **DIGEST-MISMATCH-AT-FREEZE**: old fe56c8c2d79c… -> new fe56c8c2d79c…, hunks=0

### edge-admission-migration-contract.json
- old source_revision: `71860eae99c62d7bf1349a0f46562f6395acbf1e` -> new: `8559a7a5258a2cb6750befec187e50aa85060cc6`
- `edge_permission.sh` **DIGEST-MISMATCH-AT-FREEZE**: old e395ff4f203c… -> new e395ff4f203c…, hunks=0

### edge-bot-search-migration-contract.json
- old source_revision: `71860eae99c62d7bf1349a0f46562f6395acbf1e` -> new: `8559a7a5258a2cb6750befec187e50aa85060cc6`
- `edge_permission.sh` **DIGEST-MISMATCH-AT-FREEZE**: old e395ff4f203c… -> new e395ff4f203c…, hunks=0

### edge-friend-connections-list-migration-contract.json
- old source_revision: `71860eae99c62d7bf1349a0f46562f6395acbf1e` -> new: `8559a7a5258a2cb6750befec187e50aa85060cc6`
- `edge_permission.sh` **DIGEST-MISMATCH-AT-FREEZE**: old e395ff4f203c… -> new e395ff4f203c…, hunks=0

### edge-friend-requests-list-migration-contract.json
- old source_revision: `71860eae99c62d7bf1349a0f46562f6395acbf1e` -> new: `8559a7a5258a2cb6750befec187e50aa85060cc6`
- `edge_permission.sh` **DIGEST-MISMATCH-AT-FREEZE**: old e395ff4f203c… -> new e395ff4f203c…, hunks=0

### fixed-loop-migration-contract.json
- old source_revision: `48b23001e2edf63fbedc80acdf1474e5c105ad23` -> new: `8559a7a5258a2cb6750befec187e50aa85060cc6`
- `fixed_loop_story.sh` **DIGEST-MISMATCH-AT-FREEZE**: old 14939d995cd1… -> new 14939d995cd1…, hunks=0
- `fixed_loop_story.py` **DIGEST-MISMATCH-AT-FREEZE**: old 8fc6daeace71… -> new 8fc6daeace71…, hunks=0
- `common.sh` **DIGEST-MISMATCH-AT-FREEZE**: old ca4d2571f58f… -> new 44f5445031e8…, hunks=12
  --- frozen:common.sh
  +++ dev-tip:common.sh
  @@ -443,3 +443,3 @@
   #   1. $BCS_CLI_BIN (coverage script injects the instrumented build)
  -#   2. src/bcs/target/debug/bcs-cli
  +#   2. apps/bcs/target/debug/bcs-cli
   #   3. fallback: cargo run -p bcs-cli --quiet -- (with warn)
  @@ -453,3 +453,3 @@
       repo_root="$(cd "$SCRIPT_DIR/../../../.." && pwd)"
  -    local debug_bin="$repo_root/src/bcs/target/debug/bcs-cli"
  +    local debug_bin="$repo_root/apps/bcs/target/debug/bcs-cli"
       if [[ -x "$debug_bin" ]]; then

### group-context-migration-contract.json
- old source_revision: `71860eae99c62d7bf1349a0f46562f6395acbf1e` -> new: `8559a7a5258a2cb6750befec187e50aa85060cc6`
- `cli-stories.sh` **DIGEST-MISMATCH-AT-FREEZE**: old 779672ec0d0c… -> new 576031c02600…, hunks=8
  --- frozen:cli-stories.sh
  +++ dev-tip:cli-stories.sh
  @@ -451,5 +451,2 @@
   
  -    api_patch "/groups/${group_id}/settings" '{"service_spec":{"max_concurrency":2}}'
  -    require_status "CLI service fixture enables group invocation" "200" || return
  -
       _cli_story_run "release pipeline invokes the group service" PM service invoke \

### judge-migration-contract.json
- old source_revision: `48b23001e2edf63fbedc80acdf1474e5c105ad23` -> new: `8559a7a5258a2cb6750befec187e50aa85060cc6`
- `stories.sh` **DIGEST-MISMATCH-AT-FREEZE**: old 68fff1b3a519… -> new 3025c6ee245a…, hunks=17
  --- frozen:stories.sh
  +++ dev-tip:stories.sh
  @@ -461,5 +461,4 @@
       api_patch "/groups/${group_id}/settings" '{"service_spec":{"max_concurrency":2}}'
  -    require_status "group is exposed as a bounded service" "200" || return
  -    assert_json_eq "service group max concurrency is persisted" "$RESPONSE" "service_spec.max_concurrency" "2"
  -    assert_json_eq "group settings update returns stable status" "$RESPONSE" "status" "ok"
  +    require_status "retired group policy settings return Gone" "410" || return
  +    assert_json_eq "retired policy is identified" "$RESPONSE" "error" "service_policy_retired"
   
  @@ -733,6 +732,2 @@
       assert_json_eq "read-back keeps completion output" "$RESPONSE" "output.summary" "release approved"
  -
  -    api_patch "/groups/${group_id}/settings" '{"service_spec":{"max_concurrency":2}}'
  -    require_status "release group is configured for service invocations" "200" || return
  -    assert_json_eq "service invocation concurrency is two" "$RESPONSE" "service_spec.max_concurrency" "2"
   
- `common.sh` **DIGEST-MISMATCH-AT-FREEZE**: old ca4d2571f58f… -> new 44f5445031e8…, hunks=12
  --- frozen:common.sh
  +++ dev-tip:common.sh
  @@ -443,3 +443,3 @@
   #   1. $BCS_CLI_BIN (coverage script injects the instrumented build)
  -#   2. src/bcs/target/debug/bcs-cli
  +#   2. apps/bcs/target/debug/bcs-cli
   #   3. fallback: cargo run -p bcs-cli --quiet -- (with warn)
  @@ -453,3 +453,3 @@
       repo_root="$(cd "$SCRIPT_DIR/../../../.." && pwd)"
  -    local debug_bin="$repo_root/src/bcs/target/debug/bcs-cli"
  +    local debug_bin="$repo_root/apps/bcs/target/debug/bcs-cli"
       if [[ -x "$debug_bin" ]]; then
- `http_provider_judge_mock.py` **DIGEST-MISMATCH-AT-FREEZE**: old fe56c8c2d79c… -> new fe56c8c2d79c…, hunks=0
- `mock_services.sh` **DIGEST-MISMATCH-AT-FREEZE**: old 712273ff91e4… -> new 712273ff91e4…, hunks=0

### nested-fixed-loop-migration-contract.json
- old source_revision: `48b23001e2edf63fbedc80acdf1474e5c105ad23` -> new: `8559a7a5258a2cb6750befec187e50aa85060cc6`
- `nested_fixed_loop_story.py` **DIGEST-MISMATCH-AT-FREEZE**: old f50edb9c4fc3… -> new f50edb9c4fc3…, hunks=0
- `fixed_loop_story.py` **DIGEST-MISMATCH-AT-FREEZE**: old 8fc6daeace71… -> new 8fc6daeace71…, hunks=0
- `http_provider_judge_mock.py` **DIGEST-MISMATCH-AT-FREEZE**: old fe56c8c2d79c… -> new fe56c8c2d79c…, hunks=0

### network-migration-contract.json
- old source_revision: `48b23001e2edf63fbedc80acdf1474e5c105ad23` -> new: `8559a7a5258a2cb6750befec187e50aa85060cc6`
- `stories.sh` **DIGEST-MISMATCH-AT-FREEZE**: old 68fff1b3a519… -> new 3025c6ee245a…, hunks=17
  --- frozen:stories.sh
  +++ dev-tip:stories.sh
  @@ -461,5 +461,4 @@
       api_patch "/groups/${group_id}/settings" '{"service_spec":{"max_concurrency":2}}'
  -    require_status "group is exposed as a bounded service" "200" || return
  -    assert_json_eq "service group max concurrency is persisted" "$RESPONSE" "service_spec.max_concurrency" "2"
  -    assert_json_eq "group settings update returns stable status" "$RESPONSE" "status" "ok"
  +    require_status "retired group policy settings return Gone" "410" || return
  +    assert_json_eq "retired policy is identified" "$RESPONSE" "error" "service_policy_retired"
   
  @@ -733,6 +732,2 @@
       assert_json_eq "read-back keeps completion output" "$RESPONSE" "output.summary" "release approved"
  -
  -    api_patch "/groups/${group_id}/settings" '{"service_spec":{"max_concurrency":2}}'
  -    require_status "release group is configured for service invocations" "200" || return
  -    assert_json_eq "service invocation concurrency is two" "$RESPONSE" "service_spec.max_concurrency" "2"
   
- `actor.sh` **DIGEST-MISMATCH-AT-FREEZE**: old 9b7679964e0a… -> new 9b7679964e0a…, hunks=0

### operator-cli-migration-contract.json
- old source_revision: `48b23001e2edf63fbedc80acdf1474e5c105ad23` -> new: `8559a7a5258a2cb6750befec187e50aa85060cc6`
- `stories.sh` **DIGEST-MISMATCH-AT-FREEZE**: old 68fff1b3a519… -> new 3025c6ee245a…, hunks=17
  --- frozen:stories.sh
  +++ dev-tip:stories.sh
  @@ -461,5 +461,4 @@
       api_patch "/groups/${group_id}/settings" '{"service_spec":{"max_concurrency":2}}'
  -    require_status "group is exposed as a bounded service" "200" || return
  -    assert_json_eq "service group max concurrency is persisted" "$RESPONSE" "service_spec.max_concurrency" "2"
  -    assert_json_eq "group settings update returns stable status" "$RESPONSE" "status" "ok"
  +    require_status "retired group policy settings return Gone" "410" || return
  +    assert_json_eq "retired policy is identified" "$RESPONSE" "error" "service_policy_retired"
   
  @@ -733,6 +732,2 @@
       assert_json_eq "read-back keeps completion output" "$RESPONSE" "output.summary" "release approved"
  -
  -    api_patch "/groups/${group_id}/settings" '{"service_spec":{"max_concurrency":2}}'
  -    require_status "release group is configured for service invocations" "200" || return
  -    assert_json_eq "service invocation concurrency is two" "$RESPONSE" "service_spec.max_concurrency" "2"
   
- `cli.sh` **DIGEST-MISMATCH-AT-FREEZE**: old 70b77d816d0f… -> new 70b77d816d0f…, hunks=0
- `cli-stories.sh` **DIGEST-MISMATCH-AT-FREEZE**: old 779672ec0d0c… -> new 576031c02600…, hunks=8
  --- frozen:cli-stories.sh
  +++ dev-tip:cli-stories.sh
  @@ -451,5 +451,2 @@
   
  -    api_patch "/groups/${group_id}/settings" '{"service_spec":{"max_concurrency":2}}'
  -    require_status "CLI service fixture enables group invocation" "200" || return
  -
       _cli_story_run "release pipeline invokes the group service" PM service invoke \
- `common.sh` **DIGEST-MISMATCH-AT-FREEZE**: old ca4d2571f58f… -> new 44f5445031e8…, hunks=12
  --- frozen:common.sh
  +++ dev-tip:common.sh
  @@ -443,3 +443,3 @@
   #   1. $BCS_CLI_BIN (coverage script injects the instrumented build)
  -#   2. src/bcs/target/debug/bcs-cli
  +#   2. apps/bcs/target/debug/bcs-cli
   #   3. fallback: cargo run -p bcs-cli --quiet -- (with warn)
  @@ -453,3 +453,3 @@
       repo_root="$(cd "$SCRIPT_DIR/../../../.." && pwd)"
  -    local debug_bin="$repo_root/src/bcs/target/debug/bcs-cli"
  +    local debug_bin="$repo_root/apps/bcs/target/debug/bcs-cli"
       if [[ -x "$debug_bin" ]]; then
- `group.sh` **DIGEST-MISMATCH-AT-FREEZE**: old 2bbb82c41726… -> new 2bbb82c41726…, hunks=0

### provider-migration-contract.json
- old source_revision: `48b23001e2edf63fbedc80acdf1474e5c105ad23` -> new: `8559a7a5258a2cb6750befec187e50aa85060cc6`
- `stories.sh` **DIGEST-MISMATCH-AT-FREEZE**: old 68fff1b3a519… -> new 3025c6ee245a…, hunks=17
  --- frozen:stories.sh
  +++ dev-tip:stories.sh
  @@ -461,5 +461,4 @@
       api_patch "/groups/${group_id}/settings" '{"service_spec":{"max_concurrency":2}}'
  -    require_status "group is exposed as a bounded service" "200" || return
  -    assert_json_eq "service group max concurrency is persisted" "$RESPONSE" "service_spec.max_concurrency" "2"
  -    assert_json_eq "group settings update returns stable status" "$RESPONSE" "status" "ok"
  +    require_status "retired group policy settings return Gone" "410" || return
  +    assert_json_eq "retired policy is identified" "$RESPONSE" "error" "service_policy_retired"
   
  @@ -733,6 +732,2 @@
       assert_json_eq "read-back keeps completion output" "$RESPONSE" "output.summary" "release approved"
  -
  -    api_patch "/groups/${group_id}/settings" '{"service_spec":{"max_concurrency":2}}'
  -    require_status "release group is configured for service invocations" "200" || return
  -    assert_json_eq "service invocation concurrency is two" "$RESPONSE" "service_spec.max_concurrency" "2"
   

### result-queue-migration-contract.json
- old source_revision: `48b23001e2edf63fbedc80acdf1474e5c105ad23` -> new: `8559a7a5258a2cb6750befec187e50aa85060cc6`
- `result_queue_story.py` **DIGEST-MISMATCH-AT-FREEZE**: old b672cc7bc054… -> new b672cc7bc054…, hunks=0
- `fixed_loop_story.py` **DIGEST-MISMATCH-AT-FREEZE**: old 8fc6daeace71… -> new 8fc6daeace71…, hunks=0
- `http_provider_judge_mock.py` **DIGEST-MISMATCH-AT-FREEZE**: old fe56c8c2d79c… -> new fe56c8c2d79c…, hunks=0

### session-migration-contract.json
- old source_revision: `48b23001e2edf63fbedc80acdf1474e5c105ad23` -> new: `8559a7a5258a2cb6750befec187e50aa85060cc6`
- `stories.sh` **DIGEST-MISMATCH-AT-FREEZE**: old 68fff1b3a519… -> new 3025c6ee245a…, hunks=17
  --- frozen:stories.sh
  +++ dev-tip:stories.sh
  @@ -461,5 +461,4 @@
       api_patch "/groups/${group_id}/settings" '{"service_spec":{"max_concurrency":2}}'
  -    require_status "group is exposed as a bounded service" "200" || return
  -    assert_json_eq "service group max concurrency is persisted" "$RESPONSE" "service_spec.max_concurrency" "2"
  -    assert_json_eq "group settings update returns stable status" "$RESPONSE" "status" "ok"
  +    require_status "retired group policy settings return Gone" "410" || return
  +    assert_json_eq "retired policy is identified" "$RESPONSE" "error" "service_policy_retired"
   
  @@ -733,6 +732,2 @@
       assert_json_eq "read-back keeps completion output" "$RESPONSE" "output.summary" "release approved"
  -
  -    api_patch "/groups/${group_id}/settings" '{"service_spec":{"max_concurrency":2}}'
  -    require_status "release group is configured for service invocations" "200" || return
  -    assert_json_eq "service invocation concurrency is two" "$RESPONSE" "service_spec.max_concurrency" "2"
   
- `group.sh` **DIGEST-MISMATCH-AT-FREEZE**: old 2bbb82c41726… -> new 2bbb82c41726…, hunks=0
- `common.sh` **DIGEST-MISMATCH-AT-FREEZE**: old ca4d2571f58f… -> new 44f5445031e8…, hunks=12
  --- frozen:common.sh
  +++ dev-tip:common.sh
  @@ -443,3 +443,3 @@
   #   1. $BCS_CLI_BIN (coverage script injects the instrumented build)
  -#   2. src/bcs/target/debug/bcs-cli
  +#   2. apps/bcs/target/debug/bcs-cli
   #   3. fallback: cargo run -p bcs-cli --quiet -- (with warn)
  @@ -453,3 +453,3 @@
       repo_root="$(cd "$SCRIPT_DIR/../../../.." && pwd)"
  -    local debug_bin="$repo_root/src/bcs/target/debug/bcs-cli"
  +    local debug_bin="$repo_root/apps/bcs/target/debug/bcs-cli"
       if [[ -x "$debug_bin" ]]; then
- `group_session_ws_probe.py` **DIGEST-MISMATCH-AT-FREEZE**: old 954b4b962f2e… -> new 954b4b962f2e…, hunks=0

### sessions-services-migration-contract.json
- old source_revision: `71860eae99c62d7bf1349a0f46562f6395acbf1e` -> new: `8559a7a5258a2cb6750befec187e50aa85060cc6`
- `cli-stories.sh` **DIGEST-MISMATCH-AT-FREEZE**: old 779672ec0d0c… -> new 576031c02600…, hunks=8
  --- frozen:cli-stories.sh
  +++ dev-tip:cli-stories.sh
  @@ -451,5 +451,2 @@
   
  -    api_patch "/groups/${group_id}/settings" '{"service_spec":{"max_concurrency":2}}'
  -    require_status "CLI service fixture enables group invocation" "200" || return
  -
       _cli_story_run "release pipeline invokes the group service" PM service invoke \

### structured-migration-contract.json
- old source_revision: `48b23001e2edf63fbedc80acdf1474e5c105ad23` -> new: `8559a7a5258a2cb6750befec187e50aa85060cc6`
- `stories.sh` **DIGEST-MISMATCH-AT-FREEZE**: old 68fff1b3a519… -> new 3025c6ee245a…, hunks=17
  --- frozen:stories.sh
  +++ dev-tip:stories.sh
  @@ -461,5 +461,4 @@
       api_patch "/groups/${group_id}/settings" '{"service_spec":{"max_concurrency":2}}'
  -    require_status "group is exposed as a bounded service" "200" || return
  -    assert_json_eq "service group max concurrency is persisted" "$RESPONSE" "service_spec.max_concurrency" "2"
  -    assert_json_eq "group settings update returns stable status" "$RESPONSE" "status" "ok"
  +    require_status "retired group policy settings return Gone" "410" || return
  +    assert_json_eq "retired policy is identified" "$RESPONSE" "error" "service_policy_retired"
   
  @@ -733,6 +732,2 @@
       assert_json_eq "read-back keeps completion output" "$RESPONSE" "output.summary" "release approved"
  -
  -    api_patch "/groups/${group_id}/settings" '{"service_spec":{"max_concurrency":2}}'
  -    require_status "release group is configured for service invocations" "200" || return
  -    assert_json_eq "service invocation concurrency is two" "$RESPONSE" "service_spec.max_concurrency" "2"
   
- `common.sh` **DIGEST-MISMATCH-AT-FREEZE**: old ca4d2571f58f… -> new 44f5445031e8…, hunks=12
  --- frozen:common.sh
  +++ dev-tip:common.sh
  @@ -443,3 +443,3 @@
   #   1. $BCS_CLI_BIN (coverage script injects the instrumented build)
  -#   2. src/bcs/target/debug/bcs-cli
  +#   2. apps/bcs/target/debug/bcs-cli
   #   3. fallback: cargo run -p bcs-cli --quiet -- (with warn)
  @@ -453,3 +453,3 @@
       repo_root="$(cd "$SCRIPT_DIR/../../../.." && pwd)"
  -    local debug_bin="$repo_root/src/bcs/target/debug/bcs-cli"
  +    local debug_bin="$repo_root/apps/bcs/target/debug/bcs-cli"
       if [[ -x "$debug_bin" ]]; then

### webhook-migration-contract.json
- old source_revision: `48b23001e2edf63fbedc80acdf1474e5c105ad23` -> new: `8559a7a5258a2cb6750befec187e50aa85060cc6`
- `stories.sh` **DIGEST-MISMATCH-AT-FREEZE**: old 68fff1b3a519… -> new 3025c6ee245a…, hunks=17
  --- frozen:stories.sh
  +++ dev-tip:stories.sh
  @@ -461,5 +461,4 @@
       api_patch "/groups/${group_id}/settings" '{"service_spec":{"max_concurrency":2}}'
  -    require_status "group is exposed as a bounded service" "200" || return
  -    assert_json_eq "service group max concurrency is persisted" "$RESPONSE" "service_spec.max_concurrency" "2"
  -    assert_json_eq "group settings update returns stable status" "$RESPONSE" "status" "ok"
  +    require_status "retired group policy settings return Gone" "410" || return
  +    assert_json_eq "retired policy is identified" "$RESPONSE" "error" "service_policy_retired"
   
  @@ -733,6 +732,2 @@
       assert_json_eq "read-back keeps completion output" "$RESPONSE" "output.summary" "release approved"
  -
  -    api_patch "/groups/${group_id}/settings" '{"service_spec":{"max_concurrency":2}}'
  -    require_status "release group is configured for service invocations" "200" || return
  -    assert_json_eq "service invocation concurrency is two" "$RESPONSE" "service_spec.max_concurrency" "2"
   
- `common.sh` **DIGEST-MISMATCH-AT-FREEZE**: old ca4d2571f58f… -> new 44f5445031e8…, hunks=12
  --- frozen:common.sh
  +++ dev-tip:common.sh
  @@ -443,3 +443,3 @@
   #   1. $BCS_CLI_BIN (coverage script injects the instrumented build)
  -#   2. src/bcs/target/debug/bcs-cli
  +#   2. apps/bcs/target/debug/bcs-cli
   #   3. fallback: cargo run -p bcs-cli --quiet -- (with warn)
  @@ -453,3 +453,3 @@
       repo_root="$(cd "$SCRIPT_DIR/../../../.." && pwd)"
  -    local debug_bin="$repo_root/src/bcs/target/debug/bcs-cli"
  +    local debug_bin="$repo_root/apps/bcs/target/debug/bcs-cli"
       if [[ -x "$debug_bin" ]]; then
- `http_provider_judge_mock.py` **DIGEST-MISMATCH-AT-FREEZE**: old fe56c8c2d79c… -> new fe56c8c2d79c…, hunks=0
- `group_session_ws_probe.py` **DIGEST-MISMATCH-AT-FREEZE**: old 954b4b962f2e… -> new 954b4b962f2e…, hunks=0

### migration-contracts.json
- old source_revision: `48b23001e2edf63fbedc80acdf1474e5c105ad23` -> new: `8559a7a5258a2cb6750befec187e50aa85060cc6`
- `stories.sh` **DIGEST-MISMATCH-AT-FREEZE**: old 68fff1b3a519… -> new 3025c6ee245a…, hunks=17
  --- frozen:stories.sh
  +++ dev-tip:stories.sh
  @@ -461,5 +461,4 @@
       api_patch "/groups/${group_id}/settings" '{"service_spec":{"max_concurrency":2}}'
  -    require_status "group is exposed as a bounded service" "200" || return
  -    assert_json_eq "service group max concurrency is persisted" "$RESPONSE" "service_spec.max_concurrency" "2"
  -    assert_json_eq "group settings update returns stable status" "$RESPONSE" "status" "ok"
  +    require_status "retired group policy settings return Gone" "410" || return
  +    assert_json_eq "retired policy is identified" "$RESPONSE" "error" "service_policy_retired"
   
  @@ -733,6 +732,2 @@
       assert_json_eq "read-back keeps completion output" "$RESPONSE" "output.summary" "release approved"
  -
  -    api_patch "/groups/${group_id}/settings" '{"service_spec":{"max_concurrency":2}}'
  -    require_status "release group is configured for service invocations" "200" || return
  -    assert_json_eq "service invocation concurrency is two" "$RESPONSE" "service_spec.max_concurrency" "2"
   
- `group.sh` **DIGEST-MISMATCH-AT-FREEZE**: old 2bbb82c41726… -> new 2bbb82c41726…, hunks=0
- `friends.sh` **DIGEST-MISMATCH-AT-FREEZE**: old 80c76bb471db… -> new 80c76bb471db…, hunks=0
- `session_files.sh` **DIGEST-MISMATCH-AT-FREEZE**: old 836d05d1798d… -> new 836d05d1798d…, hunks=0
- `common.sh` **DIGEST-MISMATCH-AT-FREEZE**: old ca4d2571f58f… -> new 44f5445031e8…, hunks=12
  --- frozen:common.sh
  +++ dev-tip:common.sh
  @@ -443,3 +443,3 @@
   #   1. $BCS_CLI_BIN (coverage script injects the instrumented build)
  -#   2. src/bcs/target/debug/bcs-cli
  +#   2. apps/bcs/target/debug/bcs-cli
   #   3. fallback: cargo run -p bcs-cli --quiet -- (with warn)
  @@ -453,3 +453,3 @@
       repo_root="$(cd "$SCRIPT_DIR/../../../.." && pwd)"
  -    local debug_bin="$repo_root/src/bcs/target/debug/bcs-cli"
  +    local debug_bin="$repo_root/apps/bcs/target/debug/bcs-cli"
       if [[ -x "$debug_bin" ]]; then


## Post-freeze disposition (2026-10-30)

- Promoted `story_retired_service_policy` out of `KNOWN_UNMAPPED_LEGACY_IDS` and raised
  `REVIEWED_RELEASE_PARENT_COUNT` 29→30: the retired Story (#3173 legs adopted 2026-10-29)
  becomes the 30th reviewed release parent slice (`LOCAL-SERVICE-POLICY-01`,
  `owned-local-service-policy-v1`). The source binding registry entries use freeze-content
  sha1 (`service-policy-migration-contract.json`: py `9dfc6c2a226c67a3537105e5d9c5c2fd2e69220b`,
  sh `fbec4aadd1aa747e5d2e2f80136d8a884c594e76`); drift-gate sha256 anchors are untouched.
- Artifact allowance grows 321→324 (three new slice modules plus one headroom slot).
- `render_markdown` falls back to `verification_boundary.not_proven` when `mock_boundary` is
  absent (mock-free promoted slices); existing slice output is unchanged.
- 27 focused tests for the slice (9 contract + 18 run-lifecycle, including promotion,
  registration-order and independent-slice negatives).
- Truthful-run bug found and fixed: `OwnedBcsProcess`'s stop path resets `ready=false`, so
  `child_pass`/`runtime_complete` demanding `ready is True` made every genuine run BLOCKED
  (live-1: `STOPPED/rc=0`, 23/23 HTTP checks, marker and redaction gates all PASS, yet the
  case was BLOCKED). The two unsatisfiable `ready` clauses were dropped; STOPPED + rc0 +
  frozen binary/executor digests + 23 observed checks + marker + redaction remain the gate.
  Live rerun `20261030-service-policy-live-2` → `LOCAL_SERVICE_POLICY_PASS` (8/8
  requirements, `http_checks=23`, `LOCAL_SUITE_PASS`, integrity PASS; evidence under
  `~/.codex/bcs-e2e/e2e-dev-fresh-service-policy-live-2/`; credentials `NOT_CAPTURED`).
- Count-free wording sweep: 45 standing-invariant replacements across README.md (19),
  next-story-migrations.md (22), retained-file.md (1), retained-serial-admission.md (2) and
  trusted-team-acceptance.md (1). The 2026-10-29 pin tables and dated log events keep their
  historical facts; only standing denominator claims became count-free. Acceptance grep for
  `29-case|29-entry|29-parent|29-Story|all29|original29|Original29` across the runner docs
  is empty.
- Test follow-through: `COMMAND_ORDER` +2 (`local-service-policy-run/-report`), the
  entry-switch absorbing case moved `executor.count` 28→29 with its blocker string, and the
  coverage-suite literal counts became `REVIEWED_RELEASE_PARENT_COUNT - 2 / - 1` dynamics.
- Regression (strict-subset gate): pytest at frozen base `eda01324d` = 20 failed / 2988
  passed / 220 skipped; working tree = 20 failed / 3015 passed / 220 skipped; failure ID
  sets identical (13 unique, zero new). unittest: base `failures=5, errors=15, skipped=220`;
  working tree identical counts and identical failure name set.
- Standalone fixture hygiene: six orphan processes (five OpenClaw profile daemons plus one
  `target/debug/bcs`, all `ppid=1`, started 2026-10-29 20:37–22:45 inside this worktree's
  `.standalone-openclaw/`) were terminated; they pinned load ~6 on this box and produced
  timing flakes in the judge-sandbox suite (green twice after cleanup).

## Slice-31 disposition: story_authority_user_journey (2026-10-30)

- Promoted `story_authority_user_journey` out of `KNOWN_UNMAPPED_LEGACY_IDS` and raised
  `REVIEWED_RELEASE_PARENT_COUNT` 30→31: the owned-local Bot-authority user journey becomes
  the 31st reviewed release parent slice (`LOCAL-AUTHORITY-JOURNEY-01`, suite
  `owned-local-authority-v1`, 16 frozen assertions / 24 frozen HTTP calls, binary kind `bcs`,
  sha-256-locked to `dcbc39f07cedb62c8764987caa9fb05983dd7b3b1e6185022281cf0daa73d8d3`,
  environment `local=LOCAL_EXECUTOR_ONLY` / `preprod=REMOTE_UNSUPPORTED`,
  `independent_slice=false`, `release_parent_denominator=true`, source map
  `authority-migration-contract.json`; upstream frozen at `authority.sh` git tip
  `c6f00120949078f14cb0d3602fb8301f2ccb1803`, file sha256
  `62c1f09fd74fc881edb191a827c9e2d91e11afd38980fd83c5333828371dd69a`, worktree clean).
- Registration: `local-authority-run`/`local-authority-report` commands with a report
  output-dir overwrite guard, `CaseBinding LOCAL-AUTHORITY-JOURNEY-01`, migration-inventory
  `authority_slice` plus the `authority` source binding and executor/source-map entries, and
  the junit/markdown requirement projection suite-set additions (`owned-local-authority-v1`).
- Artifact allowance 324→332: the committed Edge backlog executor (+1 flat module), the
  authority slice's four flat modules (+4), and three slots reserved for the concurrent
  provider-owner promotion (+3); the historical allowance annotations stay untouched.
- Semantic-projection bug found by the offline fence and fixed: the owned adapter wrote
  unrendered `{...}` path templates into the INTENDED operation rows (the 24 call rows were
  rendered correctly), so live-1 passed its run fence (`LOCAL_AUTHORITY_PASS`,
  `http_call_count=24`, 16/16 requirements, fixture bots retained, STOPPED rc=0) while
  `local-authority-report` correctly BLOCKED on 12 row `path` mismatches; `_begin_op` now
  resolves identity tokens before publishing the INTENDED row, and the semantic-variant guard
  grows the `unrendered-row-path-template` negative to pin that fence. Live rerun
  `20261030-authority-live-2` passes both fences (run `LOCAL_AUTHORITY_PASS`; official offline
  rendering `LOCAL_SUITE_PASS`, integrity PASS, rc=0; evidence under
  `~/.codex/bcs-e2e/e2e-dev-fresh-authority-live-2/` and
  `~/.codex/bcs-e2e/e2e-dev-fresh-authority-live-2-reports-offline/`; executor digest
  `18feb847a317bb9839de8f75a97ee8d1f13fe83c9574b00ff019bffc7218bd10` frozen by the run and
  re-derived identically; credentials `NOT_CAPTURED`, journal forbidden-substring scan clean).
  Live-1 is retained as the BLOCKED-discovery evidence trail.
- Focused unittests: 128 ran, OK (4 AuthorityLive environment-gated skips); shared count
  sweeps landed in the entry-switch, migration-inventory, story-command, service-policy and
  custom-collaboration tests and in the staged provider-owner canary fence (30→31); that
  canary currently cannot import because the concurrent provider-owner slice has not yet
  promoted its contract modules into `bcs_e2e/` by its own landing recipe - not an authority
  regression.
- Gates: upstream drift (git tip + file sha256 + contract identity) PASS; count gate 31/31
  `CASES` READY against `REVIEWED_RELEASE_PARENT_COUNT=31`; secret gate clean (journal
  forbidden-substring scan, staged-diff credential grep, strict mock-header admission record
  per run). SOP deviation recorded: this freeze-review entry was appended after live-2 so the
  document carries real results; every other SOP step ran in its documented order.
