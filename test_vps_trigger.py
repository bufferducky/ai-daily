"""通过本地 HTTP 假服务，验证实际 Bash 脚本的查询、触发和失败处理。"""

import json
import os
import subprocess
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


SCRIPT = Path(__file__).parent / "deploy" / "vps" / "trigger.sh"


class TriggerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.requests = []
        self.artifacts = []
        self.runs = []
        self.get_status = 200
        self.post_status = 204
        case = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                case.requests.append(("GET", self.path, None))
                self.send_response(case.get_status)
                self.end_headers()
                body = {"artifacts": case.artifacts} if "/artifacts?" in self.path else {"workflow_runs": case.runs}
                self.wfile.write(json.dumps(body).encode())

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                case.requests.append(("POST", self.path, body))
                self.send_response(case.post_status)
                self.end_headers()

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.config = self.root / "github.curl"
        self.config.write_text('header = "Authorization: Bearer test-only"\n')
        self.bin = self.root / "bin"
        self.bin.mkdir()
        date = self.bin / "date"
        date.write_text("""#!/bin/bash
case "$1" in
  +%F) echo 2026-11-20 ;;
  +%H%M) echo "${TEST_HHMM:-0900}" ;;
  +%s) echo 1795136400 ;;
  *) /bin/date "$@" ;;
esac
""")
        date.chmod(0o755)
        self.env = dict(os.environ, PATH=str(self.bin) + os.pathsep + os.environ["PATH"],
                        AI_DAILY_API_URL=f"http://127.0.0.1:{self.server.server_port}",
                        AI_DAILY_STATE_DIR=str(self.root), AI_DAILY_CONFIG_FILE=str(self.config))

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temp.cleanup()

    def run_trigger(self, *args):
        return subprocess.run(["/bin/bash", str(SCRIPT), *args], env=self.env,
                              capture_output=True, text=True, timeout=10)

    def test_dispatches_automatic_mode_and_cools_down(self):
        first = self.run_trigger()
        self.assertEqual(first.returncode, 0, first.stderr)
        posts = [request for request in self.requests if request[0] == "POST"]
        self.assertEqual(len(posts), 1)
        self.assertEqual(posts[0][2], {"ref": "main", "inputs": {
            "automatic": True, "force_notify": False, "dry_run": False,
        }})
        second = self.run_trigger()
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(len([request for request in self.requests if request[0] == "POST"]), 1)

    def test_receipt_stops_day_and_is_cached(self):
        self.artifacts = [{"expired": False}]
        result = self.run_trigger()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.root / "notified.date").read_text().strip(), "2026-11-20")
        self.requests.clear()
        self.assertEqual(self.run_trigger().returncode, 0)
        self.assertEqual(self.requests, [])

    def test_active_or_queued_run_prevents_duplicate_dispatch(self):
        for status in ["queued", "in_progress", "waiting", "pending", "requested"]:
            with self.subTest(status=status):
                self.runs = [{"status": status}]
                result = self.run_trigger()
                self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(any(request[0] == "POST" for request in self.requests))

    def test_failed_run_and_expired_receipt_allow_retry(self):
        self.runs = [{"status": "completed", "conclusion": "failure"}]
        self.artifacts = [{"expired": True}]
        self.assertEqual(self.run_trigger().returncode, 0)
        self.assertTrue(any(request[0] == "POST" for request in self.requests))

    def test_api_error_fails_closed(self):
        self.get_status = 403
        result = self.run_trigger()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any(request[0] == "POST" for request in self.requests))

    def test_failed_dispatch_does_not_write_success_or_cooldown(self):
        self.post_status = 422
        self.assertNotEqual(self.run_trigger().returncode, 0)
        self.assertFalse((self.root / "last-dispatch").exists())
        self.assertFalse((self.root / "notified.date").exists())

    def test_outside_window_makes_no_requests(self):
        for time in ["0829", "1000", "1600"]:
            self.env["TEST_HHMM"] = time
            self.assertEqual(self.run_trigger().returncode, 0)
        self.assertEqual(self.requests, [])

    def test_check_only_outside_window_never_dispatches_or_changes_state(self):
        self.env["TEST_HHMM"] = "1600"
        self.assertEqual(self.run_trigger("--check-only").returncode, 0)
        self.assertEqual(len(self.requests), 2)
        self.assertFalse(any(request[0] == "POST" for request in self.requests))
        self.assertFalse((self.root / "last-dispatch").exists())


if __name__ == "__main__":
    unittest.main()
