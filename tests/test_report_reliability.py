"""Report semantics distinguish audit evidence from omitted or uncertain scope."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from hebrew import finding_copy
from html_report import render_html
from repair_packet import build_packet, browser_script
from report import KEYBOARD, build_report, finding, render_markdown


def sample(rule="axe-target-size", status="fail", evidence="automatically-verified"):
    item = finding(rule, KEYBOARD, "serious", status, evidence, "2.5.8",
                   {"url": "https://fixture.example/", "selector": "#small"},
                   "ONLY_TARGET_EVIDENCE", "Small target", "Verify target size")
    item.update(rule_id=rule, engine="axe")
    return item


def report_for(findings, **run):
    metadata = {"run": {"status": "completed", "rendered": True,
                        "untested": ["Keyboard journeys", "Authenticated states"]}}
    metadata["run"].update(run)
    return build_report("https://fixture.example/", "url", findings, 1, [], metadata)


class ReportReliabilityTests(unittest.TestCase):
    def test_evidence_counts_do_not_mix_rules_occurrences_or_omitted_scope(self):
        incomplete = sample("axe-color-contrast", "human-review-required", "human-verification-required")
        passed = sample("axe-label", "pass")
        report = report_for([sample(), incomplete, passed])
        summary = report["summary"]
        self.assertEqual(summary["automatic_fail_occurrences"], 1)
        self.assertEqual(summary["engine_incomplete_occurrences"], 1)
        self.assertEqual(summary["general_manual_tasks"], 7)
        self.assertEqual(summary["untested_scope_count"], 2)
        self.assertEqual(summary["not_tested"], 0)
        self.assertEqual(summary["passed_checks"], 1)
        self.assertNotIn("score", summary)
        html = render_html(report)
        self.assertIn('id="group-incomplete"', html)
        self.assertIn('id="group-human"', html)
        self.assertIn("תחומים שלא נכללו בסריקה", html)
        self.assertNotIn("לא נבדק: 0", html)
        self.assertLess(html.index("סוגי בעיות"), html.index("<main>"))
        self.assertIn('<strong class="metric-value">1</strong><span class="metric-label">סוגי בעיות</span>', html)
        self.assertIn('<strong class="metric-value">0</strong><span class="metric-label">המלצות לבדיקה</span>', html)
        self.assertIn('<strong class="metric-value">8</strong><span class="metric-label">נושאים לבדיקה אנושית</span>', html)
        self.assertIn("zero never means complete coverage", render_markdown(report))

    def test_best_practice_is_not_a_verified_wcag_failure(self):
        item = sample("axe-heading-order")
        item.update(standards_basis="best-practice", wcag_criterion=None)
        report = report_for([item])
        actual = next(f for f in report["findings"] if f["rule_id"] == "axe-heading-order")
        self.assertEqual((actual["status"], actual["evidence_type"]), ("warning", "heuristic"))
        self.assertEqual(report["summary"]["automatic_fail_occurrences"], 0)
        self.assertEqual(report["summary"]["best_practice_occurrences"], 1)
        html = render_html(report)
        section = html.split('id="group-best-practice"')[1].split("</section>")[0]
        self.assertIn("axe-heading-order", section)
        self.assertIn("אינה כשל WCAG מאומת", section)
        self.assertIn("המלצה לשיפור", finding_copy(actual)["title"])

    def test_rendered_limitations_do_not_claim_static_only_analysis(self):
        report = report_for([])
        text = " ".join(report["limitations"])
        self.assertNotIn("Static analysis only", text)
        self.assertNotIn("narrow static fact", text)
        self.assertNotIn("Colour contrast is computed only", text)
        self.assertIn("Rendered contrast", text)
        self.assertFalse(any("Static scanning cannot operate" in f["explanation"] for f in report["findings"]))

    def test_best_practice_incomplete_remains_an_undetermined_engine_occurrence(self):
        item = sample("axe-landmark-one-main", "human-review-required", "human-verification-required")
        item.update(standards_basis="best-practice", wcag_criterion=None)
        report = report_for([item])
        html = render_html(report)
        incomplete = html.split('id="group-incomplete"')[1].split("</section>")[0]
        recommendations = html.split('id="recommendations"')[1].split('id="human-review"')[0]
        self.assertIn("axe-landmark-one-main", incomplete)
        self.assertNotIn("axe-landmark-one-main", recommendations)
        self.assertNotIn('id="group-best-practice"', html)
        self.assertIn('<strong class="metric-value">0</strong><span class="metric-label">המלצות לבדיקה</span>', html)
        self.assertIn('<strong class="metric-value">8</strong><span class="metric-label">נושאים לבדיקה אנושית</span>', html)
        self.assertEqual(report["summary"]["engine_incomplete_occurrences"], 1)
        self.assertEqual(report["summary"]["best_practice_occurrences"], 0)

    def test_not_performed_cannot_represent_the_block_page_as_target_evidence(self):
        report = report_for([sample(), sample("axe-label", "pass")], status="not-performed",
                            readiness={"reason": "CHALLENGE_REASON", "next_step": "AUTHORIZED_RETRY"})
        self.assertEqual(report["scope"]["files_scanned"], 0)
        self.assertEqual(report["findings"], [])
        self.assertEqual(report["summary"]["general_manual_tasks"], 0)
        html = render_html(report)
        self.assertIn("העמוד המבוקש לא נבדק", html)
        self.assertIn("CHALLENGE_REASON", html)
        self.assertIn("AUTHORIZED_RETRY", html)
        self.assertNotIn("לא נמצאו ליקויים מאומתים", html)
        self.assertNotIn('<input type="checkbox"', html)
        self.assertNotIn('id="copy-selected"', html)
        self.assertNotIn("ONLY_TARGET_EVIDENCE", html)
        self.assertNotIn("axe-core tested", " ".join(report["limitations"]))
        with self.assertRaisesRegex(ValueError, "העמוד המבוקש לא נבדק"):
            build_packet(report, ["axe-target-size"])

    def test_browser_builder_rejects_forged_selection_on_not_performed(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("Node required for browser packet guard")
        report = {"metadata": {"run": {"status": "not-performed"}}, "findings": [sample()]}
        script = "global.window={};\n" + browser_script()
        script += "\ntry { window.RepairPack.build(" + json.dumps(report) + ", ['axe-target-size']); process.exit(2); } catch (e) { process.stdout.write(e.message); }"
        result = subprocess.run([node, "-e", script], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("העמוד המבוקש לא נבדק", result.stdout)

    def test_copy_explains_target_size_and_verification_first_access(self):
        copy = finding_copy(sample())
        self.assertIn("אזורי לחיצה", copy["title"])
        self.assertIn("המרווח", copy["action"])
        self.assertNotIn("ARIA", copy["title"])
        report = report_for([sample()])
        packet = build_packet(report, ["axe-target-size"])
        self.assertIn("WordPress/Elementor", packet)
        self.assertIn("התוכן והתבניות", packet)
        self.assertIn("בקשת בדיקה בלבד", packet)
        self.assertIn("היעלמות ממצא לבדה אינה תיקון", packet)


if __name__ == "__main__":
    unittest.main()
