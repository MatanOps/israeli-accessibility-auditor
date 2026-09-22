"""Local network regressions for bounded, audit-only metadata discovery."""

import http.server
import json
from pathlib import Path
import subprocess
import sys
import threading
import time
import unittest
from urllib.parse import urlsplit

REPO = Path(__file__).resolve().parents[1]


class MetadataNetworkBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.requests = []
        self.stop = threading.Event()
        owner = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_GET(self):
                owner.requests.append(self.path)
                path = urlsplit(self.path).path
                if path == "/trickle":
                    self.send_response(200)
                    self.send_header("Content-Type", "application/xml")
                    self.send_header("Content-Length", "1000")
                    self.end_headers()
                    for _ in range(1000):
                        if owner.stop.is_set():
                            return
                        try:
                            self.wfile.write(b" ")
                            self.wfile.flush()
                        except (BrokenPipeError, ConnectionResetError):
                            return
                        owner.stop.wait(0.1)
                    return
                if path == "/sitemap.xml":
                    self.send_response(302)
                    self.send_header("Location", "/logout")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                if path == "/logout":
                    body = b'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"/>'
                    self.send_response(200)
                    self.send_header("Content-Type", "application/xml")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                self.send_error(404)

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = "http://127.0.0.1:{}".format(self.server.server_port)

    def tearDown(self):
        self.stop.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def test_trickling_metadata_response_obeys_total_one_second_deadline(self):
        script = '''
import json, sys, time
sys.path.insert(0, sys.argv[1])
from site_scan import fetch_bytes
started = time.monotonic()
try:
    fetch_bytes(sys.argv[2], timeout=1)
except Exception as exc:
    print(json.dumps({'elapsed': time.monotonic() - started, 'error': type(exc).__name__}))
else:
    raise AssertionError('Incomplete trickling document was accepted')
'''
        started = time.monotonic()
        try:
            process = subprocess.run([sys.executable, "-c", script, str(REPO / "scripts"), self.base + "/trickle"],
                                     capture_output=True, text=True, timeout=3)
        except subprocess.TimeoutExpired:
            self.fail("fetch_bytes(timeout=1) exceeded the outer 3-second deadline while the server sent one byte every 0.1 seconds (elapsed {:.2f}s)".format(time.monotonic() - started))
        self.assertEqual(process.returncode, 0, process.stderr)
        result = json.loads(process.stdout)
        self.assertLess(result["elapsed"], 2.0, result)
        self.assertIn(("/trickle"), self.requests)

    def test_sitemap_redirect_never_requests_actionful_same_origin_destination(self):
        script = '''
import json, sys
sys.path.insert(0, sys.argv[1])
from site_scan import discover_sitemaps
print(json.dumps(discover_sitemaps(sys.argv[2], timeout=1)))
'''
        process = subprocess.run([sys.executable, "-c", script, str(REPO / "scripts"), self.base],
                                 capture_output=True, text=True, timeout=3)
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertNotIn("/logout", self.requests, "Audit-only metadata redirect fetched an actionful URL: " + repr(self.requests))
        result = json.loads(process.stdout)
        self.assertTrue(result["truncated"])
        self.assertTrue(result["warnings"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
