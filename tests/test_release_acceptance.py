"""Release gates using real browser rendering and a local Hebrew repair exercise."""

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
FIXTURES = REPO / "tests" / "fixtures"
NODE = shutil.which("node")


class ReleaseHandler(http.server.BaseHTTPRequestHandler):
    repair_state = "before"

    def log_message(self, *_args):
        pass

    def do_GET(self):
        names = {"/challenge": "challenge", "/please-wait": "please-wait",
                 "/delayed": "delayed", "/repair": "hebrew-repair", "/contact": "contact"}
        name = names.get(self.path)
        if name is None:
            self.send_error(404)
            return
        body = (FIXTURES / ("release-" + name + ".html")).read_text(encoding="utf-8")
        if self.path == "/repair" and self.repair_state != "before":
            body = body.replace('id="catalog"', 'id="content"')
            body = body.replace('aria-label="הצגת אפשרויות עבור חולצה"',
                                'aria-label="בחר אפשרויות עבור חולצה"')
            if self.repair_state == "removed":
                body = body.replace('    <a id="choose-options" href="#product-details" aria-label="בחר אפשרויות עבור חולצה">בחר אפשרויות</a>\n', '')
            elif self.repair_state == "renamed":
                body = body.replace('id="choose-options"', 'id="new-options"')
        payload = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        try:
            self.wfile.write(payload)
        except (BrokenPipeError, ConnectionResetError):
            pass


class ReleaseAcceptanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.env = dict(os.environ, PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1")
        cache = Path(os.environ.get("A11Y_AUDITOR_CACHE", str(REPO / ".runtime")))
        if "PLAYWRIGHT_BROWSERS_PATH" not in cls.env and (cache / "browsers").exists():
            cls.env["PLAYWRIGHT_BROWSERS_PATH"] = str(cache / "browsers")
        if not NODE:
            raise unittest.SkipTest("Node is unavailable; release browser acceptance was not tested")
        check = subprocess.run([NODE, "-e", "require('axe-core');require('playwright').chromium.launch({headless:true}).then(b=>b.close()).catch(e=>{console.error(e.message);process.exit(1)})"],
                               cwd=REPO, env=cls.env, capture_output=True, text=True, timeout=25)
        if check.returncode:
            raise unittest.SkipTest("Browser runtime unavailable: " + check.stderr[-300:])
        cls.temp = tempfile.TemporaryDirectory(prefix="release acceptance ")
        cls.root = Path(os.environ.get("A11Y_RELEASE_ACCEPTANCE_OUTPUT", cls.temp.name))
        cls.root.mkdir(parents=True, exist_ok=True)
        cls.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), ReleaseHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = "http://127.0.0.1:{}".format(cls.server.server_port)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=3)
        cls.temp.cleanup()

    def cli(self, route, name, baseline=None):
        output = self.root / name
        args = [sys.executable, str(SCRIPTS / "audit.py"), "--url", self.base + route,
                "--output", str(output), "--timeout", "12"]
        if baseline:
            args.extend(["--baseline", str(baseline)])
        process = subprocess.run(args, cwd=REPO, env=self.env, capture_output=True, text=True, timeout=25)
        self.assertNotIn("Traceback (most recent call last)", process.stderr)
        for suffix in ("json", "html", "md"):
            self.assertTrue((output / ("accessibility-report." + suffix)).is_file(), process.stderr)
        report = json.loads((output / "accessibility-report.json").read_text(encoding="utf-8"))
        return process, report, output

    def browser(self, script, *args):
        process = subprocess.run([NODE, "-e", script, *args], cwd=REPO, env=self.env,
                                 capture_output=True, text=True, timeout=35)
        self.assertEqual(process.returncode, 0, process.stderr)
        return json.loads(process.stdout)

    def test_http_200_challenge_is_operational_failure_without_target_evidence(self):
        process, report, output = self.cli("/challenge", "challenge")
        self.assertEqual(process.returncode, 2, process.stderr)
        self.assertEqual(report["metadata"]["run"]["status"], "not-performed")
        self.assertTrue(report["errors"])
        self.assertEqual([f for f in report["findings"] if f["status"] in ("fail", "pass")], [])
        visible = self.browser(r'''
const {chromium}=require('playwright');
(async()=>{const browser=await chromium.launch({headless:true});try {
const page=await browser.newPage();await page.goto(process.argv[1]);
console.log(JSON.stringify({text:await page.locator('body').innerText(), selectable:await page.locator('input.finding-select').count(), packet:await page.locator('#packet-next').isVisible()}));
}finally{await browser.close();}})().catch(e=>{console.error(e.stack);process.exit(1)});
''', (output / "accessibility-report.html").as_uri())
        self.assertIn("העמוד המבוקש לא נבדק", visible["text"])
        self.assertNotIn("לא נמצאו ליקויים מאומתים", visible["text"])
        self.assertEqual(visible["selectable"], 0, "A rejected page must not produce selectable target repairs")
        self.assertFalse(visible["packet"])

    def test_valid_content_mentioning_please_wait_is_audited(self):
        process, report, _ = self.cli("/please-wait", "valid mention")
        self.assertIn(process.returncode, (0, 1), process.stderr)
        self.assertEqual(report["metadata"]["run"]["status"], "completed")
        self.assertEqual(report["errors"], [])
        self.assertTrue(any(f["status"] == "pass" and f.get("engine") == "axe" for f in report["findings"]))

    def test_short_contact_form_with_captcha_widget_is_not_an_interstitial(self):
        process, report, _ = self.cli("/contact", "contact with widget")
        self.assertIn(process.returncode, (0, 1), process.stderr)
        self.assertEqual(report["metadata"]["run"]["status"], "completed")
        self.assertEqual(report["errors"], [])

    def test_delayed_hebrew_content_is_checked_after_it_appears(self):
        process, report, _ = self.cli("/delayed", "delayed Hebrew")
        self.assertEqual(process.returncode, 1, process.stderr)
        self.assertEqual(report["metadata"]["run"]["status"], "completed")
        self.assertTrue(any(f.get("rule_id") == "axe-label-content-name-mismatch" and f["status"] == "fail"
                            and f.get("location", {}).get("selector") == "#delayed-action"
                            for f in report["findings"]), "The delayed control must actually be audited")

    def test_selected_hebrew_findings_are_repaired_and_verified_with_positive_evidence(self):
        ReleaseHandler.repair_state = "before"
        try:
            process, before, output = self.cli("/repair", "Hebrew before")
            self.assertEqual(process.returncode, 1, process.stderr)
            rules = {"axe-label-content-name-mismatch": "#choose-options", "skip-link-target-missing": "#skip-content"}
            targets = {}
            for rule, selector in rules.items():
                matches = [f for f in before["findings"] if f.get("rule_id") == rule and f["status"] == "fail"
                           and f.get("location", {}).get("selector") == selector]
                self.assertEqual(len(matches), 1, (rule, matches))
                targets[rule] = matches[0]
            self.assertFalse(any(f["status"] == "fail" and f.get("rule_id") == "axe-label-content-name-mismatch"
                                 and f.get("location", {}).get("selector") == "#valid-expanded"
                                 for f in before["findings"]))
            self.assert_skip_keyboard_target("#skip-content")
            self.assert_selected_report_controls(output, targets)
            baseline = output / "accessibility-report.json"
            ReleaseHandler.repair_state = "fixed"
            process, after, _ = self.cli("/repair", "Hebrew fixed", baseline)
            self.assert_skip_keyboard_target("#content")
            self.assertIn(process.returncode, (0, 1), process.stderr)
            fixed = {entry["identity"] for entry in after["comparison"]["fixed_verified"]}
            for rule, finding in targets.items():
                self.assertIn(finding["stable_id"], fixed, after["comparison"])
                selector = rules[rule]
                self.assertIs(after["metadata"]["run"]["observed_selectors"][selector], True)
                self.assertTrue(any(f.get("rule_id") == rule and f["status"] == "pass"
                                    and any(loc.get("selector") == selector for loc in f.get("locations", []))
                                    for f in after["findings"]), "Fix needs an explicit pass for the same rule and selector")
            for state in ("removed", "renamed"):
                with self.subTest(state=state):
                    ReleaseHandler.repair_state = state
                    process, changed, _ = self.cli("/repair", "Hebrew " + state, baseline)
                    self.assertIn(process.returncode, (0, 1), process.stderr)
                    identity = targets["axe-label-content-name-mismatch"]["stable_id"]
                    self.assertNotIn(identity, {e["identity"] for e in changed["comparison"]["fixed_verified"]})
                    self.assertIn(identity, {e["identity"] for e in changed["comparison"]["not_tested"]})
        finally:
            ReleaseHandler.repair_state = "before"

    def assert_skip_keyboard_target(self, expected):
        result = self.browser(r''' 
const {chromium}=require('playwright');
(async()=>{const browser=await chromium.launch({headless:true});try{
const page=await browser.newPage();await page.goto(process.argv[1]);
await page.keyboard.press('Tab');
if(await page.locator('#skip-content').evaluate(el=>el!==document.activeElement))throw new Error('Skip link is not first keyboard target');
await page.keyboard.press('Enter');
console.log(JSON.stringify({focused:await page.evaluate(()=>document.activeElement.id),hash:new URL(page.url()).hash}));
}finally{await browser.close();}})().catch(e=>{console.error(e.stack);process.exit(1)});
''', self.base + "/repair")
        self.assertEqual(result["focused"], expected.lstrip("#"))
        self.assertEqual(result["hash"], "#content")

    def assert_selected_report_controls(self, output, targets):
        selected = targets["axe-label-content-name-mismatch"]["stable_id"]
        excluded = targets["skip-link-target-missing"]["stable_id"]
        result = self.browser(r'''
const {chromium}=require('playwright');const fs=require('fs');
function check(value,message){if(!value)throw new Error(message);}
(async()=>{const browser=await chromium.launch({headless:true});try{
const page=await browser.newPage({acceptDownloads:true,viewport:{width:1280,height:900}});
const external=[];await page.route(/^https?:/,route=>{external.push(route.request().url());return route.abort();});
await page.addInitScript(()=>Object.defineProperty(navigator,'clipboard',{value:{writeText:async value=>{window.copied=value;}}}));
await page.goto(process.argv[1]);
check(await page.locator('html').getAttribute('dir')==='rtl','Report must be RTL');
await page.screenshot({path:process.argv[5]});
const box=page.locator('input.finding-select[value="'+process.argv[2]+'"]');
await box.evaluate(el=>{for(let p=el.parentElement;p;p=p.parentElement){if(p.tagName==='DETAILS')p.open=true;}});
await box.focus();await page.keyboard.press('Space');
check(await box.isChecked(),'Keyboard selection failed');
await page.locator('#copy-selected').click();
await page.waitForFunction(()=>typeof window.copied==='string');
const packet=await page.evaluate(()=>window.copied);
check(packet.includes(process.argv[2])&&!packet.includes(process.argv[3]),'Copy contains wrong selection');
check(packet.includes('#choose-options')&&!packet.includes('#skip-content'),'Copy contains unselected evidence');
const downloadPromise=page.waitForEvent('download');await page.locator('#download-selected').click();
const download=await downloadPromise;const downloaded=fs.readFileSync(await download.path(),'utf8');
check(downloaded===packet,'Downloaded packet differs from copied selection');
await download.saveAs(process.argv[4]);
await page.locator('#clear-selected').click();
check(await page.locator('input.finding-select:checked').count()===0,'Selection not cleared');
check(!await page.locator('#packet-next').isVisible(),'Stale handoff visible after clear');
await box.check();await page.evaluate(()=>{navigator.clipboard.writeText=async()=>{throw new Error('clipboard unavailable');};});
await page.locator('#copy-selected').click();await page.locator('#packet-fallback').waitFor({state:'visible'});
await box.uncheck();check(!await page.locator('#packet-fallback').isVisible(),'Stale packet after selection change');
check(await page.locator('#packet-text').inputValue()==='','Stale packet text retained');
await page.setViewportSize({width:390,height:844});
check(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth+1),'Mobile report has horizontal overflow');
await page.locator('#copy-selected').scrollIntoViewIfNeeded();check(await page.locator('#copy-selected').isVisible(),'Mobile copy control missing');
check(external.length===0,'Standalone report loaded external resources');
await page.evaluate(()=>window.scrollTo(0,0));await page.screenshot({path:process.argv[6]});
console.log(JSON.stringify({ok:true,selected:process.argv[2],downloaded:download.suggestedFilename()}));
}finally{await browser.close();}})().catch(e=>{console.error(e.stack);process.exit(1)});
''', (output / "accessibility-report.html").as_uri(), selected, excluded, str(output / "repair-packet-selected.md"),
             str(output / "report-desktop.png"), str(output / "report-mobile.png"))
        self.assertTrue(result["ok"])
        self.assertEqual(result["downloaded"], "repair-packet.md")


if __name__ == "__main__":
    unittest.main(verbosity=2)
