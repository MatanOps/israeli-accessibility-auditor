"""Conservative report comparison: absence alone never proves remediation."""

import copy
import json


STATUSES = {"fail", "warning", "pass", "not-tested", "human-review-required"}
ACTIONABLE = {"fail", "warning"}
GROUPS = ("fixed_verified", "changed_unverified", "remaining", "new", "not_tested")


def _identity(finding):
    return finding.get("stable_id") or finding.get("id")


def _validate(report, label):
    """Narrow imported JSON before using it; incomplete coverage is not a pass."""
    if not isinstance(report, dict) or not isinstance(report.get("findings"), list):
        raise ValueError("{} must be a report object with a findings list".format(label))
    for key in ("scope", "metadata"):
        if key in report and not isinstance(report[key], dict):
            raise ValueError("{}.{} must be an object".format(label, key))
    if "errors" in report and not isinstance(report["errors"], list):
        raise ValueError("{}.errors must be a list".format(label))
    run = report.get("metadata", {}).get("run", {})
    if not isinstance(run, dict):
        raise ValueError("{}.metadata.run must be an object".format(label))
    if "engines" in run and not isinstance(run["engines"], dict):
        raise ValueError("{}.metadata.run.engines must be an object".format(label))
    seen = set()
    for item in report["findings"]:
        if not isinstance(item, dict):
            raise ValueError("{} contains a non-object finding".format(label))
        for key in ("stable_id", "id", "rule_id", "engine"):
            if key in item and item[key] is not None and not isinstance(item[key], str):
                raise ValueError("{}.finding.{} must be a string".format(label, key))
        identity = _identity(item)
        if not isinstance(identity, str) or not identity.strip():
            raise ValueError("{} contains a finding without an identity".format(label))
        if identity in seen:
            raise ValueError("{} contains a duplicate finding identity: {}".format(label, identity))
        seen.add(identity)
        if not isinstance(item.get("status"), str) or item["status"] not in STATUSES:
            raise ValueError("{} contains a finding with an unknown status".format(label))
        locations = item.get("locations", [])
        if not isinstance(locations, list):
            raise ValueError("{}.finding.locations must be a list".format(label))
        locations = locations + ([item["location"]] if "location" in item else [])
        for location in locations:
            if not isinstance(location, dict):
                raise ValueError("{}.finding location must be an object".format(label))
            for key in ("url", "selector", "file"):
                if key in location and location[key] is not None and not isinstance(location[key], str):
                    raise ValueError("{}.finding location {} must be a string".format(label, key))
    return report


def _run(report):
    return report.get("metadata", {}).get("run", {})


def _coverage_set(values):
    if not isinstance(values, list) or not values:
        return None
    try:
        if any(not isinstance(value, (str, dict)) or not value for value in values):
            return None
        return {json.dumps(value, sort_keys=True, ensure_ascii=False) for value in values}
    except (TypeError, ValueError):
        return None


def _locations(finding):
    locations = list(finding.get("locations", []))
    if isinstance(finding.get("location"), dict):
        locations.append(finding["location"])
    return {(loc.get("url"), loc.get("selector")) for loc in locations
            if isinstance(loc.get("url"), str) and loc["url"].strip()
            and isinstance(loc.get("selector"), str) and loc["selector"].strip()}


def _coverage_gap(before, after):
    """Return why equivalent rendered execution cannot be established, or None."""
    old, current = _run(before), _run(after)
    if after.get("errors"):
        return "The current report contains operational errors."
    if current.get("status") != "completed" or current.get("rendered") is not True:
        return "The current rendered audit did not complete."
    if "site" in current and (not isinstance(current["site"], dict)
                              or current["site"].get("complete") is not True):
        return "The current website coverage did not complete."
    old_target = before.get("scope", {}).get("target")
    new_target = after.get("scope", {}).get("target")
    if not isinstance(old_target, str) or not old_target or old_target != new_target:
        return "The target is missing or differs from the baseline."
    for key in ("original_url", "final_url"):
        if not isinstance(old.get(key), str) or not old[key] or old[key] != current.get(key):
            return "The {} is missing or differs from the baseline.".format(key)
    for key in ("pages", "states"):
        old_values, new_values = _coverage_set(old.get(key)), _coverage_set(current.get(key))
        if old_values is None or new_values is None or not old_values <= new_values:
            return "The {} coverage is missing or reduced.".format(key)
    if "rules" in old:
        old_rules, new_rules = _coverage_set(old["rules"]), _coverage_set(current.get("rules"))
        if old_rules is None or new_rules is None or not old_rules <= new_rules:
            return "The rule coverage is missing or reduced."
    old_engines, new_engines = old.get("engines"), current.get("engines")
    if not isinstance(old_engines, dict) or not isinstance(new_engines, dict) or not old_engines.get("axe"):
        return "The engine coverage cannot be established."
    for engine, version in old_engines.items():
        if not isinstance(version, str) or not version or new_engines.get(engine) != version:
            return "The {} engine or version differs from the baseline.".format(engine)
    return None


def _verified_engine(item):
    return item.get("engine") == "axe" or (
        item.get("engine") == "rendered-dom" and item.get("rule_id") == "skip-link-target-missing")


def _entry(identity, before, after, reason):
    return {"identity": identity, "before": copy.deepcopy(before),
            "after": copy.deepcopy(after), "reason": reason}


def compare_reports(before, after):
    """Return fixed_verified, changed_unverified, remaining, new and not_tested.

    A verified fix requires a prior verified rendered failure, equivalent completed
    rendered coverage, the old selector still present, and a verified rendered pass
    for that exact rule AND URL/selector. Static disappearance cannot prove a
    fix. Pass-only records are supporting evidence, never new issues.
    Invalid report structures raise ValueError instead of silently proving fixes.
    """
    before, after = _validate(before, "before"), _validate(after, "after")
    result = {group: [] for group in GROUPS}
    previous = {_identity(item): item for item in before["findings"]}
    current = {_identity(item): item for item in after["findings"]}
    pass_locations = {}
    for item in after["findings"]:
        if (item["status"] == "pass" and _verified_engine(item)
                and item.get("evidence_type") == "automatically-verified" and item.get("rule_id")):
            pass_locations.setdefault((item["engine"], item["rule_id"]), set()).update(_locations(item))

    for identity, old in previous.items():
        if old["status"] == "pass":
            continue
        new = current.get(identity)
        if old["status"] not in ACTIONABLE:
            if new is None or new["status"] not in ACTIONABLE:
                result["not_tested"].append(_entry(identity, old, new, "Human or untested evidence is not a verified fix."))
            continue
        if new is not None and new["status"] in ACTIONABLE:
            result["remaining"].append(_entry(identity, old, new, "The finding is still reported."))
            continue
        if new is not None and new["status"] in ("not-tested", "human-review-required"):
            result["not_tested"].append(_entry(identity, old, new, "The current finding still requires verification."))
            continue
        run = _run(after)
        old_target, new_target = before.get("scope", {}).get("target"), after.get("scope", {}).get("target")
        if after.get("errors") or run.get("status") == "not-performed" or (old_target and new_target and old_target != new_target):
            result["not_tested"].append(_entry(identity, old, new, "Execution failed, was not performed, or targeted a different scope."))
            continue
        if not _verified_engine(old):
            result["changed_unverified"].append(_entry(identity, old, new, "Static finding absence does not prove remediation."))
            continue
        if old.get("engine") == "rendered-dom":
            old_version = _run(before).get("engines", {}).get("rendered-dom")
            if not isinstance(old_version, str) or not old_version:
                result["not_tested"].append(_entry(identity, old, new, "The rendered DOM check version is missing."))
                continue
        gap = _coverage_gap(before, after)
        if gap:
            result["not_tested"].append(_entry(identity, old, new, gap))
            continue
        locations = _locations(old)
        observed = run.get("observed_selectors")
        if isinstance(run.get("site"), dict):
            by_page = run.get("observed_selectors_by_page")
            still_present = (bool(locations) and isinstance(by_page, dict) and all(
                isinstance(by_page.get(url), dict) and by_page[url].get(selector) is True
                for url, selector in locations))
        else:
            still_present = bool(locations) and isinstance(observed, dict) and all(
                observed.get(selector) is True for _, selector in locations)
        if not still_present:
            result["not_tested"].append(_entry(identity, old, new, "The old selector was removed or its continued presence was not observed."))
            continue
        if old["status"] != "fail" or old.get("evidence_type") != "automatically-verified":
            result["changed_unverified"].append(_entry(identity, old, new, "The prior finding was heuristic, not a verified failure."))
            continue
        if locations <= pass_locations.get((old.get("engine"), old.get("rule_id")), set()):
            result["fixed_verified"].append(_entry(identity, old, new, "The same rendered rule passed for the still-present selector under equivalent completed coverage."))
        else:
            result["changed_unverified"].append(_entry(identity, old, new, "No verified pass covers the old rule and exact URL/selector."))

    for identity, item in current.items():
        old = previous.get(identity)
        if item["status"] in ACTIONABLE and (old is None or old["status"] not in ACTIONABLE):
            result["new"].append(_entry(identity, old, item, "This actionable finding was not reported in the baseline."))
        elif item["status"] in ("not-tested", "human-review-required") and old is None:
            result["not_tested"].append(_entry(identity, None, item, "The current audit identifies coverage requiring verification."))
    return result
