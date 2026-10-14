#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
WORK="$(mktemp -d)"
export LOG_DIR="$WORK"
source "$ROOT/singlebox/modules/bbs_community.sh"
trap 'rm -rf "$WORK"' EXIT

BOTS_PROFILE_DIR=""
bbs_community_start
[[ ! -f "$WORK/bbs_community.log" ]]

BOTS_PROFILE_DIR="singlebox/agents/2bots_bbs_community_profile"
backend_ready() { return 1; }
if bbs_community_start 2>/dev/null; then
    echo "BBS community initialization accepted without backend" >&2
    exit 1
fi

backend_ready() { return 0; }
bbs_community_request() {
    printf '%s\n' "$2" >> "$WORK/requests.log"
    printf '%s' '{"code":201000,"data":{"topic_id":"topic_1"}}'
}
bbs_community_seed_topics
[[ "$(grep -c '^/api/v1/collaboration/tasks/bbs/topics$' "$WORK/requests.log")" -eq 5 ]]

bcs_bot_profile_dir() { printf '%s/%s' "$WORK" "$1"; }
bots_session_bot_uuid() { printf 'bot-%s' "$1"; }
request_path=""
bbs_community_request() {
    request_path="$2"
    printf '%s' '{"code":200000,"data":{"bot_id":"bot-product"}}'
}
bbs_community_seed_subscriptions
[[ "$request_path" == '/api/v1/collaboration/tasks/bots/bot-community-product-officer/bbs/browse-subscription?owner_user_id=mock-user' ]]

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
    echo 'BBS community readiness rejected a complete initialization' >&2
    exit 1
fi

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
    echo 'BBS community readiness accepted an incomplete subscription set' >&2
    exit 1
fi

printf 'PASS: singlebox BBS community module tests\n'
