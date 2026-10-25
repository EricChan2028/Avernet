import json
import subprocess
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

SCRIPT = str(Path(__file__).resolve().parents[2] / "scripts/qdrant_compare_bench.py")
IDENTITY = {"instance_id": "test", "dataset_id": "snapshot", "sample_set_id": "queries"}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def do_GET(self):
        result = dict(IDENTITY)
        if self.path.endswith("/info"):
            result.update(
                search_scope="direct_qdrant",
                dataset_ready=True,
                sample_count=1,
                server_ready=True,
                backends=["local", "server", "server_grpc"],
                filter_fields={},
                sample_source="configured_embedding",
                vector_count=10,
            )
        else:
            result.update(status="green", points_count=10)
        self.reply(result)
        if self.path.endswith("/server-info"):
            # Simulate an idle keep-alive connection being closed by the server.
            self.close_connection = True

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.reply(
            dict(
                IDENTITY,
                backend=body["backend"],
                search_scope="direct_qdrant",
                keyword_hits=[{"id": "a"}],
                timing_ms={"store": 1, "dispatch_wait": 0},
            )
        )

    def reply(self, result):
        raw = json.dumps(result).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)
        self.wfile.flush()


class IdleConnectionTest(unittest.TestCase):
    def test_after_run_status_uses_fresh_connection(self):
        self.check_backend("server")

    def test_grpc_driver_checks_same_dataset_before_and_after_run(self):
        self.check_backend("server_grpc")

    def check_backend(self, backend):
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            proc = subprocess.run(
                [
                    sys.executable,
                    SCRIPT,
                    "run",
                    "--backend",
                    backend,
                    "--base",
                    f"http://127.0.0.1:{server.server_port}",
                    "--qps",
                    "1",
                    "--seconds",
                    "1",
                    "--concurrency",
                    "1",
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            result = json.loads(proc.stdout)
            self.assertEqual(result["success"], 1)
            self.assertEqual(result["errors"], {}, proc.stdout + proc.stderr)
            self.assertEqual(result["server_index_after"]["status"], "green")
            self.assertEqual(proc.returncode, 0)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == "__main__":
    unittest.main()
