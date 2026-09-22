"""Local-only integration checks for rendering, HTML reports and repair packets.

Run after runtime preparation: python -m unittest discover -s tests -v
Browser cases skip explicitly when the optional browser runtime is unavailable.
"""
from __future__ import annotations

from collections import Counter
import http.server
import importlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest


REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"
NODE = shutil.which("node")


def document(body):
    return ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
            '<title>Local accessibility test</title></head><body>' + body + '</body></html>')


VALID_HTML = document('<main><h1>Local test</h1><p>Deterministic local content.</p></main>')
JS_HTML = document('<div id="root"></div><script>setTimeout(() => {'
                   'document.querySelector("#root").innerHTML = '
                   '\'<main><h1>Rendered content</h1><img id="dynamic-image" '
                   'src="data:image/svg+xml,%3Csvg xmlns=%22http://www.w3.org/2000/svg%22/%3E"></main>\';'
                   '}, 150);</script>')


class DeterministicHandler(http.server.BaseHTTPRequestHandler):
    counts = Counter()
    counter_lock = threading.Lock()
    repair_state = "missing"

    def log_message(self, *_args):
        pass

    def do_GET(self):
        with self.counter_lock:
            self.counts[self.path] += 1
            attempt = self.counts[self.path]
        status, content_type, body = 200, "text/html; charset=utf-8", VALID_HTML
        if self.path == "/javascript":
            body = JS_HTML
        elif self.path == "/repair":
            alt = ' alt="Black square"' if self.repair_state == "fixed" else ""
            image = ('<img id="repair-target" width="20" height="20"' + alt
                     + ' src="data:image/svg+xml,%3Csvg xmlns=%22http://www.w3.org/2000/svg%22 '
                     + 'width=%2220%22 height=%2220%22%3E%3Crect width=%2220%22 height=%2220%22 '
                     + 'fill=%22black%22/%3E%3C/svg%3E">')
            if self.repair_state == "removed":
                image = ""
            body = document('<main><h1>Local repair fixture</h1><p>A synthetic black square.</p>' + image + '</main>')
        elif self.path == "/slow":
            time.sleep(0.3)
        elif self.path == "/timeout":
            time.sleep(2)
        elif self.path == "/transient" and attempt == 1:
            status, body = 503, "Temporary local test failure"
        elif self.path == "/permanent":
            status, body = 404, "Local fixture not found"
        elif self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "/final")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        elif self.path == "/empty":
            body = ""
        elif self.path == "/empty-document":
            body = document("")
        elif self.path == "/non-html":
            content_type, body = "application/json", '{"message":"fixture"}'
        elif self.path == "/blocked":
            status, body = 403, document('<h1>Access denied</h1>')
        payload = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        try:
            self.wfile.write(payload)
        except (BrokenPipeError, ConnectionResetError):
            pass


class SprintIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="a11y rendered acceptance ")
        cls.root = Path(cls.temp.name)
        cls.env = dict(os.environ, PYTHONUTF8="1")
        cache = Path(os.environ.get("A11Y_AUDITOR_CACHE", str(REPO / ".runtime")))
        if "PLAYWRIGHT_BROWSERS_PATH" not in cls.env and (cache / "browsers").exists():
            cls.env["PLAYWRIGHT_BROWSERS_PATH"] = str(cache / "browsers")
        DeterministicHandler.counts.clear()
        cls.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), DeterministicHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base_url = f"http://127.0.0.1:{cls.server.server_port}"
        cls.browser_reason = None
        if not NODE:
            cls.browser_reason = "Node.js is unavailable"
        elif not (SCRIPTS / "browser_scan.cjs").exists():
            cls.browser_reason = "browser_scan.cjs is unavailable"
        else:
            check = subprocess.run(
                [NODE, "-e", "require('axe-core');require('playwright').chromium.launch({headless:true}).then(b=>b.close()).catch(e=>{console.error(e.message);process.exit(1)})"],
                cwd=REPO, env=cls.env, text=True, capture_output=True, timeout=25,
            )
            if check.returncode:
                cls.browser_reason = "Browser runtime is unavailable: " + check.stderr[-400:]

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=3)
        cls.temp.cleanup()

    def require_browser(self):
        if self.browser_reason:
            self.skipTest(self.browser_reason)

    def scan_browser(self, route, selectors=(), timeout_ms=12000):
        self.require_browser()
        process = subprocess.run(
            [NODE, str(SCRIPTS / "browser_scan.cjs")],
            input=json.dumps({"url": self.base_url + route, "timeout_ms": timeout_ms, "selectors": list(selectors)}),
            cwd=REPO, env=self.env, text=True, capture_output=True, timeout=20,
        )
        try:
            result = json.loads(process.stdout)
        except json.JSONDecodeError:
            self.fail(f"Browser adapter stdout is not exactly one JSON object: {process.stdout!r}; stderr={process.stderr!r}")
        self.assertIsInstance(result.get("ok"), bool)
        self.assertIsInstance(result.get("run"), dict)
        self.assertIsInstance(result.get("errors"), list)
        self.assertIn(result["run"].get("status"), {"completed", "partial", "not-performed"})
        return result

    def scan_cli(self, *arguments, output_name=None):
        output = self.root / (output_name or self.id().split(".")[-1])
        process = subprocess.run(
            [sys.executable, str(SCRIPTS / "audit.py"), *map(str, arguments), "--output", str(output)],
            cwd=REPO, env=self.env, text=True, capture_output=True, timeout=45,
        )
        self.assertNotIn("Traceback (most recent call last)", process.stderr,
                         "A crashed CLI must not masquerade as exit code 1 for findings")
        for extension in ("md", "json", "html"):
            self.assertTrue((output / f"accessibility-report.{extension}").exists(),
                            f"Missing {extension} report; exit={process.returncode}; stderr={process.stderr}")
        html = (output / "accessibility-report.html").read_text(encoding="utf-8")
        self.assertIn("</html>", html.lower(), "The HTML report must be complete, not an empty file after a renderer crash")
        report = json.loads((output / "accessibility-report.json").read_text(encoding="utf-8"))
        return process, report, output

    def test_browser_executes_javascript_and_axe_locally(self):
        result = self.scan_browser("/javascript", ("#dynamic-image", "#missing-control"))
        self.assertTrue(result["ok"], result["errors"])
        self.assertEqual(result["run"]["status"], "completed")
        self.assertTrue(result["run"]["rendered"])
        self.assertIn('id="dynamic-image"', result["html"])
        self.assertIn("image-alt", {item["id"] for item in result["axe"]["violations"]})
        self.assertTrue(result["axe"].get("testEngine", {}).get("version"))
        self.assertIs(result["run"]["observed_selectors"].get("#dynamic-image"), True)
        self.assertIs(result["run"]["observed_selectors"].get("#missing-control"), False)

    def test_browser_waits_for_slow_local_response(self):
        result = self.scan_browser("/slow")
        self.assertTrue(result["ok"], result["errors"])
        self.assertEqual(result["run"]["status"], "completed")

    def test_browser_retries_one_transient_response(self):
        result = self.scan_browser("/transient")
        self.assertTrue(result["ok"], result["errors"])
        self.assertEqual(DeterministicHandler.counts["/transient"], 2)

    def test_browser_does_not_retry_permanent_http_error(self):
        result = self.scan_browser("/permanent")
        self.assertFalse(result["ok"])
        self.assertNotEqual(result["run"]["status"], "completed")
        self.assertTrue(result["errors"])
        self.assertEqual(DeterministicHandler.counts["/permanent"], 1)

    def test_browser_timeout_is_bounded_and_not_a_completed_audit(self):
        started = time.monotonic()
        result = self.scan_browser("/timeout", timeout_ms=800)
        self.assertLess(time.monotonic() - started, 6, "A short configured deadline must bound the adapter")
        self.assertFalse(result["ok"])
        self.assertNotEqual(result["run"]["status"], "completed")
        self.assertTrue(result["errors"])
        self.assertLessEqual(DeterministicHandler.counts["/timeout"], 2)

    def test_browser_records_redirect_final_url(self):
        result = self.scan_browser("/redirect")
        self.assertTrue(result["ok"], result["errors"])
        self.assertEqual(result["run"]["original_url"], self.base_url + "/redirect")
        self.assertEqual(result["run"]["final_url"], self.base_url + "/final")

    def test_browser_rejects_empty_non_html_and_blocked_responses(self):
        for route in ("/empty", "/empty-document", "/non-html", "/blocked"):
            with self.subTest(route=route):
                result = self.scan_browser(route)
                self.assertFalse(result["ok"])
                self.assertNotEqual(result["run"]["status"], "completed")
                self.assertTrue(result["errors"])

    def test_url_cli_defaults_to_rendered_axe_engine(self):
        self.require_browser()
        process, report, _ = self.scan_cli("--url", self.base_url + "/javascript")
        self.assertEqual(process.returncode, 1, process.stderr)
        self.assertTrue(report["metadata"]["run"]["rendered"])
        self.assertEqual(report["metadata"]["run"]["status"], "completed")
        matches = [f for f in report["findings"] if f.get("engine") == "axe"
                   and f.get("rule_id") == "axe-image-alt" and f["status"] == "fail"]
        self.assertTrue(matches, "Rendered image without alt must survive axe-to-report translation")
        self.assertTrue(all(f.get("stable_id") for f in matches))

    def test_static_url_is_explicitly_partial(self):
        process, report, _ = self.scan_cli("--url", self.base_url + "/javascript", "--static")
        self.assertIn(process.returncode, {0, 1}, process.stderr)
        self.assertFalse(report["metadata"]["run"]["rendered"])
        self.assertEqual(report["metadata"]["run"]["status"], "partial")
        self.assertFalse(any(f.get("engine") == "axe" for f in report["findings"]))

    def test_source_report_remains_partial_and_has_html(self):
        source = self.root / "local source"
        source.mkdir()
        (source / "index.html").write_text(VALID_HTML, encoding="utf-8")
        process, report, output = self.scan_cli("--path", source)
        self.assertIn(process.returncode, {0, 1}, process.stderr)
        self.assertEqual(report["metadata"]["run"]["status"], "partial")
        html = (output / "accessibility-report.html").read_text(encoding="utf-8")
        self.assertIn('dir="rtl"', html)
        self.assertIn("Next Impact", html)

    def test_rendered_cli_error_still_writes_all_reports(self):
        self.require_browser()
        process, report, _ = self.scan_cli("--url", self.base_url + "/blocked")
        self.assertEqual(process.returncode, 2, process.stderr)
        self.assertTrue(report["errors"])
        self.assertNotEqual(report["metadata"]["run"]["status"], "completed")
        self.assertTrue(any(f["status"] == "not-tested" for f in report["findings"]))

    def test_source_baseline_absence_is_not_verified_fixed(self):
        source = self.root / "baseline source"
        source.mkdir()
        page = source / "index.html"
        page.write_text(document('<main><h1>Test</h1><img src="x"></main>'), encoding="utf-8")
        _, _, before_output = self.scan_cli("--path", source, output_name="baseline before")
        page.write_text(VALID_HTML, encoding="utf-8")
        process, report, _ = self.scan_cli("--path", source, "--baseline", before_output / "accessibility-report.json",
                                           output_name="baseline after")
        self.assertIn(process.returncode, {0, 1}, process.stderr)
        self.assertIn("comparison", report)
        self.assertFalse(report["comparison"]["fixed_verified"], "Source absence alone never proves a fix")

    def test_rendered_baseline_distinguishes_repaired_image_from_removed_element(self):
        self.require_browser()
        DeterministicHandler.repair_state = "missing"
        url = self.base_url + "/repair"
        try:
            process, before, before_output = self.scan_cli("--url", url, output_name="rendered repair before")
            self.assertEqual(process.returncode, 1, process.stderr)
            prior = [f for f in before["findings"] if f.get("engine") == "axe"
                     and f.get("rule_id") == "axe-image-alt" and f["status"] == "fail"]
            self.assertEqual(len(prior), 1, "The controlled fixture must have exactly one missing-alt image")
            identity = prior[0].get("stable_id") or prior[0]["id"]
            baseline = before_output / "accessibility-report.json"

            def includes_identity(items):
                for item in items:
                    if isinstance(item, str) and item == identity:
                        return True
                    if isinstance(item, dict):
                        if (item.get("stable_id") or item.get("id")) == identity:
                            return True
                        for key in ("before", "finding", "previous"):
                            nested = item.get(key)
                            if isinstance(nested, dict) and (nested.get("stable_id") or nested.get("id")) == identity:
                                return True
                return False

            DeterministicHandler.repair_state = "fixed"
            process, fixed, _ = self.scan_cli("--url", url, "--baseline", baseline,
                                             output_name="rendered repair fixed")
            self.assertIn(process.returncode, {0, 1}, process.stderr)
            self.assertEqual(fixed["metadata"]["run"]["status"], "completed")
            self.assertTrue(includes_identity(fixed["comparison"]["fixed_verified"]),
                            "Axe passing the same extant image selector should verify this narrow repair")

            DeterministicHandler.repair_state = "removed"
            process, removed, _ = self.scan_cli("--url", url, "--baseline", baseline,
                                               output_name="rendered repair removed")
            self.assertIn(process.returncode, {0, 1}, process.stderr)
            self.assertTrue(includes_identity(removed["comparison"]["not_tested"]),
                            "Removing the element is unavailable coverage, not proof of repaired alt text")
            self.assertFalse(includes_identity(removed["comparison"]["fixed_verified"]))
        finally:
            DeterministicHandler.repair_state = "missing"

    def test_html_controls_build_only_selected_repair_packet(self):
        self.require_browser()
        if not (SCRIPTS / "html_report.py").exists() or not (SCRIPTS / "repair_packet.py").exists():
            self.skipTest("HTML report or repair packet module is unavailable")
        sys.path.insert(0, str(SCRIPTS))
        try:
            report_module = importlib.import_module("report")
            html_module = importlib.import_module("html_report")
            findings = []
            for suffix, status, evidence_type in (("selected", "fail", "automatically-verified"),
                                                   ("unselected", "warning", "heuristic")):
                item = report_module.finding("test-" + suffix, "Images and media", "serious", status, evidence_type,
                    "1.1.1", {"file": "fixture.html", "line": 1}, "UNIQUE_" + suffix.upper(),
                    "Synthetic finding " + suffix, "Verify and repair " + suffix)
                item.update(stable_id="stable-" + suffix, rule_id="test-" + suffix, engine="static")
                findings.append(item)
            report = report_module.build_report("fixture.html", "source", findings, 1, [],
                {"run": {"status": "partial", "rendered": False, "engines": {"static": True},
                         "pages": [], "states": [], "untested": []}})
            selected_identity = next(item["stable_id"] for item in report["findings"]
                                     if item["evidence"] == "UNIQUE_SELECTED")
            html_file = self.root / "interactive report.html"
            html_file.write_text(html_module.render_html(report), encoding="utf-8")
        finally:
            sys.path.pop(0)
        script = r'''
const {chromium} = require('playwright');
const fs = require('fs');
(async () => {
  const browser = await chromium.launch({headless:true});
  try {
    const context = await browser.newContext({acceptDownloads:true});
    const page = await context.newPage();
    const external = [];
    await page.route(/^https?:/, route => { external.push(route.request().url()); return route.abort(); });
    await page.addInitScript(() => {
      Object.defineProperty(navigator, 'clipboard', {value:{writeText:async value=>{window.__copied=value;}}});
    });
    await page.goto(process.argv[1]);
    await page.waitForFunction(() => window.auditReport && window.RepairPack && window.RepairPack.build);
    await page.evaluate(() => {
      const original = window.RepairPack.build;
      window.RepairPack.build = function(report, ids) {window.__selectedIds=Array.from(ids); return original(report,ids);};
    });
    const boxes = page.locator('input[type="checkbox"]');
    if (await boxes.count() < 2) throw new Error('Expected selectable findings');
    await page.locator('.occurrence-details > summary').first().click();
    await boxes.evaluateAll(elements => elements.forEach(element => {element.checked=false;element.dispatchEvent(new Event('change',{bubbles:true}));}));
    await boxes.first().check();
    const copy = page.getByRole('button', {name:/העתק|העתקה|copy/i}).first();
    await copy.click();
    await page.waitForFunction(() => window.__selectedIds && window.__selectedIds.length===1);
    const selected = await page.evaluate(() => ({ids:window.__selectedIds, packet:window.__copied || document.querySelector('textarea')?.value || ''}));
    if (selected.ids[0]!==process.argv[2]) throw new Error('Unexpected selected identity: '+JSON.stringify(selected.ids));
    if (!selected.packet.includes('UNIQUE_SELECTED') || selected.packet.includes('UNIQUE_UNSELECTED')) throw new Error('Packet included unselected evidence or omitted selected evidence');
    if (!await page.locator('#packet-next').isVisible()) throw new Error('Missing next step after copying');
    if (!selected.packet.includes('ראיית מעבר מפורשת')) throw new Error('Repair packet does not require positive verification');
    const downloadPromise = page.waitForEvent('download');
    await page.getByRole('button',{name:/הורד|הורדה|download/i}).first().click();
    const download = await downloadPromise;
    const file = await download.path();
    const downloaded = fs.readFileSync(file,'utf8');
    if (!downloaded.includes('UNIQUE_SELECTED') || downloaded.includes('UNIQUE_UNSELECTED')) throw new Error('Download selection differs');
    await page.getByRole('button', {name:'ניקוי הבחירה'}).click();
    if (await page.locator('input:checked').count()) throw new Error('Clear selection failed');
    if (await page.locator('#packet-next').isVisible()) throw new Error('Stale handoff after clearing selection');
    await copy.click();
    if (!(await page.locator('#packet-status').innerText()).includes('לא נבחרו')) throw new Error('Empty selection was not explained');
    await boxes.first().check();
    await page.evaluate(() => {navigator.clipboard.writeText = async () => {throw new Error('denied');};});
    await copy.click();
    await page.locator('#packet-fallback').waitFor({state:'visible'});
    if (!(await page.locator('#packet-text').inputValue()).includes('UNIQUE_SELECTED')) throw new Error('Clipboard fallback lost selected packet');
    await boxes.first().uncheck();
    if (await page.locator('#packet-fallback').isVisible()) throw new Error('Stale fallback packet after selection changed');
    for (const outcome of ['reject', 'resolve']) {
      await boxes.first().check();
      await page.evaluate(() => {navigator.clipboard.writeText = () => new Promise((resolve, reject) => {window.__finishCopy={resolve,reject};});});
      await copy.click();
      await page.getByRole('button', {name:'ניקוי הבחירה'}).click();
      await page.evaluate(result => {window.__finishCopy[result](result==='reject' ? new Error('late denial') : undefined);}, outcome);
      if (await page.locator('#packet-fallback').isVisible()) throw new Error('Late clipboard result revived stale packet');
      if (await page.locator('#packet-text').inputValue()) throw new Error('Late clipboard result restored stale text');
      if (await page.locator('#packet-next').isVisible()) throw new Error('Late clipboard result restored stale next step');
      if (!(await page.locator('#packet-status').innerText()).includes('הבחירה נוקתה')) throw new Error('Late clipboard result overwrote current status');
    }
    if (external.length) throw new Error('HTML report requested external resources: '+external.join(','));
    console.log(JSON.stringify({ok:true,ids:selected.ids}));
  } finally {await browser.close();}
})().catch(error => {console.error(error.stack);process.exit(1);});
'''
        process = subprocess.run([NODE, "-e", script, html_file.as_uri(), selected_identity], cwd=REPO, env=self.env,
                                 capture_output=True, text=True, timeout=35)
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertTrue(json.loads(process.stdout)["ok"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
