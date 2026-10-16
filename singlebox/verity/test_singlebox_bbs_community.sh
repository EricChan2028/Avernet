#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
WORK="$(mktemp -d)"
export LOG_DIR="$WORK"
export PROJECT_ROOT="$ROOT"
source "$ROOT/singlebox/modules/bbs_community.sh"
trap 'rm -rf "$WORK"' EXIT

fail() {
    printf 'FAIL: %s\n' "$*" >&2
    exit 1
}
assert_eq() {
    [ "$1" = "$2" ] || fail "expected '$2', got '$1'"
}
assert_not_exists() {
    [ ! -e "$1" ] || fail "unexpected file: $1"
}
assert_count() {
    local actual
    actual="$(grep -E -c "$2" "$1")"
    assert_eq "$actual" "$3"
}

BOTS_PROFILE_DIR=""
bbs_community_start
assert_not_exists "$WORK/bbs_community.log"

BOTS_PROFILE_DIR="singlebox/agents/2bots_bbs_community_profile"

for profile in community-product-officer community-engineering-officer; do
    for skill in bbs-browse bbs-topic-handler; do
        resolver="$ROOT/$BOTS_PROFILE_DIR/$profile/skills/$skill/scripts/resolve_bot_identity.py"
        [ -f "$resolver" ] || fail "missing identity resolver: $resolver"
    done
done

backend_ready() { return 1; }
if bbs_community_start 2>/dev/null; then
    fail "BBS community initialization accepted without backend"
fi

backend_ready() { return 0; }
bbs_community_ensure_product_bot() {
    case "$1" in
        community-product-officer) printf '%s' 'bot-product' ;;
        community-engineering-officer) printf '%s' 'bot-engineering' ;;
        *) return 1 ;;
    esac
}
bbs_community_request() {
    printf '%s\n' "$2" >> "$WORK/requests.log"
    printf '%s' '{"code":201000,"data":{"topic_id":"topic_1"}}'
}
bbs_community_seed_topics
assert_eq "$(grep -c '^/api/v1/collaboration/tasks/bbs/topics$' "$WORK/requests.log")" 5

bcs_bot_profile_dir() { printf '%s\n' "$1"; }
bots_session_bot_uuid() {
    local profile="${1%%/.bcs/*}"
    printf 'bot-%s' "$profile"
}
bbs_community_request() {
    printf '%s\n' "$2" >> "$WORK/subscription-paths.log"
    printf '%s' '{"code":200000,"data":{"bot_id":"bot-product"}}'
}
bbs_community_seed_subscriptions
assert_eq "$(sed -n '1p' "$WORK/subscription-paths.log")" \
    '/api/v1/collaboration/tasks/bots/bot-product/bbs/browse-subscription?owner_user_id=mock-user'
assert_eq "$(sed -n '2p' "$WORK/subscription-paths.log")" \
    '/api/v1/collaboration/tasks/bots/bot-engineering/bbs/browse-subscription?owner_user_id=mock-user'

gateway_ready() { return 0; }
bbs_community_gateway_login() { :; }
bbs_community_ensure_product_bot() {
    case "$1" in
        community-product-officer) printf '%s' 'bot-product' ;;
        community-engineering-officer) printf '%s' 'bot-engineering' ;;
        *) return 1 ;;
    esac
}
bbs_community_create_skill_zip() { : > "$2"; }
bbs_community_gateway_request() {
    local method="$1"
    local path="$2"

    local skill_name
    printf '%s %s\n' "$method" "$path" >>"$WORK/skill-requests.log"

    if [ "$method" = GET ] && [[ "$path" == *'/routines?user_id='* ]]; then
        if [ "${BBS_COMMUNITY_TEST_ROUTINES_EMPTY:-0}" = 1 ]; then
            printf '%s' '{"code":200000,"data":{"total":0,"items":[]}}'
        else
            printf '%s' '{"code":200000,"data":{"total":1,"items":[{"routine_id":"cron-1","name":"bbs-browse-loop","trigger":{"type":"schedule","cron":"*/30 * * * *"},"command":"run once","enabled":true,"timezone":"Asia/Shanghai"}]}}'
        fi
    elif [ "$method" = GET ] && [[ "$path" == *'/skill-sets/resources?user_id='* ]]; then
        printf '%s' '{"code":200000,"data":[{"id":"set-1","name":"BBS Demo 能力集","is_default":false,"is_active":true,"mcps":[],"clis":[]}]}'
    elif [ "$method" = GET ] && [[ "$path" == *'/skill-sets?user_id='* ]]; then
        printf '%s' '{"code":200000,"data":[]}'
    elif [ "$method" = GET ] && [[ "$path" == *'/skills/101/content?user_id='* ]]; then
        printf '%s' '{"code":200000,"data":{"content":"# BBS Browse\nreal seeded skill\n"}}'
    elif [ "$method" = POST ] && [[ "$path" == *'/skill-sets?user_id='* ]]; then
        printf '%s' '{"code":201000,"data":{"id":"set-1","name":"BBS Demo 能力集","is_default":false,"is_active":true}}'
    elif [ "$method" = GET ] && [[ "$path" == *'/skill-sets/'*'/skills?user_id='* ]]; then
        if [ "${BBS_COMMUNITY_SEED_IN_PROGRESS:-0}" = 1 ]; then
            printf '%s' '{"code":200000,"data":[]}'
        else
            printf '%s' '{"code":200000,"data":['
            for skill_name in "${BBS_COMMUNITY_SKILL_NAMES[@]}"; do
                if [ "${BBS_COMMUNITY_SKILL_SET_DROP_LAST:-0}" = 1 ] && [ "$skill_name" = "${BBS_COMMUNITY_SKILL_NAMES[${#BBS_COMMUNITY_SKILL_NAMES[@]}-1]}" ]; then
                    continue
                fi
                printf '{"skill_id":"101","name":"%s"},' "$skill_name"
            done
            printf '{}]}'
        fi
    elif [ "$method" = POST ] && [[ "$path" == */skills?user_id=* ]]; then
        printf '%s' '{"code":201000,"data":{"skill":{"skill_id":"101","name":"seeded","active":false}}}'
    elif [ "$method" = PUT ] && [[ "$path" == *'/skill-sets/'*'/skills/'*'?'* ]]; then
        printf '%s' '{"code":200000,"data":{"changed":true}}'
    else
        fail "unexpected gateway request: ${method} ${path}"
    fi
}
BBS_COMMUNITY_SEED_IN_PROGRESS=1
bbs_community_seed_skills
unset BBS_COMMUNITY_SEED_IN_PROGRESS
assert_count "$WORK/skill-requests.log" '^GET /openapi/v1/bots/.*/skill-sets\?user_id=' 2
assert_count "$WORK/skill-requests.log" '^POST /openapi/v1/bots/.*/skill-sets\?user_id=' 2
assert_count "$WORK/skill-requests.log" '^GET /openapi/v1/bots/.*/skill-sets/.*/skills\?user_id=' 2
assert_count "$WORK/skill-requests.log" '^POST /openapi/v1/bots/.*/skills\?user_id=' 6
assert_count "$WORK/skill-requests.log" '^GET /openapi/v1/bots/.*/skills/101/content\?user_id=' 6
assert_count "$WORK/skill-requests.log" '^PUT /openapi/v1/bots/.*/skill-sets/.*/skills/.*\?user_id=' 6
assert_eq "$(find "$WORK" -name 'bbs-skill.*.zip' | wc -l | tr -d ' ')" 0
assert_eq "$(find "$WORK" -name 'bbs-json.*' | wc -l | tr -d ' ')" 0

bbs_community_request() {
    case "$2" in
        *'/bbs/topics'*)
            printf '%s' '{"code":200000,"data":{"total":5}}'
            ;;
        *'/bbs/browse-subscriptions'*)
            printf '%s' '{"code":200000,"data":{"total":2}}'
            ;;
    esac
}
if ! bbs_community_ready; then
    fail "BBS community readiness rejected a complete initialization"
fi
assert_count "$WORK/skill-requests.log" '^GET /openapi/v1/bots/.*/routines\?user_id=' 2

BBS_COMMUNITY_TEST_ROUTINES_EMPTY=1
if bbs_community_ready; then
    fail "BBS community readiness accepted a missing routine"
fi
unset BBS_COMMUNITY_TEST_ROUTINES_EMPTY

bbs_community_request() {
    case "$2" in
        *'/bbs/topics'*)
            printf '%s' '{"code":200000,"data":{"total":5}}'
            ;;
        *'/bbs/browse-subscriptions'*)
            printf '%s' '{"code":200000,"data":{"total":1}}'
            ;;
    esac
}
if bbs_community_ready; then
    fail "BBS community readiness accepted an incomplete subscription set"
fi

bbs_community_request() {
    case "$2" in
        *'/bbs/topics'*)
            printf '%s' '{"code":200000,"data":{"total":5}}'
            ;;
        *'/bbs/browse-subscriptions'*)
            printf '%s' '{"code":200000,"data":{"total":2}}'
            ;;
    esac
}
BBS_COMMUNITY_SKILL_SET_DROP_LAST=1
if bbs_community_ready; then
    fail "BBS community readiness accepted an incomplete skill set"
fi

printf 'PASS: singlebox BBS community module tests\n'
