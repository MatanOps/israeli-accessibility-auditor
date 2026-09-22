"""Website audit acceptance against a local, observable HTTP fixture.

No public site is contacted. The served source tree is hashed around every run
so a report cannot pass this gate by repairing the fixture while auditing it.
"""

from collections import Counter
import hashlib
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
from urllib.parse import urlsplit

REPO = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")


def document(title, content):
    return ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>' + title + '</title><style>body{color:#111;background:#fff;'
            'font:18px sans-serif}a{display:inline-block;margin:12px;color:#003366}'
            '</style></head><body><main id="content"><h1>' + title + '</h1>'
            + content + '</main></body></html>')


class SiteAcceptanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.env = dict(os.environ, PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1")
        if not NODE:
            raise unittest.SkipTest("Node unavailable; website browser acceptance not tested")
        probe = subprocess.run(
            [NODE, "-e", "require('axe-core');require('playwright').chromium.launch({headless:true}).then(b=>b.close()).catch(e=>{console.error(e.message);process.exit(1)})"],
            cwd=REPO, env=cls.env, capture_output=True, text=True, timeout=25)
        if probe.returncode:
            raise unittest.SkipTest("Browser runtime unavailable: " + probe.stderr[-300:])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="site acceptance ")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "served-source"
        self.source.mkdir()
        self.requests = []
        self.external_requests = []
        self.scenario = "complete"
        owner = self

        class External(http.server.BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_GET(self):
                owner.external_requests.append(self.path)
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"External destination must never be visited")

        self.external = self.start_server(External)
        self.external_url = "http://127.0.0.1:{}/external".format(self.external.server_port)

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_POST(self):
                owner.requests.append(("POST", self.path))
                self.send_error(405)

            def do_GET(self):
                owner.requests.append(("GET", self.path))
                path = urlsplit(self.path).path
                if path == "/robots.txt":
                    return self.respond("User-agent: *\nAllow: /\nSitemap: " + owner.base + "/sitemap.xml", "text/plain")
                if path == "/sitemap.xml":
                    entries = [owner.base + "/sitemap-only"] if owner.scenario == "complete" else []
                    return self.respond('<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
                                        + ''.join('<url><loc>' + url + '</loc></url>' for url in entries) + '</urlset>', "application/xml")
                if path == "/blocked" or (owner.scenario == "all-blocked" and path == "/"):
                    return self.respond(document("Access denied", "<p>Access to this page is forbidden.</p>"), status=403)
                filename = {"/": "index.html", "/inner": "inner.html", "/sitemap-only": "sitemap.html"}.get(path)
                if filename:
                    return self.respond((owner.source / filename).read_text(encoding="utf-8"))
                self.send_error(404)

            def respond(self, body, content_type="text/html", status=200):
                payload = body.encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", content_type + "; charset=utf-8")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                try:
                    self.wfile.write(payload)
                except (BrokenPipeError, ConnectionResetError):
                    pass

        self.server = self.start_server(Handler)
        self.base = "http://127.0.0.1:{}".format(self.server.server_port)
        self.write_fixture()

    def start_server(self, handler):
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        def close():
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)

        self.addCleanup(close)
        return server

    def write_fixture(self):
        shared = '<img id="shared-image" src="data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7" width="20" height="20">'
        links = ('<nav aria-label="Main"><a href="/inner">Inner page</a>'
                 '<a href="/inner#content">Inner fragment</a><a href="/inner">Inner duplicate</a>'
                 '<a href="#content">This page</a><a href="' + self.external_url + '">External destination</a>'
                 '<a href="/download.pdf" download>Download</a><a href="/login">Log in</a>'
                 '<a href="/logout">Log out</a><a href="/?action=delete">Delete action</a></nav>')
        if self.scenario == "mixed":
            links += '<a href="/blocked">Blocked page</a>'
        (self.source / "index.html").write_text(document("Local acceptance homepage", links + shared), encoding="utf-8")
        (self.source / "inner.html").write_text(document("Inner acceptance page", shared + '<button id="inner-only-button" type="button"></button><a href="/">Home</a>'), encoding="utf-8")
        (self.source / "sitemap.html").write_text(document("Only in sitemap", '<p>This page can only be discovered from the XML sitemap.</p>'), encoding="utf-8")

    def source_hashes(self):
        return {str(path.relative_to(self.source)): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in self.source.rglob("*") if path.is_file()}

    def cli(self, name, *, single_page=False, max_pages=100):
        before = self.source_hashes()
        output = self.root / name
        args = [sys.executable, str(REPO / "scripts" / "audit.py"),
                "--url" if single_page else "--site", self.base + "/",
                "--output", str(output), "--timeout", "30"]
        if not single_page:
            args += ["--max-pages", str(max_pages), "--max-seconds", "600"]
        process = subprocess.run(args, cwd=REPO, env=self.env, capture_output=True, text=True, timeout=120)
        self.assertEqual(before, self.source_hashes(), "Audit modified the served website source")
        self.assertNotIn("Traceback (most recent call last)", process.stderr)
        for suffix in ("json", "html", "md"):
            self.assertTrue((output / ("accessibility-report." + suffix)).is_file(), process.stderr)
        reports = list(output.rglob("accessibility-report.json"))
        self.assertEqual(len(reports), 1, "Website audit must return one aggregated report")
        report = json.loads(reports[0].read_text(encoding="utf-8"))
        return process, report, output

    def assert_coverage_consistent(self, report):
        site = report["metadata"]["run"]["site"]
        for key in ("discovered", "scanned", "failed", "pending", "excluded"):
            self.assertIs(type(site[key]), int, key)
            self.assertGreaterEqual(site[key], 0, key)
        self.assertEqual(site["discovered"], site["scanned"] + site["failed"] + site["pending"])
        self.assertEqual(len(site["page_results"]), site["scanned"] + site["failed"])
        self.assertEqual(len(site["pending_urls"]), site["pending"])
        self.assertEqual(len(site["excluded_urls"]), site["excluded"])
        self.assertEqual(report["summary"]["files_scanned"], site["scanned"])
        self.assertIs(type(site["complete"]), bool)
        self.assertTrue(site["stop_reason"])
        self.assertEqual(len({entry["url"] for entry in site["page_results"]}), len(site["page_results"]))
        for entry in site["page_results"]:
            self.assertTrue({"url", "final_url", "status", "reason"} <= set(entry), entry)
            self.assertIn(entry["status"], ("completed", "failed"), entry)
        self.assertEqual(sum(entry["status"] == "completed" for entry in site["page_results"]), site["scanned"])
        self.assertEqual(sum(entry["status"] == "failed" for entry in site["page_results"]), site["failed"])
        for entry in site["excluded_urls"]:
            self.assertTrue(entry["url"] and entry["reason"], entry)
        self.assertEqual(site["limits"]["max_seconds"], 600)
        return site

    def assert_no_unsafe_visits(self):
        self.assertEqual(self.external_requests, [], "External anchors must not be crawled")
        forbidden = {"/download.pdf", "/login", "/logout", "/?action=delete"}
        self.assertFalse(any(path in forbidden or method != "GET" for method, path in self.requests), self.requests)

    def test_complete_site_aggregates_real_page_evidence_and_selected_packet(self):
        process, report, output = self.cli("complete")
        self.assertEqual(process.returncode, 1, process.stderr)
        site = self.assert_coverage_consistent(report)
        self.assertEqual((site["discovered"], site["scanned"], site["failed"], site["pending"]), (3, 3, 0, 0))
        self.assertTrue(site["complete"])
        self.assertEqual(site["limits"]["max_pages"], 100)
        self.assertEqual(report["metadata"]["run"]["status"], "completed")
        self.assertEqual({entry["url"] for entry in site["page_results"]}, {self.base + path for path in ("/", "/inner", "/sitemap-only")})
        self.assertEqual(report["errors"], [])
        self.assert_no_unsafe_visits()
        paths = Counter(path for method, path in self.requests if method == "GET")
        self.assertEqual(paths["/inner"], 1, "Duplicate links and fragments must share one audit")
        excluded = {entry["url"] for entry in site["excluded_urls"]}
        self.assertTrue({self.external_url, self.base + "/download.pdf", self.base + "/login",
                         self.base + "/logout", self.base + "/?action=delete"} <= excluded, excluded)
        failures = [item for item in report["findings"] if item["status"] == "fail"]
        shared = [item for item in failures if item.get("rule_id") == "axe-image-alt" and item["location"]["selector"] == "#shared-image"]
        self.assertEqual(len(shared), 2)
        self.assertEqual({item["location"]["url"] for item in shared}, {self.base + "/", self.base + "/inner"})
        self.assertEqual(len({item["stable_id"] for item in shared}), 2, "Same selector on different pages needs distinct evidence identities")
        inner = [item for item in failures if item.get("rule_id") == "axe-button-name" and item["location"]["selector"] == "#inner-only-button"]
        self.assertEqual(len(inner), 1)
        self.assertEqual(inner[0]["location"]["url"], self.base + "/inner")
        expected_types = {(item["engine"], item["rule_id"]) for item in failures}
        self.assertEqual(report["summary"]["problem_types"], len(expected_types))
        self.assertEqual(report["summary"]["problem_occurrences"], len(failures))
        self.assertLess(report["summary"]["problem_types"], report["summary"]["problem_occurrences"])
        self.assert_report_browser(report, output, shared, inner[0])
        # Legacy --url remains one page, with stable identities shared with --site.
        self.requests.clear()
        legacy_process, legacy, _ = self.cli("single page compatibility", single_page=True)
        self.assertEqual(legacy_process.returncode, 1, legacy_process.stderr)
        self.assertEqual(legacy["summary"]["files_scanned"], 1)
        self.assertFalse(legacy["metadata"]["run"].get("site"))
        self.assertNotIn(("GET", "/inner"), self.requests)
        home_id = next(item["stable_id"] for item in shared if item["location"]["url"] == self.base + "/")
        self.assertIn(home_id, {item["stable_id"] for item in legacy["findings"]})

    def test_page_limit_is_partial_with_pending_urls_not_a_clean_site(self):
        process, report, _ = self.cli("page cap", max_pages=1)
        self.assertEqual(process.returncode, 2, process.stderr)
        site = self.assert_coverage_consistent(report)
        self.assertEqual(site["scanned"], 1)
        self.assertGreater(site["pending"], 0)
        self.assertFalse(site["complete"])
        self.assertEqual(site["limits"]["max_pages"], 1)
        self.assertEqual(report["metadata"]["run"]["status"], "partial")
        self.assertIn(site["stop_reason"], ("max-pages", "max_pages", "page-limit"))
        self.assert_no_unsafe_visits()

    def test_blocked_inner_page_preserves_successes_and_operational_failure(self):
        self.scenario = "mixed"
        self.write_fixture()
        process, report, _ = self.cli("one blocked")
        self.assertEqual(process.returncode, 2, process.stderr)
        site = self.assert_coverage_consistent(report)
        self.assertEqual((site["discovered"], site["scanned"], site["failed"], site["pending"]), (3, 2, 1, 0))
        self.assertFalse(site["complete"])
        self.assertEqual(report["metadata"]["run"]["status"], "partial")
        self.assertTrue(report["errors"])
        blocked = next(entry for entry in site["page_results"] if entry["url"] == self.base + "/blocked")
        self.assertTrue(blocked["reason"])
        self.assertTrue(any(item["status"] == "fail" and item["location"].get("url") == self.base + "/inner" for item in report["findings"]))
        self.assertFalse(any(item["status"] in ("pass", "fail") and item["location"].get("url") == self.base + "/blocked" for item in report["findings"]))
        self.assert_no_unsafe_visits()

    def test_all_blocked_is_not_performed_without_target_findings(self):
        self.scenario = "all-blocked"
        process, report, output = self.cli("all blocked")
        self.assertEqual(process.returncode, 2, process.stderr)
        site = self.assert_coverage_consistent(report)
        self.assertEqual((site["discovered"], site["scanned"], site["failed"], site["pending"]), (1, 0, 1, 0))
        self.assertFalse(site["complete"])
        self.assertEqual(report["metadata"]["run"]["status"], "not-performed")
        self.assertTrue(report["errors"])
        self.assertFalse(any(item["status"] in ("pass", "fail") for item in report["findings"]))
        html = (output / "accessibility-report.html").read_text(encoding="utf-8")
        self.assertNotIn('class="finding-select"', html)
        self.assert_no_unsafe_visits()

    def assert_report_browser(self, report, output, shared, selected):
        inputs = {"selected": selected["stable_id"], "url": selected["location"]["url"],
                  "sharedUrls": [item["location"]["url"] for item in shared],
                  "shared": [item["stable_id"] for item in shared],
                  "counts": {key: report["metadata"]["run"]["site"][key] for key in ("discovered", "scanned", "failed", "pending", "excluded")}}
        process = subprocess.run([NODE, "-e", r'''
const {chromium}=require('playwright');
function check(value,message){if(!value)throw new Error(message);}
(async()=>{const b=await chromium.launch({headless:true});try{
const page=await b.newPage({viewport:{width:1280,height:900}});const args=JSON.parse(process.argv[2]);
const external=[];await page.route(/^https?:/,r=>{external.push(r.request().url());return r.abort();});
await page.addInitScript(()=>Object.defineProperty(navigator,'clipboard',{value:{writeText:async text=>{window.copied=text;}}}));
await page.goto(process.argv[1]);
check(await page.locator('html').getAttribute('dir')==='rtl','RTL report missing');
const coverage=page.locator('#site-coverage');check(await coverage.isVisible(),'Site coverage absent from report header');
check((await coverage.boundingBox()).y<900,'Site coverage is not visible near top');
for(const [key,value] of Object.entries(args.counts)){
 const metric=coverage.locator('[data-site-metric="'+key+'"]');
 check(await metric.count()===1,'Missing unique metric '+key);
 check((await metric.innerText()).includes(String(value)),'Wrong coverage metric '+key);
}
const box=id=>page.locator('input.finding-select[value="'+id+'"]');
const grouped=await box(args.shared[0]).evaluate((el,id)=>el.closest('article.rule').querySelector('input.finding-select[value="'+id+'"]')!==null,args.shared[1]);
check(grouped,'Same rule on separate pages was not grouped together');
await box(args.selected).evaluate(el=>{for(let p=el.parentElement;p;p=p.parentElement)if(p.tagName==='DETAILS')p.open=true;});
await box(args.selected).check();await page.locator('#copy-selected').click();await page.waitForFunction(()=>typeof window.copied==='string');
const packet=await page.evaluate(()=>window.copied);
check(packet.includes(args.selected)&&packet.includes(args.url)&&packet.includes('#inner-only-button'),'Selected repair lacks page URL/evidence');
for(const id of args.shared)check(!packet.includes(id),'Unselected finding leaked into repair packet');
check(!packet.includes('#shared-image'),'Unselected evidence leaked into repair packet');
await box(args.shared[0]).locator('xpath=ancestor::article').locator('button.rule-packet').click();
await page.evaluate(()=>{window.copied=null;});await page.locator('#copy-packet-preview').click();
await page.waitForFunction(()=>typeof window.copied==='string');
const groupedPacket=await page.evaluate(()=>window.copied);
for(const id of args.shared)check(groupedPacket.includes(id),'Grouped packet lost selected occurrence');
for(const url of args.sharedUrls)check(groupedPacket.includes(url),'Grouped packet lost page URL');
check(!groupedPacket.includes(args.selected)&&!groupedPacket.includes('#inner-only-button'),'Grouped packet retained previous selection');
await page.setViewportSize({width:390,height:844});
check(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth+1),'Mobile report horizontally overflows');
check(external.length===0,'Standalone report attempted external loading');
console.log(JSON.stringify({ok:true}));
}finally{await b.close();}})().catch(e=>{console.error(e.stack);process.exit(1)});
''', (output / "accessibility-report.html").as_uri(), json.dumps(inputs)],
                                 cwd=REPO, env=self.env, capture_output=True, text=True, timeout=35)
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertTrue(json.loads(process.stdout)["ok"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
