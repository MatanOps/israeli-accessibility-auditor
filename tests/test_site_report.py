"""Site reports expose honest coverage while preserving finding selection data."""

import copy
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
import re
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from html_report import render_html
from compare import compare_reports
from report import IMAGES, build_report, finding, render_markdown


def site_report(status="partial", stop_reason="page-limit"):
    items = []
    for url, selector in (("https://site.test/", "#first"), ("https://site.test/", "#second"),
                          ("https://site.test/about", "#third")):
        item = finding("image-alt", IMAGES, "serious", "fail", "automatically-verified", "1.1.1",
                       {"url": url, "selector": selector}, "<img id='" + selector + "'>",
                       "Missing image alternative", "Add useful alternative text")
        item.update(engine="axe", rule_id="image-alt")
        items.append(item)
    site = {"discovered": 4, "scanned": 2, "failed": 1, "pending": 1, "excluded": 1,
            "complete": False, "stop_reason": stop_reason,
            "limits": {"max_pages": 2, "max_seconds": 600},
            "page_results": [
                {"url": "https://site.test/", "final_url": "https://site.test/", "status": "completed", "reason": ""},
                {"url": "https://site.test/about", "final_url": "https://site.test/about", "status": "completed", "reason": ""},
                {"url": "https://site.test/blocked", "final_url": "https://site.test/blocked", "status": "failed", "reason": "HTTP 403"}],
            "excluded_urls": [{"url": "https://outside.test/", "reason": "external host"}],
            "pending_urls": ["https://site.test/contact"]}
    if status == "completed":
        site.update(discovered=2, failed=0, pending=0, complete=True, stop_reason="completed")
        site["page_results"] = site["page_results"][:2]
        site["pending_urls"] = []
    elif status == "not-performed":
        site.update(discovered=1, scanned=0, failed=1, pending=0, stop_reason="no-pages")
        site["page_results"] = site["page_results"][2:]
        site["pending_urls"] = []
    return build_report("https://site.test/", "site", items, site["scanned"], [],
                        {"run": {"status": status, "rendered": True, "site": site}})


class SiteReportTests(unittest.TestCase):
    def test_partial_coverage_is_prominent_and_keeps_valid_findings(self):
        report = site_report()
        output = render_html(report)
        header = output.split("</header>")[0]
        self.assertIn('id="site-coverage"', header)
        self.assertIn("2 מתוך 4 עמודים שהתגלו נבדקו", re.sub(r"<[^>]+>", "", header))
        self.assertIn("הסריקה חלקית", header)
        self.assertIn("הגיעה למספר העמודים", header)
        for key, count in (("scanned", 2), ("discovered", 4), ("failed", 1), ("pending", 1), ("excluded", 1)):
            self.assertRegex(header, r'data-site-count="' + key + r'">' + str(count) + "</(?:strong|span)>")
        self.assertEqual(report["summary"]["problem_occurrences"], 3)
        self.assertEqual(report["summary"]["problem_types"], 1)
        self.assertEqual(output.count('<article class="rule rule-verified">'), 1)
        self.assertIn("3 מופעים · 2 עמודים", output)
        self.assertIn("הכנת בקשה לתיקון זה", output)

    def test_coverage_disclosure_includes_all_urls_and_starts_closed(self):
        output = render_html(site_report())
        self.assertIn('<details id="site-coverage-details">', output)
        details = output.split('<details id="site-coverage-details">')[1].split("</details>")[0]
        for url in ("https://site.test/", "https://site.test/about", "https://site.test/blocked",
                    "https://site.test/contact", "https://outside.test/"):
            self.assertIn(url, details)
        self.assertIn("HTTP 403", details)
        self.assertIn("external host", details)
        self.assertIn("ממתין לבדיקה", details)
        self.assertIn("עד 2 עמודים, עד 600 שניות", details)
        for identity in ("recommendations", "human-review", "group-pass"):
            self.assertIn('<details id="' + identity + '">', output)

    def test_success_claim_is_limited_to_discovered_pages(self):
        report = site_report("completed")
        output = render_html(report)
        self.assertIn("2 מתוך 2 עמודים שהתגלו נבדקו", re.sub(r"<[^>]+>", "", output))
        self.assertIn("הסריקה הסתיימה עבור העמודים שהתגלו", output)
        self.assertIn("ייתכנו עמודים נוספים שלא התגלו", output)
        self.assertNotIn("הבדיקה הושלמה לעמוד ולמצב המתועדים בלבד", output)
        self.assertNotIn("Playwright rendered one page", " ".join(report["limitations"]))
        self.assertIn("Undiscovered pages", " ".join(report["limitations"]))

    def test_site_manual_and_error_records_use_url_locations_without_source_limitations(self):
        report = build_report("https://site.test/", "site", [], 1, ["A second page failed"],
                              {"run": {"status": "partial", "rendered": True,
                                       "site": {"complete": False}}})
        self.assertTrue(report["findings"])
        for item in report["findings"]:
            self.assertEqual(item["location"], {"url": "https://site.test/", "selector": None})
            self.assertNotIn("file", item["location"])
        limitations = " ".join(report["limitations"])
        self.assertNotIn("JSX", limitations)
        self.assertNotIn("single HTTP response", limitations)
        self.assertNotIn("Static analysis only", limitations)

    def test_multiple_page_errors_remain_valid_baseline_records(self):
        metadata = {"run": {"status": "partial", "rendered": True, "site": {"complete": False}}}
        before = build_report("https://site.test/", "site", [], 1,
                              ["Page A failed", "Page B failed", "Page A failed"], metadata)
        errors = [item for item in before["findings"] if item["rule_id"] == "coverage-operational-error"]
        self.assertEqual(len(errors), 3)
        self.assertEqual(len({item["stable_id"] for item in errors}), 3)
        after = site_report("completed")
        comparison = compare_reports(before, after)
        old_errors = [item for item in comparison["not_tested"]
                      if item["before"] and item["before"]["rule_id"] == "coverage-operational-error"]
        self.assertEqual(len(old_errors), 3)
        self.assertEqual(comparison["fixed_verified"], [])

    def test_report_grouping_preserves_ids_and_all_locations(self):
        report = site_report()
        before = copy.deepcopy(report)
        output = render_html(report)
        self.assertEqual(report, before)
        embedded = re.search(r'<script type="application/json" id="audit-report-data">(.*?)</script>', output, re.S)
        self.assertEqual(json.loads(embedded.group(1)), report)
        for item in report["findings"]:
            self.assertIn('value="' + item["stable_id"] + '"', output)
        for selector in ("#first", "#second", "#third"):
            self.assertIn(selector, output)

    def test_failed_site_has_no_actionable_findings_or_packet(self):
        report = site_report("not-performed")
        output = render_html(report)
        self.assertIn("0 מתוך 1 עמודים שהתגלו נבדקו", re.sub(r"<[^>]+>", "", output))
        self.assertIn("הסריקה לא בוצעה", output)
        self.assertNotIn('class="finding-select"', output)
        self.assertNotIn('id="select-verified"', output)
        self.assertEqual(report["summary"]["problem_occurrences"], 0)
        self.assertIn("לא בוצעה — אין תוצאות בדיקה", render_markdown(report))

    def test_markdown_has_plain_hebrew_site_summary_before_technical_detail(self):
        output = render_markdown(site_report())
        self.assertLess(output.index("סיכום בדיקת האתר"), output.index("Scope and limitations"))
        self.assertIn("2 מתוך 4 עמודים שהתגלו נבדקו", output)
        self.assertIn("סוגי בעיות: 1 (3 מופעים)", output)
        self.assertIn("כיסוי לא הושלם", output)
        self.assertIn("יש להשלים גם את סריקת העמודים", output)

    def test_untrusted_site_urls_reasons_and_limits_are_escaped(self):
        report = site_report()
        site = report["metadata"]["run"]["site"]
        payload = '<img src=x onerror="alert(1)">'
        site["page_results"][0].update(url=payload, reason=payload, final_url=payload)
        site["stop_reason"] = payload
        site["limits"]["max_pages"] = payload
        output = render_html(report)
        self.assertNotIn(payload, output)
        self.assertIn("&lt;img", output)
        self.assertNotIn(payload, render_markdown(report))

    def test_single_page_report_keeps_existing_scope(self):
        report = site_report("completed")
        del report["metadata"]["run"]["site"]
        output = render_html(report)
        self.assertNotIn('id="site-coverage"', output)
        self.assertIn("הבדיקה הושלמה לעמוד ולמצב המתועדים בלבד", output)
        self.assertNotIn("סיכום בדיקת האתר", render_markdown(report))

    def test_coverage_link_opens_list_and_moves_focus_for_mouse_and_keyboard(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("Node is unavailable")
        env = dict(os.environ)
        root = Path(__file__).resolve().parents[1]
        cache = Path(env.get("A11Y_AUDITOR_CACHE", str(root / ".runtime")))
        if "PLAYWRIGHT_BROWSERS_PATH" not in env and (cache / "browsers").exists():
            env["PLAYWRIGHT_BROWSERS_PATH"] = str(cache / "browsers")
        ready = subprocess.run([node, "-e", "require('playwright').chromium.launch().then(b=>b.close()).catch(e=>process.exit(1))"],
                               cwd=root, env=env, capture_output=True, timeout=25)
        if ready.returncode:
            self.skipTest("Prepared browser runtime unavailable")
        with tempfile.TemporaryDirectory(prefix="site coverage keyboard ") as directory:
            for state in ("partial", "not-performed"):
                Path(directory, state + ".html").write_text(render_html(site_report(state)), encoding="utf-8")
            script = r'''
const {chromium}=require('playwright');
const {pathToFileURL}=require('url');
const path=require('path');
function check(value,message){if(!value)throw new Error(message);}
(async()=>{const browser=await chromium.launch();try{
  for(const state of ['partial','not-performed'])for(const width of [1280,390]){
    const page=await browser.newPage({viewport:{width,height:900}});
    const errors=[];page.on('pageerror',e=>errors.push(e.message));
    await page.goto(pathToFileURL(path.join(process.argv[1],state+'.html')).href);
    const details=page.locator('#site-coverage-details');
    const link=page.locator('#site-coverage a');
    check(!await details.evaluate(el=>el.open),'Coverage details initially expanded');
    check(await page.locator('.rule-verified details[open]').count()===0,'Technical details initially expanded');
    check(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'Horizontal overflow');
    if(state==='partial')check(await page.locator('.rule-verified h3').first().evaluate(el=>el.getBoundingClientRect().top)<950,'First problem buried below long header');
    await link.focus();await page.keyboard.press('Enter');
    check(await details.evaluate(el=>el.open),'Keyboard link did not open coverage list');
    check(await details.locator('summary').evaluate(el=>el===document.activeElement),'Keyboard link did not move focus');
    await page.keyboard.press('Enter');
    check(!await details.evaluate(el=>el.open),'Native keyboard disclosure no longer closes');
    await link.click();
    check(await details.evaluate(el=>el.open),'Pointer link did not open coverage list');
    check(await details.locator('summary').evaluate(el=>el===document.activeElement),'Pointer link did not move focus');
    check(errors.length===0,'JavaScript errors: '+errors.join('; '));
    await page.close();
  }
}finally{await browser.close();}})().catch(e=>{console.error(e);process.exit(1)});
'''
            result = subprocess.run([node, "-e", script, directory], cwd=root, env=env,
                                    capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
