#!/usr/bin/env python3
"""Stdlib-only, bounded fixed-rate driver for one pinned BCSFuse process."""

import argparse
import http.client
import json
import math
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, wait
from pathlib import Path
from urllib.parse import urlsplit

QUERIES = [
    "告警",
    "缺陷",
    "文档",
    "财务分析",
    "风控",
    "数据分析",
    "前端开发",
    "查询未解决的缺陷",
]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["prepare", "run"])
    parser.add_argument("--base", default="http://127.0.0.1:8765")
    parser.add_argument("--backend", choices=["local", "server"], default="local")
    parser.add_argument("--mode", choices=["keyword", "vector"], default="keyword")
    parser.add_argument(
        "--filters", default="{}", help="JSON object of prefilter conditions"
    )
    parser.add_argument(
        "--filter-fields",
        default="{}",
        help="prepare: JSON field -> keyword/integer/bool",
    )
    parser.add_argument("--queries-file", type=Path)
    parser.add_argument("--keyword-only", action="store_true")
    parser.add_argument("--local-only", action="store_true")
    parser.add_argument("--qps", type=float, default=1)
    parser.add_argument("--seconds", type=int, default=30)
    parser.add_argument("--concurrency", type=int, default=8)
    parser.add_argument("--top-k", type=int, default=10)
    args = parser.parse_args()
    if (
        not 0 < args.qps <= 10000
        or not 1 <= args.concurrency <= 128
        or not 1 <= args.seconds <= 3600
    ):
        parser.error("qps: (0,10000], concurrency: 1..128, seconds: 1..3600")
    base = urlsplit(args.base)
    if (
        base.scheme not in {"http", "https"}
        or not base.hostname
        or base.username
        or base.query
    ):
        parser.error("base must be an HTTP(S) URL without credentials or query")
    headers = {"Content-Type": "application/json"}
    if os.getenv("BCSFUSE_BENCH_TOKEN"):
        headers["Authorization"] = "Bearer " + os.environ["BCSFUSE_BENCH_TOKEN"]
    if os.getenv("BCSFUSE_BENCH_COOKIE"):
        headers["Cookie"] = os.environ["BCSFUSE_BENCH_COOKIE"]
    local = threading.local()
    connections = []
    guard = threading.Lock()

    def request(path, body=None, timeout=10):
        if not getattr(local, "connection", None):
            cls = (
                http.client.HTTPSConnection
                if base.scheme == "https"
                else http.client.HTTPConnection
            )
            local.connection = cls(base.hostname, base.port, timeout=timeout)
            with guard:
                connections.append(local.connection)
        conn = local.connection
        conn.timeout = timeout
        if conn.sock:
            conn.sock.settimeout(timeout)
        conn.request(
            "GET" if body is None else "POST",
            base.path.rstrip("/") + "/internal/bench/" + path,
            body=None if body is None else json.dumps(body),
            headers=headers,
        )
        response = conn.getresponse()
        raw = response.read()
        if response.status != 200:
            raise RuntimeError("HTTP " + str(response.status))
        return json.loads(raw)

    try:
        info = request("info")
        if info.get("search_scope") != "direct_qdrant":
            raise RuntimeError("Old benchmark deployed: not direct_qdrant")
        if args.action == "prepare":
            fields = json.loads(args.filter_fields)
            if not info["dataset_ready"]:
                dataset = request(
                    "dataset",
                    {"include_server": not args.local_only, "filter_fields": fields},
                    300,
                )
                if dataset["instance_id"] != info["instance_id"]:
                    raise RuntimeError("Instance changed; pin the target")
            elif fields != info["filter_fields"] or (
                not args.local_only and not info["server_ready"]
            ):
                raise RuntimeError(
                    "Existing dataset has different settings; restart before preparing another"
                )
            queries = (
                json.loads(args.queries_file.read_text())
                if args.queries_file
                else QUERIES
            )
            samples = request(
                "samples",
                {"queries": queries, "with_vectors": not args.keyword_only},
                300,
            )
            if samples["instance_id"] != info["instance_id"]:
                raise RuntimeError("Instance changed; pin the target")
            print(json.dumps(request("info"), ensure_ascii=False, indent=2))
            return
        filters = json.loads(args.filters)
        if not info["dataset_ready"] or not info["sample_count"]:
            raise RuntimeError("Run prepare first")
        if args.backend == "server" and not info["server_ready"]:
            raise RuntimeError("Server dataset not ready")
        if any(key not in info["filter_fields"] for key in filters):
            raise RuntimeError("Filter field was not declared during preparation")
        if args.mode == "vector" and info["sample_source"] != "configured_embedding":
            raise RuntimeError("Prepare real query embeddings first")

        server_before = request("server-info") if args.backend == "server" else {}
        if server_before and (
            server_before["instance_id"] != info["instance_id"]
            or server_before["dataset_id"] != info["dataset_id"]
        ):
            raise RuntimeError("Server status came from another dataset or instance")

        def one(index):
            started = time.perf_counter()
            result = request(
                "search",
                {
                    "backend": args.backend,
                    "mode": args.mode,
                    "top_k": args.top_k,
                    "sample_set_id": info["sample_set_id"],
                    "sample_index": index % info["sample_count"],
                    "filters": filters,
                },
            )
            for key in ["instance_id", "dataset_id", "sample_set_id"]:
                if result[key] != info[key]:
                    raise RuntimeError(key + " changed; stop test")
            if (
                result.get("backend") != args.backend
                or result.get("search_scope") != "direct_qdrant"
            ):
                raise RuntimeError("Wrong benchmark backend or scope")
            return {
                "http": (time.perf_counter() - started) * 1000,
                **result["timing_ms"],
                "empty": not result[args.mode + "_hits"],
            }

        rows, errors, pending = [], {}, set()
        attempted = skipped = 0

        def collect(done):
            for future in done:
                try:
                    rows.append(future.result())
                except (
                    RuntimeError,
                    OSError,
                    ValueError,
                    KeyError,
                    http.client.HTTPException,
                ) as error:
                    label = (
                        str(error)
                        if isinstance(error, RuntimeError)
                        else type(error).__name__
                    )
                    errors[label] = errors.get(label, 0) + 1

        started = time.perf_counter()
        with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
            for index in range(math.ceil(args.qps * args.seconds)):
                deadline = started + index / args.qps
                if deadline > time.perf_counter():
                    time.sleep(deadline - time.perf_counter())
                done = {future for future in pending if future.done()}
                pending -= done
                collect(done)
                if errors:
                    break
                if (
                    len(pending) >= args.concurrency
                    or time.perf_counter() - deadline > 1 / args.qps
                ):
                    skipped += 1
                    continue
                pending.add(pool.submit(one, index))
                attempted += 1
            if pending:
                collect(wait(pending)[0])
        if not errors:
            remaining = args.seconds - (time.perf_counter() - started)
            if remaining > 0:
                time.sleep(remaining)
        elapsed = time.perf_counter() - started

        def percentiles(key):
            values = sorted(row[key] for row in rows)
            return (
                {
                    f"p{p}": round(values[math.ceil(len(values) * p / 100) - 1], 2)
                    for p in [50, 95, 99]
                }
                if values
                else {}
            )

        server_after = {}
        if args.backend == "server" and not errors:
            try:
                # The control connection was idle throughout the load phase.
                # Reconnect explicitly; do not retry measured search requests.
                local.connection.close()
                server_after = request("server-info")
                if (
                    server_after["instance_id"] != info["instance_id"]
                    or server_after["dataset_id"] != info["dataset_id"]
                ):
                    errors["server status identity changed"] = 1
            except (
                RuntimeError,
                OSError,
                ValueError,
                KeyError,
                http.client.HTTPException,
            ):
                errors["server status unavailable after run"] = 1
        print(
            json.dumps(
                {
                    "server_index_before": server_before,
                    "server_index_after": server_after,
                    "instance_id": info["instance_id"],
                    "dataset_id": info["dataset_id"],
                    "sample_set_id": info["sample_set_id"],
                    "backend": args.backend,
                    "mode": args.mode,
                    "filters": filters,
                    "vector_count": info["vector_count"],
                    "target_qps": args.qps,
                    "elapsed_s": round(elapsed, 2),
                    "attempted": attempted,
                    "success": len(rows),
                    "success_qps": round(len(rows) / elapsed, 2),
                    "skipped_slots": skipped,
                    "errors": errors,
                    "model_calls": 0,
                    "empty_results": sum(row["empty"] for row in rows),
                    "http_ms": percentiles("http"),
                    "store_ms": percentiles("store"),
                    "dispatch_ms": percentiles("dispatch_wait"),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        if errors or skipped:
            raise SystemExit(1)
    finally:
        for conn in connections:
            conn.close()


if __name__ == "__main__":
    main()
