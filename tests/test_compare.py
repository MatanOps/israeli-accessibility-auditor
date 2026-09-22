"""Regression tests for evidence-preserving baseline comparison."""

import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from compare import compare_reports


URL = "https://example.test/page"


def finding(identity="old", status="fail", selector="#submit", **extra):
    result = {"id": identity, "stable_id": identity, "rule_id": "axe-button-name", "engine": "axe",
              "status": status, "evidence_type": "automatically-verified",
              "location": {"url": URL, "selector": selector}}
    result.update(extra)
    return result


def report(findings):
    return {"findings": findings, "scope": {"target": URL}, "errors": [], "metadata": {"run": {
        "status": "completed", "rendered": True, "original_url": URL, "final_url": URL,
        "pages": [URL], "states": ["initial-render"], "engines": {"playwright": "1.63.0", "axe": "4.13.0"},
        "observed_selectors": {"#submit": True},
    }}}


class ComparisonTests(unittest.TestCase):
    def setUp(self):
        self.before = report([finding()])
        self.after = report([finding("pass-button-name", "pass")])

    def test_same_rule_and_selector_under_complete_coverage_proves_fix(self):
        result = compare_reports(self.before, self.after)
        self.assertEqual([x["identity"] for x in result["fixed_verified"]], ["old"])
        self.assertEqual(result["new"], [])

    def test_pass_locations_list_is_supported(self):
        self.after["findings"][0]["location"] = {"url": URL, "selector": None}
        self.after["findings"][0]["locations"] = [{"url": URL, "selector": "#submit"}]
        self.assertEqual(len(compare_reports(self.before, self.after)["fixed_verified"]), 1)

    def test_removed_unknown_or_string_truthy_selector_is_not_tested(self):
        for observed in ({"#submit": False}, {}, {"#submit": "true"}, None):
            with self.subTest(observed=observed):
                self.after["metadata"]["run"]["observed_selectors"] = observed
                result = compare_reports(self.before, self.after)
                self.assertEqual(len(result["not_tested"]), 1)
                self.assertEqual(result["fixed_verified"], [])

    def test_reduced_or_unknown_execution_coverage_never_proves_fix(self):
        cases = (("status", "partial"), ("status", "not-performed"), ("rendered", False),
                 ("pages", []), ("pages", [URL + "/other"]), ("states", []),
                 ("states", ["different-state"]), ("engines", {}),
                 ("engines", {"axe": "different"}), ("original_url", URL + "?other"),
                 ("final_url", URL + "/other"))
        for key, value in cases:
            with self.subTest(key=key, value=value):
                after = copy.deepcopy(self.after)
                after["metadata"]["run"][key] = value
                result = compare_reports(self.before, after)
                self.assertEqual(result["fixed_verified"], [])
                self.assertEqual(len(result["not_tested"]), 1)

    def test_larger_scope_does_not_reduce_prior_coverage(self):
        self.after["metadata"]["run"]["pages"].append(URL + "/extra")
        self.after["metadata"]["run"]["states"].append("dialog-open")
        self.assertEqual(len(compare_reports(self.before, self.after)["fixed_verified"]), 1)

    def test_operational_error_or_wrong_target_is_not_tested(self):
        for change in ("error", "target"):
            after = copy.deepcopy(self.after)
            if change == "error":
                after["errors"] = ["Browser navigation failed"]
            else:
                after["scope"]["target"] = URL + "/other"
            self.assertEqual(len(compare_reports(self.before, after)["not_tested"]), 1)

    def test_absence_without_matching_rule_and_node_pass_is_unverified(self):
        variants = ([], [finding("pass", "pass", selector="#another")],
                    [finding("pass", "pass", rule_id="axe-color-contrast")],
                    [finding("pass", "pass", engine="static")],
                    [finding("pass", "pass", evidence_type="heuristic")])
        for findings in variants:
            with self.subTest(findings=findings):
                after = report(findings)
                result = compare_reports(self.before, after)
                self.assertEqual(len(result["changed_unverified"]), 1)
                self.assertEqual(result["fixed_verified"], [])

    def test_pass_for_different_url_does_not_prove_fix(self):
        self.after["findings"][0]["location"]["url"] = URL + "/other"
        self.assertEqual(len(compare_reports(self.before, self.after)["changed_unverified"]), 1)

    def test_all_prior_locations_must_still_exist_and_pass(self):
        self.before["findings"][0]["locations"] = [{"url": URL, "selector": "#second"}]
        self.after["metadata"]["run"]["observed_selectors"]["#second"] = True
        self.assertEqual(len(compare_reports(self.before, self.after)["changed_unverified"]), 1)
        self.after["findings"][0]["locations"] = [{"url": URL, "selector": "#second"}]
        self.assertEqual(len(compare_reports(self.before, self.after)["fixed_verified"]), 1)

    def test_source_disappearance_is_unverified_even_with_pass(self):
        self.before["findings"][0]["engine"] = "static"
        self.after["metadata"]["run"]["status"] = "partial"
        result = compare_reports(self.before, self.after)
        self.assertEqual(len(result["changed_unverified"]), 1)
        self.assertEqual(result["fixed_verified"], [])

    def test_heuristic_warning_is_not_promoted_to_verified_fix(self):
        self.before["findings"][0]["status"] = "warning"
        self.before["findings"][0]["evidence_type"] = "heuristic"
        self.assertEqual(len(compare_reports(self.before, self.after)["changed_unverified"]), 1)

    def test_remaining_and_new_use_stable_identity(self):
        self.after["findings"] = [finding("changed-display-id", stable_id="old"), finding("new")]
        result = compare_reports(self.before, self.after)
        self.assertEqual([x["identity"] for x in result["remaining"]], ["old"])
        self.assertEqual([x["identity"] for x in result["new"]], ["new"])

    def test_legacy_id_fallback_is_supported_without_proving_absence_fixed(self):
        old = {"id": "legacy", "status": "fail"}
        before = {"findings": [old]}
        self.assertEqual(len(compare_reports(before, {"findings": [old]})["remaining"]), 1)
        self.assertEqual(len(compare_reports(before, {"findings": []})["changed_unverified"]), 1)

    def test_human_and_untested_statuses_are_never_fixed(self):
        self.before["findings"].append(finding("manual", "human-review-required"))
        self.after["findings"] = [finding(status="not-tested"), finding("new-manual", "human-review-required")]
        result = compare_reports(self.before, self.after)
        self.assertEqual(len(result["not_tested"]), 3)
        self.assertEqual(result["fixed_verified"], [])

    def test_prior_pass_becoming_failure_is_new(self):
        self.before["findings"][0]["status"] = "pass"
        self.after["findings"] = [finding()]
        self.assertEqual(len(compare_reports(self.before, self.after)["new"]), 1)

    def test_malformed_baselines_raise_clear_value_errors(self):
        bad = (None, [], {}, {"findings": {}}, {"findings": [None]},
               {"findings": [{"id": "x", "status": "compliant"}]},
               {"findings": [{"id": [], "status": "fail"}]},
               {"findings": [finding(), finding()]},
               {"findings": [finding(location=[]) ]},
               {"findings": [], "metadata": {"run": []}},
               {"findings": [], "metadata": {"run": {"engines": []}}})
        for value in bad:
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    compare_reports(value, self.after)

    def test_rendered_skip_target_requires_same_rule_engine_and_positive_receipt(self):
        for item in (self.before["findings"][0], self.after["findings"][0]):
            item.update(engine="rendered-dom", rule_id="skip-link-target-missing")
        for data in (self.before, self.after):
            data["metadata"]["run"]["engines"]["rendered-dom"] = "1.0.0"
        self.assertEqual(len(compare_reports(self.before, self.after)["fixed_verified"]), 1)
        self.before["metadata"]["run"]["engines"].pop("rendered-dom")
        self.assertFalse(compare_reports(self.before, self.after)["fixed_verified"])
        self.before["metadata"]["run"]["engines"]["rendered-dom"] = "1.0.0"
        self.after["findings"][0]["engine"] = "axe"
        self.assertFalse(compare_reports(self.before, self.after)["fixed_verified"])

    def test_reduced_rule_coverage_prevents_verified_fix(self):
        self.before["metadata"]["run"]["rules"] = ["button-name", "label-content-name-mismatch"]
        self.after["metadata"]["run"]["rules"] = ["button-name"]
        self.assertFalse(compare_reports(self.before, self.after)["fixed_verified"])
        self.after["metadata"]["run"]["rules"].append("label-content-name-mismatch")
        self.assertEqual(len(compare_reports(self.before, self.after)["fixed_verified"]), 1)

    def test_does_not_mutate_inputs(self):
        before_copy, after_copy = copy.deepcopy(self.before), copy.deepcopy(self.after)
        result = compare_reports(self.before, self.after)
        result["fixed_verified"][0]["before"]["status"] = "pass"
        self.assertEqual(self.before, before_copy)
        self.assertEqual(self.after, after_copy)


if __name__ == "__main__":
    unittest.main()
