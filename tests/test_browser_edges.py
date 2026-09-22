"""Local browser regressions for scope receipts, recovered retries and app shells."""

from collections import Counter
import http.server
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest


REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"
NODE = shutil.which("node")
VALID = ('<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Local edge test</title>'
         '</head><body><main><h1>Browser edge test</h1><p>Visible content ready for auditing.</p></main></body></html>')
SHELL = ('<!doctype html><html lang="en"><head><title>Application loading</title></head><body>'
         '<main id="app"><div><div><div><div><div></div></div></div></div></div></main>')
DELAYED = (SHELL + '<script>setTimeout(function(){document.querySelector("#app").innerHTML='
           '\'<h1>Loaded application</h1><button id="late-action">Open details</button>\';},1800);</script></body></html>')


class EdgeHandler(http.server.BaseHTTPRequestHandler):
    counts = Counter()
    lock = threading.Lock()

    def log_message(self, *_args):
        pass

    def do_GET(self):
        with self.lock:
            self.counts[self.path] += 1
            count = self.counts[self.path]
        status, mime, body = 200, "text/html; charset=utf-8", VALID
        if self.path == "/missing-mime":
            mime = None
        elif self.path == "/malformed-mime":
            mime = "text/html-invalid"
        elif self.path == "/mime-parameter":
            mime = "text/plain; note=text/html"
        elif self.path == "/shell":
            body = SHELL + "</body></html>"
        elif self.path == "/delayed-shell":
            body = DELAYED
        elif self.path in ("/recover", "/recover-cli") and count == 1:
            status, body = 503, "<html><body>Temporary fixture failure</body></html>"
        payload = body.encode("utf-8")
        self.send_response(status)
        if mime is not None:
            self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        try:
            self.wfile.write(payload)
        except (BrokenPipeError, ConnectionResetError):
            pass


class BrowserEdgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.env = dict(os.environ, PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1")
        cache = Path(os.environ.get("A11Y_AUDITOR_CACHE", str(REPO / ".runtime")))
        if "PLAYWRIGHT_BROWSERS_PATH" not in cls.env and (cache / "browsers").exists():
            cls.env["PLAYWRIGHT_BROWSERS_PATH"] = str(cache / "browsers")
        if not NODE:
            raise unittest.SkipTest("Node is unavailable; browser edges were not tested")
        check = subprocess.run(
            [NODE, "-e", "require('axe-core');require('playwright').chromium.launch({headless:true})"
             ".then(b=>b.close()).catch(e=>{console.error(e.message);process.exit(1)})"],
            cwd=REPO, env=cls.env, text=True, capture_output=True, timeout=25,
        )
        if check.returncode:
            raise unittest.SkipTest("Browser runtime unavailable; edges not tested: " + check.stderr[-300:])
        cls.temp = tempfile.TemporaryDirectory(prefix="a11y browser edges ")
        cls.root = Path(cls.temp.name)
        EdgeHandler.counts.clear()
        cls.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), EdgeHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = "http://127.0.0.1:{}".format(cls.server.server_port)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=3)
        cls.temp.cleanup()

    def adapter(self, route, selectors=()):
        process = subprocess.run(
            [NODE, str(SCRIPTS / "browser_scan.cjs")],
            input=json.dumps({"url": self.base + route, "timeout_ms": 12000, "selectors": list(selectors)}),
            cwd=REPO, env=self.env, text=True, capture_output=True, timeout=17,
        )
        try:
            result = json.loads(process.stdout)
        except json.JSONDecodeError:
            self.fail("Expected one adapter JSON result: {}\n{}".format(process.stdout, process.stderr))
        self.assertIsInstance(result, dict)
        self.assertIsInstance(result.get("errors"), list)
        return result

    def test_missing_and_nonexact_mime_never_produce_completed_audit(self):
        for route in ("/missing-mime", "/malformed-mime", "/mime-parameter"):
            with self.subTest(route=route):
                result = self.adapter(route)
                self.assertFalse(result["ok"], {"route": route, "status": result["run"]["status"],
                                                "errors": result["errors"]})
                self.assertNotEqual(result["run"]["status"], "completed")
                self.assertTrue(result["errors"])
                self.assertFalse(result["axe"]["passes"], "Rejected content must not produce passing checks")

    def test_empty_nested_shell_is_not_completed(self):
        result = self.adapter("/shell")
        self.assertFalse(result["ok"], {"status": result["run"]["status"], "errors": result["errors"]})
        self.assertNotEqual(result["run"]["status"], "completed")
        self.assertTrue(result["errors"])

    def test_nested_shell_waits_for_real_delayed_content(self):
        result = self.adapter("/delayed-shell", ("#late-action",))
        self.assertTrue(result["ok"], result["errors"])
        self.assertEqual(result["run"]["status"], "completed")
        self.assertEqual(result["errors"], [])
        self.assertIn('id="late-action"', result["html"])
        self.assertIs(result["run"]["observed_selectors"].get("#late-action"), True)

    def test_recovered_transient_attempt_is_history_not_operational_error(self):
        result = self.adapter("/recover")
        self.assertTrue(result["ok"], result["errors"])
        self.assertEqual(result["run"]["status"], "completed")
        self.assertEqual(result["errors"], [], "Recovered HTTP failures belong in attempt history")
        self.assertEqual(EdgeHandler.counts["/recover"], 2)
        attempts = result["run"]["attempts"]
        self.assertIsInstance(attempts, list)
        self.assertEqual(len(attempts), 2)
        self.assertEqual(attempts[0]["http_status"], 503)
        self.assertEqual(attempts[1]["http_status"], 200)

    def test_completed_run_has_comparable_engine_page_state_receipts(self):
        result = self.adapter("/receipts")
        self.assertTrue(result["ok"], result["errors"])
        run = result["run"]
        self.assertEqual(run["engines"], {"playwright": "1.63.0", "axe": "4.13.0", "rendered-dom": "1.0.0"})
        self.assertEqual(run["pages"], [self.base + "/receipts"])
        self.assertEqual(run["states"], ["initial-render"])
        self.assertEqual(run["original_url"], self.base + "/receipts")
        self.assertEqual(run["final_url"], self.base + "/receipts")

    def test_cli_recovered_transient_response_is_not_exit_two(self):
        output = self.root / "recovered CLI"
        process = subprocess.run(
            [sys.executable, str(SCRIPTS / "audit.py"), "--url", self.base + "/recover-cli",
             "--output", str(output)], cwd=REPO, env=self.env, text=True, capture_output=True, timeout=40,
        )
        self.assertIn(process.returncode, (0, 1), process.stderr)
        path = output / "accessibility-report.json"
        self.assertTrue(path.is_file(), process.stderr)
        report = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(report["errors"], [])
        self.assertEqual(report["metadata"]["run"]["status"], "completed")
        self.assertEqual(EdgeHandler.counts["/recover-cli"], 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
