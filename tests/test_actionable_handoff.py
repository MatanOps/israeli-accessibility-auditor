"""Exercise visible, selection-scoped repair instructions in the standalone report."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"
NODE = shutil.which("node")
sys.path.insert(0, str(SCRIPTS))
from html_report import render_html
from report import FORMS, build_report, finding


class ActionableHandoffTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.env = dict(os.environ, PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1")
        cache = Path(os.environ.get("A11Y_AUDITOR_CACHE", str(REPO / ".runtime")))
        if "PLAYWRIGHT_BROWSERS_PATH" not in cls.env and (cache / "browsers").exists():
            cls.env["PLAYWRIGHT_BROWSERS_PATH"] = str(cache / "browsers")
        if not NODE:
            raise unittest.SkipTest("Node is unavailable; actionable handoff was not browser-tested")
        check = subprocess.run([NODE, "-e", "require('playwright').chromium.launch({headless:true}).then(b=>b.close()).catch(e=>{console.error(e.message);process.exit(1)})"],
                               cwd=REPO, env=cls.env, text=True, capture_output=True, timeout=25)
        if check.returncode:
            raise unittest.SkipTest("Browser runtime unavailable: " + check.stderr[-300:])
        cls.temp = tempfile.TemporaryDirectory(prefix="actionable handoff ")
        cls.root = Path(cls.temp.name)
        items = []
        for marker, rule, status in (
                ("VERIFIED_ALPHA", "axe-label-content-name-mismatch", "fail"),
                ("VERIFIED_BETA", "axe-label-content-name-mismatch", "fail"),
                ("UNCERTAIN_GAMMA", "axe-color-contrast", "human-review-required"),
                ("HEURISTIC_DELTA", "axe-heading-order", "warning"),
                ("PASS_EPSILON", "axe-button-name", "pass"),
                ("UNTESTED_ZETA", "test-omitted", "not-tested")):
            evidence_type = ("human-verification-required" if status in ("human-review-required", "not-tested")
                             else "heuristic" if status == "warning" else "automatically-verified")
            item = finding(rule, FORMS, "info" if status == "pass" else "serious", status,
                           evidence_type, "2.5.3", {"url": "https://fixture.example/", "selector": "#" + marker.lower()},
                           marker, "Local fixture evidence " + marker, "Verify the selected local evidence")
            item.update(rule_id=rule, engine="axe")
            items.append(item)
        metadata = {"run": {"status": "completed", "rendered": True, "untested": ["Other pages"]}}
        cls.report = build_report("https://fixture.example/", "url", items, 1, [], metadata)
        cls.ids = {item["evidence"]: item["stable_id"] for item in cls.report["findings"] if item["evidence"] in
                   {"VERIFIED_ALPHA", "VERIFIED_BETA", "UNCERTAIN_GAMMA", "HEURISTIC_DELTA", "PASS_EPSILON", "UNTESTED_ZETA"}}
        cls.report_path = cls.root / "actionable-report.html"
        cls.report_path.write_text(render_html(cls.report), encoding="utf-8")
        blocked = build_report("https://fixture.example/", "url", items, 0, ["Local challenge fixture"],
                               {"run": {"status": "not-performed", "rendered": False}})
        cls.blocked_path = cls.root / "blocked-report.html"
        cls.blocked_path.write_text(render_html(blocked), encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def browser(self, body, path=None):
        script = r'''
const {chromium}=require('playwright');const fs=require('fs');
function check(value,message){if(!value)throw new Error(message);}
(async()=>{const browser=await chromium.launch({headless:true});try{
const page=await browser.newPage({acceptDownloads:true,viewport:{width:1280,height:900}});
page.setDefaultTimeout(2500);
const external=[];await page.route(/^https?:/,route=>{external.push(route.request().url());return route.abort();});
await page.addInitScript(()=>Object.defineProperty(navigator,'clipboard',{value:{writeText:async value=>{window.copied=value;}}}));
await page.goto(process.argv[1]);const ids=JSON.parse(process.argv[2]);
const boxFor=marker=>page.locator('input.finding-select[value="'+ids[marker]+'"]');
const cardFor=marker=>page.locator('.rule').filter({has:boxFor(marker)});
async function select(marker){const box=boxFor(marker);await box.evaluate(el=>{for(let p=el.parentElement;p;p=p.parentElement){if(p.tagName==='DETAILS')p.open=true;}});await box.check();}
async function selected(){return page.locator('input.finding-select:checked').evaluateAll(elements=>elements.map(el=>el.value).sort());}
async function assertPacket(included,excluded){
check(await page.locator('#packet-fallback').isVisible(),'Prompt is not visible');
const text=await page.locator('#packet-text').inputValue();
for(const marker of included)check(text.includes(marker),'Missing selected evidence '+marker);
for(const marker of excluded)check(!text.includes(marker),'Unselected evidence included '+marker);
return text;
}
''' + body + r'''
check(external.length===0,'Standalone report requested remote resources');
console.log(JSON.stringify({ok:true}));
}finally{await browser.close();}})().catch(e=>{console.error(e.stack);process.exit(1)});
'''
        process = subprocess.run([NODE, "-e", script, (path or self.report_path).as_uri(), json.dumps(self.ids)],
                                 cwd=REPO, env=self.env, capture_output=True, text=True, timeout=20)
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertTrue(json.loads(process.stdout)["ok"])

    def test_preview_exposes_current_selection_copy_download_and_invalidates_stale_text(self):
        self.browser(r'''
check(await page.locator('#preview-selected').count()===1,'Missing explicit preview action');
await select('VERIFIED_ALPHA');
await page.locator('#preview-selected').focus();await page.keyboard.press('Enter');
const preview=await assertPacket(['VERIFIED_ALPHA'],['VERIFIED_BETA','UNCERTAIN_GAMMA','HEURISTIC_DELTA','PASS_EPSILON']);
check(await page.locator('#packet-text').evaluate(el=>el===document.activeElement),'Prompt did not receive keyboard focus');
check(await page.locator('#packet-text').evaluate(el=>el.selectionStart===0&&el.selectionEnd===0&&el.scrollTop===0),'Prompt opened at the end instead of the actionable introduction');
await page.locator('#copy-selected').click();await page.waitForFunction(()=>typeof window.copied==='string');
check(await page.evaluate(()=>window.copied)===preview,'Copied packet differs from visible prompt');
const pending=page.waitForEvent('download');await page.locator('#download-selected').click();
const download=await pending;check(fs.readFileSync(await download.path(),'utf8')===preview,'Download differs from visible prompt');
check(await page.locator('#copy-packet-preview').isVisible(),'Visible prompt lacks its own copy action');
check(await page.locator('#download-packet-preview').isVisible(),'Visible prompt lacks its own download action');
await page.evaluate(()=>{window.copied=null;});
await page.locator('#copy-packet-preview').focus();await page.keyboard.press('Enter');
await page.waitForFunction(()=>typeof window.copied==='string');
check(await page.evaluate(()=>window.copied)===preview,'Prompt-panel copy differs from current selected packet');
const panelPending=page.waitForEvent('download');await page.locator('#download-packet-preview').click();
const panelDownload=await panelPending;
check(fs.readFileSync(await panelDownload.path(),'utf8')===preview,'Prompt-panel download differs from current selected packet');

await select('UNCERTAIN_GAMMA');
check(!await page.locator('#packet-fallback').isVisible(),'Manual selection change retained stale preview');
check(!await page.locator('#copy-packet-preview').isVisible()&&!await page.locator('#download-packet-preview').isVisible(),'Stale prompt-panel actions remain visible');
check(await page.locator('#packet-text').inputValue()==='','Manual change retained stale prompt text');
await page.locator('#preview-selected').click();await assertPacket(['VERIFIED_ALPHA','UNCERTAIN_GAMMA'],['VERIFIED_BETA','HEURISTIC_DELTA']);
await page.locator('#clear-selected').click();
check((await selected()).length===0,'Clear retained selection');
check(!await page.locator('#packet-fallback').isVisible(),'Clear retained visible prompt');
check(await page.locator('#packet-text').inputValue()==='','Clear retained prompt text');
await page.locator('#preview-selected').click();
check(!await page.locator('#packet-fallback').isVisible(),'Empty preview revived stale prompt');
''')

    def test_rule_action_replaces_selection_with_its_own_occurrences_and_focuses_prompt(self):
        self.browser(r'''
const nonPass=page.locator('.rule:not(.rule-pass):not(.rule-untested)');
for(let i=0;i<await nonPass.count();i++)check(await nonPass.nth(i).locator('button.rule-packet').count()===1,'Non-pass rule missing direct handoff');
check(await page.locator('.rule-pass button.rule-packet, .rule-untested button.rule-packet').count()===0,'Passed or untested rule offers repair action');
await select('UNCERTAIN_GAMMA');await select('HEURISTIC_DELTA');
const action=cardFor('VERIFIED_ALPHA').locator('button.rule-packet');
check(await action.innerText()==='הכנת בקשה לתיקון זה','Verified button label is not actionable');
await action.focus();await page.keyboard.press('Enter');
check(JSON.stringify(await selected())===JSON.stringify([ids.VERIFIED_ALPHA,ids.VERIFIED_BETA].sort()),'Rule action merged unrelated prior selections');
await assertPacket(['VERIFIED_ALPHA','VERIFIED_BETA'],['UNCERTAIN_GAMMA','HEURISTIC_DELTA','PASS_EPSILON']);
check(await page.locator('#packet-text').evaluate(el=>el===document.activeElement),'Rule prompt did not receive focus');
check(await page.locator('#packet-text').evaluate(el=>el.selectionStart===0&&el.selectionEnd===0&&el.scrollTop===0),'Prompt opened at the end instead of the actionable introduction');
await page.setViewportSize({width:390,height:844});
check(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'Mobile layout overflows horizontally');
check(await page.locator('#packet-text').isVisible(),'Mobile prompt is hidden');
''')

    def test_verified_action_discards_uncertain_selection_and_incomplete_action_requests_review(self):
        self.browser(r'''
await select('UNCERTAIN_GAMMA');await select('HEURISTIC_DELTA');
await page.locator('#select-verified').click();
check(JSON.stringify(await selected())===JSON.stringify([ids.VERIFIED_ALPHA,ids.VERIFIED_BETA].sort()),'Verified action retained uncertain findings');
await assertPacket(['VERIFIED_ALPHA','VERIFIED_BETA'],['UNCERTAIN_GAMMA','HEURISTIC_DELTA']);
const incomplete=cardFor('UNCERTAIN_GAMMA').locator('button.rule-packet');
const label=await incomplete.innerText();
check(label==='הכנת בקשה לבדיקה זו','Incomplete button does not request verification');
check(!/תיקון מאומת|ליקוי מאומת/.test(label),'Incomplete button claims confirmed repair evidence');
await incomplete.click();
check(JSON.stringify(await selected())===JSON.stringify([ids.UNCERTAIN_GAMMA]),'Incomplete action retained previous confirmed findings');
const packet=await assertPacket(['UNCERTAIN_GAMMA'],['VERIFIED_ALPHA','VERIFIED_BETA','HEURISTIC_DELTA']);
check(packet.includes('human-review-required'),'Incomplete status missing from handoff');
check(/בקשת בדיקה בלבד|בדיקה ואימות|נדרש.*אימות/.test(packet),'Incomplete handoff lacks verification instruction');
''')

    def test_not_performed_has_no_handoff_controls_or_findings(self):
        self.browser(r'''
check((await page.locator('body').innerText()).includes('העמוד המבוקש לא נבדק'),'Missing operational failure explanation');
for(const selector of ['#preview-selected','button.rule-packet','#copy-selected','#download-selected','#copy-packet-preview','#download-packet-preview','#select-verified','input.finding-select'])check(await page.locator(selector).count()===0,'Blocked report exposes repair control '+selector);
check(!(await page.locator('body').innerText()).includes('VERIFIED_ALPHA'),'Blocked report exposes target evidence');
''', self.blocked_path)


if __name__ == "__main__":
    unittest.main(verbosity=2)
