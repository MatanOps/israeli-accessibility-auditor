"""Readiness/challenge detection and explicit axe rule selection regressions.

These run the real browser adapter (Playwright + axe) against local fixtures.
They demonstrate in mocked local fixtures that an HTTP 200 interstitial no
longer produces a completed scan; they make no claim about live sites.
"""

import http.server
import json
import os
from pathlib import Path
import shutil
import subprocess
import threading
import time
import unittest


REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"
NODE = shutil.which("node")

CLOUDFLARE_CHALLENGE = (
    '<!doctype html><html lang="en"><head><meta charset="utf-8">'
    '<title>Just a moment...</title></head><body>'
    '<form id="challenge-form" action="/verify" method="post"></form>'
    '<div id="challenge-running">Checking your browser before accessing example.com.</div>'
    '<script src="/cdn-cgi/challenge-platform/h/b/orchestrate/jsch/v1"></script>'
    '</body></html>'
)
ONE_MOMENT_CHALLENGE = (
    '<!doctype html><html lang="en"><head><meta charset="utf-8">'
    '<title>One moment, please...</title></head><body>'
    '<p>Please wait while your request is being verified...</p>'
    '</body></html>'
)
PLEASE_WAIT_PROSE = (
    '<!doctype html><html lang="en"><head><meta charset="utf-8">'
    '<title>Support center</title></head><body>'
    '<nav><a href="/">Home</a> <a href="/docs">Docs</a> <a href="/pricing">Pricing</a>'
    ' <a href="/contact">Contact</a> <a href="/status">Status</a></nav>'
    '<main><h1>Support center</h1>'
    '<p>If you contacted support today, please wait up to one business day for an answer. '
    'One moment, please, is what our phone line says while routing your call, and that is fine.</p>'
    '<p>' + ('Real article content about response times and escalation paths. ' * 20) + '</p>'
    '<p>You can also open a ticket, browse the knowledge base, or check the status page.</p>'
    '</main></body></html>'
)
DELAYED_CONTENT = (
    '<!doctype html><html lang="en"><head><meta charset="utf-8">'
    '<title>Loading store</title></head><body>'
    '<div id="gate"><p>One moment, please...</p></div>'
    '<script>setTimeout(function () {'
    "document.title = 'Store';"
    "document.getElementById('gate').outerHTML = '<main><h1>Store</h1>'"
    " + '<nav><a href=\"/a\">A</a> <a href=\"/b\">B</a> <a href=\"/c\">C</a>"
    " <a href=\"/d\">D</a> <a href=\"/e\">E</a></nav>'"
    " + '<p id=\"late-content\">The real catalog arrived after a short loading gate.</p></main>';"
    '}, 1200);</script></body></html>'
)
RULES_FIXTURE = (
    '<!doctype html><html lang="en"><head><meta charset="utf-8">'
    '<title>Rule coverage fixture</title></head><body>'
    '<a href="#content" class="skip" style="position:absolute;left:-10000px">Skip to content</a>'
    '<main><h1>Store</h1><h3>Products</h3>'
    '<a href="/p1" aria-label="View product details"><span>Choose options</span></a>'
    '<a href="/p2" aria-label="Add to cart - Blue shirt">Add to cart</a>'
    '<p>Catalog text so the page has enough real content for a normal audit run.</p></main>'
    '<div>Text outside any landmark for the region rule.</div>'
    '</body></html>'
)


class ReadinessHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def do_GET(self):
        status, mime, body = 200, "text/html; charset=utf-8", PLEASE_WAIT_PROSE
        if self.path == "/cloudflare-challenge":
            body = CLOUDFLARE_CHALLENGE
        elif self.path == "/rich-challenge":
            body = CLOUDFLARE_CHALLENGE.replace('</body>',
                '<section>' + '<p>Security verification instructions and privacy information.</p>' * 30
                + '</section></body>')
        elif self.path == "/one-moment":
            body = ONE_MOMENT_CHALLENGE
        elif self.path == "/delayed-content":
            body = DELAYED_CONTENT
        elif self.path == "/rules":
            body = RULES_FIXTURE
        elif self.path == "/forbidden":
            status, body = 403, "<html><body>Forbidden</body></html>"
        elif self.path == "/plain-text":
            mime, body = "text/plain; charset=utf-8", "not html at all"
        elif self.path == "/hang":
            time.sleep(8)
            return
        payload = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        try:
            self.wfile.write(payload)
        except (BrokenPipeError, ConnectionResetError):
            pass


class ReadinessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.env = dict(os.environ, PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1")
        cache = Path(os.environ.get("A11Y_AUDITOR_CACHE", str(REPO / ".runtime")))
        if "PLAYWRIGHT_BROWSERS_PATH" not in cls.env and (cache / "browsers").exists():
            cls.env["PLAYWRIGHT_BROWSERS_PATH"] = str(cache / "browsers")
        if not NODE:
            raise unittest.SkipTest("Node is unavailable; readiness was not tested")
        check = subprocess.run(
            [NODE, "-e", "require('axe-core');require('playwright').chromium.launch({headless:true})"
             ".then(b=>b.close()).catch(e=>{console.error(e.message);process.exit(1)})"],
            cwd=REPO, env=cls.env, text=True, capture_output=True, timeout=25,
        )
        if check.returncode:
            raise unittest.SkipTest("Browser runtime unavailable; readiness not tested: " + check.stderr[-300:])
        cls.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), ReadinessHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = "http://127.0.0.1:{}".format(cls.server.server_port)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=3)

    def adapter(self, route, timeout_ms=15000, selectors=()):
        process = subprocess.run(
            [NODE, str(SCRIPTS / "browser_scan.cjs")],
            input=json.dumps({"url": self.base + route, "timeout_ms": timeout_ms,
                              "selectors": list(selectors)}),
            cwd=REPO, env=self.env, text=True, capture_output=True,
            timeout=timeout_ms / 1000 + 8,
        )
        try:
            result = json.loads(process.stdout)
        except json.JSONDecodeError:
            self.fail("Expected one adapter JSON result: {}\n{}".format(process.stdout, process.stderr))
        self.assertIsInstance(result, dict)
        self.assertIsInstance(result.get("errors"), list)
        return result

    def assert_blocked_without_findings(self, result, route):
        detail = {"route": route, "status": result["run"]["status"], "errors": result["errors"]}
        self.assertFalse(result["ok"], detail)
        self.assertEqual(result["run"]["status"], "not-performed", detail)
        self.assertTrue(result["errors"], "A blocked run must state a clear reason")
        self.assertEqual(result["html"], "", "No target HTML may be captured from an interstitial")
        for bucket in ("violations", "incomplete", "passes", "inapplicable"):
            self.assertEqual(result["axe"][bucket], [],
                             "No axe findings may come from an interstitial: " + bucket)

    def test_http200_provider_challenge_is_not_a_completed_scan(self):
        result = self.adapter("/cloudflare-challenge")
        self.assert_blocked_without_findings(result, "/cloudflare-challenge")
        self.assertTrue(any("scan not performed" in e for e in result["errors"]), result["errors"])
        readiness = result["run"]["readiness"]
        self.assertEqual(readiness["verdict"], "challenge-suspected")
        self.assertTrue(readiness["evidence"]["strong_markers"], readiness)

    def test_provider_challenge_is_not_validated_by_long_help_text(self):
        result = self.adapter("/rich-challenge")
        self.assert_blocked_without_findings(result, "/rich-challenge")

    def test_http200_one_moment_interstitial_is_not_a_completed_scan(self):
        result = self.adapter("/one-moment", timeout_ms=12000)
        self.assert_blocked_without_findings(result, "/one-moment")
        readiness = result["run"]["readiness"]
        self.assertEqual(readiness["verdict"], "challenge-suspected")
        self.assertGreaterEqual(readiness["checks"], 2,
                                "A suspected interstitial gets a bounded re-check before rejection")
        self.assertTrue(readiness["evidence"]["title_markers"], readiness)

    def test_normal_page_mentioning_please_wait_is_not_blocked(self):
        result = self.adapter("/please-wait-prose")
        self.assertTrue(result["ok"], result["errors"])
        self.assertEqual(result["run"]["status"], "completed")
        self.assertEqual(result["errors"], [])
        self.assertEqual(result["run"]["readiness"]["verdict"], "content")
        self.assertIn("Support center", result["html"])

    def test_delayed_real_content_behind_loading_gate_completes(self):
        result = self.adapter("/delayed-content")
        self.assertTrue(result["ok"], result["errors"])
        self.assertEqual(result["run"]["status"], "completed")
        self.assertEqual(result["errors"], [])
        readiness = result["run"]["readiness"]
        self.assertEqual(readiness["verdict"], "content")
        self.assertGreaterEqual(readiness["checks"], 2, "The gate must have been re-checked")
        self.assertIn('id="late-content"', result["html"])

    def test_http_403_stays_blocked(self):
        result = self.adapter("/forbidden")
        self.assertFalse(result["ok"], result["errors"])
        self.assertNotEqual(result["run"]["status"], "completed")
        self.assertTrue(any("403" in e for e in result["errors"]), result["errors"])
        self.assertEqual(result["axe"]["violations"], [])

    def test_non_html_content_stays_blocked(self):
        result = self.adapter("/plain-text")
        self.assertFalse(result["ok"], result["errors"])
        self.assertNotEqual(result["run"]["status"], "completed")
        self.assertTrue(any("non-HTML" in e for e in result["errors"]), result["errors"])

    def test_network_timeout_stays_an_operational_failure(self):
        result = self.adapter("/hang", timeout_ms=4000)
        self.assertFalse(result["ok"], result["errors"])
        self.assertNotEqual(result["run"]["status"], "completed")
        self.assertTrue(any("navigation failed" in e or "budget" in e for e in result["errors"]),
                        result["errors"])

    def test_explicit_rules_actually_execute_and_are_recorded(self):
        result = self.adapter("/rules")
        self.assertTrue(result["ok"], result["errors"])
        self.assertEqual(result["run"]["status"], "completed")
        policy = result["run"]["axe_policy"]
        self.assertEqual(policy["run_only_tags"],
                         ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22a", "wcag22aa"])
        self.assertEqual(policy["enabled_rules"],
                         ["label-content-name-mismatch", "skip-link", "heading-order",
                          "landmark-one-main", "page-has-heading-one", "region"])
        self.assertEqual(policy["experimental_rules"], ["label-content-name-mismatch"])
        execution = policy["rule_execution"]
        for rule in policy["enabled_rules"]:
            self.assertIn(execution.get(rule),
                          ("violations", "incomplete", "passes", "inapplicable"),
                          "Rule did not execute: {} -> {}".format(rule, execution.get(rule)))
        by_rule = {entry["id"]: entry for entry in result["axe"]["violations"]}
        self.assertIn("label-content-name-mismatch", by_rule,
                      sorted(by_rule) + [execution])
        mismatch = by_rule["label-content-name-mismatch"]
        self.assertEqual(len(mismatch["nodes"]), 1,
                         "Only the mismatched link may fail; the contained name must pass")
        self.assertIn("Choose options", mismatch["nodes"][0]["html"])
        self.assertIn("experimental", mismatch["tags"],
                      "Raw axe tags must survive the adapter transport")
        self.assertIn("heading-order", by_rule, sorted(by_rule))
        self.assertIn("region", by_rule, sorted(by_rule))
        executed_skip_link = execution["skip-link"]
        self.assertIn(executed_skip_link, ("violations", "incomplete"),
                      "A skip link without a target must not pass silently")


if __name__ == "__main__":
    unittest.main(verbosity=2)
