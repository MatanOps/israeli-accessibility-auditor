"""Regression coverage for the neutral Hebrew report and distinct result counts."""

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")
sys.path.insert(0, str(REPO / "scripts"))
from html_report import render_html
from report import DISCLAIMER, FORMS, finding


def neutral_report():
    """Keep the input small, including stale summary and legacy disclaimer data."""
    items = []
    cases = [
        ("FAIL_A", "shared-rule", "axe", "fail"),
        ("FAIL_B", "shared-rule", "axe", "fail"),
        ("FAIL_C", "shared-rule", "rendered-dom", "fail"),
        ("FAIL_D", "another-rule", "axe", "fail"),
        ("WARNING_A", "shared-rule", "axe", "warning"),
        ("WARNING_B", "recommendation-rule", "static", "warning"),
        ("WARNING_C", "recommendation-rule", "static", "warning"),
        ("REVIEW_A", "contrast-rule", "axe", "human-review-required"),
        ("REVIEW_B", "contrast-rule", "axe", "human-review-required"),
        ("REVIEW_C", "human-keyboard", "manual-checklist", "human-review-required"),
        ("PASS_A", "passed-rule", "axe", "pass"),
        ("NOT_TESTED_A", "omitted-rule", "static", "not-tested"),
    ]
    for marker, rule, engine, status in cases:
        evidence_type = ("automatically-verified" if status in ("fail", "pass") else
                         "heuristic" if status == "warning" else "human-verification-required")
        item = finding(rule, FORMS, "serious" if status == "fail" else "info", status,
                       evidence_type, None, {"url": "https://fixture.example/", "selector": "#" + marker.lower()},
                       marker, "Neutral local fixture", "Verify this selected evidence")
        item.update(rule_id=rule, engine=engine, stable_id=marker.lower(), id=marker.lower())
        items.append(item)
    return {"version": "test", "scope": {"target": "https://fixture.example/", "rendered": True},
            "findings": items, "errors": [], "limitations": [],
            "summary": {"actionable_findings": 999, "by_status": {"fail": 999}},
            "metadata": {"run": {"status": "completed", "rendered": True, "untested": ["Other pages"]}},
            "disclaimer": "LEGACY ENGLISH DISCLAIMER: This report is not a certificate."}


class PlainReportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.env = dict(os.environ, PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1")
        cache = Path(os.environ.get("A11Y_AUDITOR_CACHE", str(REPO / ".runtime")))
        if "PLAYWRIGHT_BROWSERS_PATH" not in cls.env and (cache / "browsers").exists():
            cls.env["PLAYWRIGHT_BROWSERS_PATH"] = str(cache / "browsers")
        cls.browser_reason = None
        if not NODE:
            cls.browser_reason = "Node is unavailable"
        else:
            check = subprocess.run([NODE, "-e", "require('playwright').chromium.launch({headless:true}).then(b=>b.close()).catch(e=>{console.error(e.message);process.exit(1)})"],
                                   cwd=REPO, env=cls.env, capture_output=True, text=True, timeout=25)
            if check.returncode:
                cls.browser_reason = "Browser runtime unavailable: " + check.stderr[-300:]
        cls.temp = tempfile.TemporaryDirectory(prefix="plain Hebrew report ")
        cls.path = Path(cls.temp.name) / "report.html"
        cls.html = render_html(neutral_report())
        cls.path.write_text(cls.html, encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def browser(self, body):
        if self.browser_reason:
            self.skipTest(self.browser_reason)
        script = r'''
const {chromium}=require('playwright');
function check(value,message){if(!value)throw new Error(message);}
(async()=>{const browser=await chromium.launch({headless:true});try{
const page=await browser.newPage({viewport:{width:1280,height:900}});page.setDefaultTimeout(2500);
const errors=[],external=[];page.on('pageerror',e=>errors.push(e.message));
await page.route(/^https?:/,route=>{external.push(route.request().url());return route.abort();});
await page.goto(process.argv[1]);
''' + body + r'''
check(errors.length===0,'Report JavaScript errors: '+errors.join('; '));
check(external.length===0,'Standalone report requested remote resources');
console.log(JSON.stringify({ok:true}));
}finally{await browser.close();}})().catch(e=>{console.error(e.stack);process.exit(1)});
'''
        result = subprocess.run([NODE, "-e", script, self.path.as_uri()], cwd=REPO, env=self.env,
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(json.loads(result.stdout)["ok"])

    def test_neutral_header_canonical_hebrew_footer_and_requested_palette(self):
        footer = re.search(r'<footer\b[^>]*>(.*?)</footer>', self.html, re.S)
        self.assertIsNotNone(footer)
        self.assertIn(DISCLAIMER, footer.group(1))
        self.assertRegex(DISCLAIMER, r"[א-ת]")
        self.assertNotIn("LEGACY ENGLISH DISCLAIMER", footer.group(1))
        css = re.search(r"<style[^>]*>(.*?)</style>", self.html, re.S).group(1).lower()
        for color in ("#070707", "#c89a45", "#efe6d8"):
            self.assertIn(color, css)
        self.browser(r'''
check(await page.locator('header.page h1').innerText()==='Israeli Accessibility Auditor','Incorrect neutral product title');
check(await page.locator('header.page').getByText('בדיקות נגישות ותיקונים',{exact:true}).count()===1,'Missing exact Hebrew subtitle');
check(!/next\s*impact/i.test(await page.locator('body').innerText()),'Neutral report contains Next Impact marketing');
check(await page.locator('html').getAttribute('dir')==='rtl','Report lost RTL direction');
''')

    def test_summary_counts_rule_engine_pairs_separately_from_occurrences_and_statuses(self):
        self.browser(r'''
const expected=[['סוגי בעיות',3,4],['המלצות לבדיקה',2,3],['נושאים לבדיקה אנושית',2,null]];
check(await page.locator('header .metrics .metric').count()===3,'Expected three distinct outcome metrics');
for(const [label,types,occurrences] of expected){
const metric=page.locator('header .metrics .metric').filter({has:page.locator('.metric-label').getByText(label,{exact:true})});
check(await metric.count()===1,'Missing metric '+label);
check((await metric.locator('.metric-value').innerText()).trim()===String(types),'Wrong distinct engine/rule count for '+label);
if(occurrences!==null)check((await metric.locator('.metric-detail').innerText()).includes(occurrences+' מופעים'),'Wrong occurrence count for '+label);
}
check(!(await page.locator('header .metrics').innerText()).includes('999'),'Renderer trusted obsolete summary counts');
''')

    def test_failures_are_visible_and_optional_recommendations_and_review_start_collapsed(self):
        self.browser(r'''
for(const selector of ['#recommendations','#human-review']){
check(await page.locator('details'+selector).count()===1,'Missing native disclosure '+selector);
check(!await page.locator(selector).evaluate(el=>el.open),'Optional section initially expanded: '+selector);
}
const card=marker=>page.locator('.rule').filter({has:page.locator('input.finding-select[value="'+marker+'"]')});
for(const marker of ['fail_a','fail_c','fail_d'])check(await card(marker).locator('button.rule-packet').isVisible(),'Failure action initially hidden: '+marker);
check(!await card('warning_a').locator('button.rule-packet').isVisible(),'Recommendation initially exposed');
check(!await card('review_a').locator('button.rule-packet').isVisible(),'Human review initially exposed');
await page.locator('#recommendations > summary').focus();await page.keyboard.press('Enter');
check(await card('warning_a').locator('button.rule-packet').isVisible(),'Keyboard could not expand recommendations');
await page.locator('#human-review > summary').click();
check(await card('review_a').locator('button.rule-packet').isVisible(),'Human review cannot be opened');
''')

    def test_desktop_mobile_layout_and_card_prompt_remain_usable(self):
        self.browser(r'''
for(const width of [1280,390]){
await page.setViewportSize({width,height:900});
check(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'Initial horizontal overflow at '+width);
const card=page.locator('.rule').filter({has:page.locator('input.finding-select[value="fail_d"]')});
await card.locator('button.rule-packet').click();
check(await page.locator('#packet-fallback').isVisible(),'Card prompt hidden at '+width);
check(await page.locator('#packet-text').evaluate(el=>el===document.activeElement&&el.scrollTop===0),'Prompt focus/start lost at '+width);
const packet=await page.locator('#packet-text').inputValue();
check(packet.includes('FAIL_D')&&!packet.includes('FAIL_A')&&!packet.includes('WARNING_A')&&!packet.includes('REVIEW_A'),'Card prompt leaked unrelated selected evidence');
check(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'Prompt horizontal overflow at '+width);
}
''')


if __name__ == "__main__":
    unittest.main(verbosity=2)
