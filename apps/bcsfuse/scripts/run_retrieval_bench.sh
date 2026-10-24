#!/usr/bin/env bash
# Sequential storage runs. Never update data, deploy, or inject faults.
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
k6_bin="${K6_BIN:-k6}"
: "${BASE_URL:?Set BASE_URL to one pinned prepub instance}"
if [[ -z "${BCSFUSE_BENCH_TOKEN:-}" && -z "${BCSFUSE_BENCH_COOKIE:-}" ]]; then
  echo 'Set BCSFUSE_BENCH_COOKIE for the existing gateway, or BCSFUSE_BENCH_TOKEN for bearer mode' >&2
  exit 2
fi
: "${QUERIES_FILE:?Set QUERIES_FILE to an absolute path containing representative queries}"
if [[ "$QUERIES_FILE" != /* || ! -f "$QUERIES_FILE" ]]; then
  echo 'QUERIES_FILE must be an existing absolute path' >&2
  exit 2
fi
command -v "$k6_bin" >/dev/null || { echo 'Install k6 or set K6_BIN' >&2; exit 2; }

read -r -a rates <<< "${RATES:-10}"
read -r -a modes <<< "${MODES:-vector keyword both}"
for rate in "${rates[@]}"; do
  [[ "$rate" =~ ^[1-9][0-9]*$ ]] || { echo "Invalid rate: $rate" >&2; exit 2; }
done
for mode in "${modes[@]}"; do
  case "$mode" in vector|keyword|both) ;; *) echo "Invalid mode: $mode" >&2; exit 2 ;; esac
done
if [[ ${#rates[@]} -eq 0 || ${#modes[@]} -eq 0 ]]; then
  echo 'RATES and MODES must not be empty' >&2
  exit 2
fi

export BASE_URL QUERIES_FILE
if [[ -n "${BCSFUSE_BENCH_TOKEN:-}" ]]; then export BCSFUSE_BENCH_TOKEN; fi
if [[ -n "${BCSFUSE_BENCH_COOKIE:-}" ]]; then export BCSFUSE_BENCH_COOKIE; fi
export DURATION="${DURATION:-30s}"
result_dir="${RESULT_DIR:-${TMPDIR:-/tmp}/bcsfuse-bench-$(date +%Y%m%d-%H%M%S)-$$}"
# Refuse to overwrite evidence from an earlier run.
mkdir "$result_dir"
printf 'mode,offered_qps,duration,exit_code,summary,log\n' > "$result_dir/runs.csv"
printf 'Results: %s\n' "$result_dir"
for mode in "${modes[@]}"; do
  for rate in "${rates[@]}"; do
    stem="$mode-$rate"
    summary="$result_dir/$stem.json"
    run_log="$result_dir/$stem.log"
    printf 'Running mode=%s rate=%s duration=%s\n' "$mode" "$rate" "$DURATION"
    result=0
    MODE="$mode" RATE="$rate" "$k6_bin" run --summary-export "$summary" \
      "$script_dir/retrieval_bench.js" > "$run_log" 2>&1 || result=$?
    printf '%s,%s,%s,%s,%s,%s\n' "$mode" "$rate" "$DURATION" "$result" \
      "$stem.json" "$stem.log" >> "$result_dir/runs.csv"
    tail -35 "$run_log"
    if [[ "$result" -ne 0 ]]; then
      printf 'Stopped after failure. Inspect %s before increasing load.\n' "$run_log" >&2
      exit "$result"
    fi
  done
done
