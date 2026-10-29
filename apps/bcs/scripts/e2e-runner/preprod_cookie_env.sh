#!/usr/bin/env bash
# Preprod credential loader (sourced, never executed as a child).
#
# Contract (bcs-multi-environment-e2e, preprod credential agreement):
#   - The preprod human credential lives in ONE local file, default
#     "$HOME/.codex/bcs-e2e/preprod-human-cookie.txt", outside the
#     repository, never committed, never logged, never reported.
#   - The file holds exactly ONE line: the raw Cookie header value
#     (the environment variable referenced by the preprod Profile's
#     auth.header_env). No JSON wrapper, no key names, no commentary.
#   - This loader NEVER deletes, rewrites, or truncates the file.
#     It must survive until the cookie expires; deletion is a
#     human decision after expiry, not a runner or cleanup action.
#
# Usage (from the e2e-runner directory):
#   source ./preprod_cookie_env.sh [ENV_NAME] [COOKIE_FILE]
#     ENV_NAME   default BCS_E2E_HUMAN_COOKIE
#                (must match the profile auth.header_env reference;
#                 use BCS_E2E Human_TOKEN-style names only if the
#                 profile references Authorization instead)
#     COOKIE_FILE default "$HOME/.codex/bcs-e2e/preprod-human-cookie.txt"
#
# Environment: bash or zsh. The export only persists because this
# script is sourced; nothing here runs the runner by itself.

_preprod_cookie_env_load() {
  local env_name="${1:-BCS_E2E_HUMAN_COOKIE}"
  local cookie_file="${2:-$HOME/.codex/bcs-e2e/preprod-human-cookie.txt}"

  case "$env_name" in
    ''|*[!A-Z0-9_]*|[0-9]*|??|?) ;;
    *) : ;;
  esac
  if [[ ! "$env_name" =~ ^[A-Z][A-Z0-9_]{2,100}$ ]]; then
    printf 'preprod cookie: invalid environment name %q\n' "$env_name" >&2
    return 1
  fi

  if [[ -L "$cookie_file" || ! -f "$cookie_file" ]]; then
    printf 'preprod cookie: file not found: %s\n' "$cookie_file" >&2
    printf '  drop the single-line Cookie header value there; the file is never deleted by the runner\n' >&2
    return 1
  fi

  local newlines
  newlines="$(wc -l < "$cookie_file" | tr -d '[:space:]')"
  if [[ "$newlines" -gt 1 ]]; then
    printf 'preprod cookie: file must be a single line: %s\n' "$cookie_file" >&2
    return 1
  fi

  local cookie
  cookie="$(cat "$cookie_file")"
  if [[ -z "$cookie" ]]; then
    printf 'preprod cookie: file is empty: %s\n' "$cookie_file" >&2
    return 1
  fi
  case "$cookie" in
    *$'\n'*|*$'\r'*)
      printf 'preprod cookie: embedded newline in credential: %s\n' "$cookie_file" >&2
      return 1
      ;;
  esac
  case "$cookie" in
    *[[:cntrl:]]*)
      printf 'preprod cookie: control characters in credential: %s\n' "$cookie_file" >&2
      return 1
      ;;
  esac

  export "$env_name=$cookie"
  printf 'preprod cookie: %s loaded from %s (file left in place; do-not-delete until cookie expiry)\n' \
    "$env_name" "$cookie_file" >&2
}

_preprod_cookie_env_load "$@"
_preprod_cookie_rc=$?
unset -f _preprod_cookie_env_load
# Propagate the load result to the sourcing shell without
# letting the cleanup unset clobber it.
return "$_preprod_cookie_rc" 2>/dev/null || exit "$_preprod_cookie_rc"
