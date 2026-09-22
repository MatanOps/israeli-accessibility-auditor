#!/usr/bin/env python3
"""Unified finding model and Markdown/JSON report assembly.

The finding concept, category grouping and remediation phrasing were adapted
from ``a11y_scanner.py`` in alirezarezvani/claude-skills
(MIT License, Copyright (c) 2025 Alireza Rezvani). The upstream text and JSON
formatters, severity-only scoring and "all checks passed" summary were removed
and replaced with the status/evidence model required by this project. See
THIRD_PARTY_NOTICES.md for the full notice.

This module uses only the Python standard library so ``audit.py`` can always
write a report, even when third-party dependencies are missing.
"""

from __future__ import annotations

import html
import json
import os
import re
from collections import OrderedDict

VERSION = "0.1.0"

# The 14 report categories, in display order. Scanners import these names.
IMAGES = "Images and media"
STRUCTURE = "Semantic structure and landmarks"
HEADINGS = "Headings"
KEYBOARD = "Keyboard and focus"
FORMS = "Forms, labels and validation"
LINKS = "Links and buttons"
ARIA = "ARIA, names, roles and states"
TABLES = "Tables"
CONTRAST = "Contrast and visual presentation"
ZOOM = "Zoom, reflow and reduced motion"
HEBREW = "Hebrew, RTL and bidirectional content"
LANGUAGE = "Language metadata"
STATEMENT = "Accessibility statement indicators"
HUMAN = "Human verification required"

CATEGORIES = (IMAGES, STRUCTURE, HEADINGS, KEYBOARD, FORMS, LINKS, ARIA, TABLES,
              CONTRAST, ZOOM, HEBREW, LANGUAGE, STATEMENT, HUMAN)

SEVERITIES = ("critical", "serious", "moderate", "minor", "info")
STATUSES = ("fail", "pass", "warning", "not-tested", "human-review-required")
EVIDENCE_TYPES = ("automatically-verified", "heuristic", "human-verification-required")
ACTIONABLE = ("fail", "warning")

_SEVERITY_ORDER = {name: index for index, name in enumerate(SEVERITIES)}
_STATUS_ORDER = {name: index for index, name in enumerate(("fail", "warning", "not-tested",
                                                            "human-review-required", "pass"))}


def finding(rule_id, category, severity, status, evidence_type, wcag_criterion, location,
            evidence, explanation, remediation):
    """Build one JSON-serializable finding and validate its controlled fields."""
    if category not in CATEGORIES:
        raise ValueError("Unknown category: {!r}".format(category))
    if severity not in SEVERITIES:
        raise ValueError("Unknown severity: {!r}".format(severity))
    if status not in STATUSES:
        raise ValueError("Unknown status: {!r}".format(status))
    if evidence_type not in EVIDENCE_TYPES:
        raise ValueError("Unknown evidence type: {!r}".format(evidence_type))
    if not isinstance(location, dict) or not ({"file", "line"} <= set(location)
                                              or {"url", "selector"} <= set(location)):
        raise ValueError("Location must carry file+line or url+selector: {!r}".format(location))
    return OrderedDict([
        ("id", str(rule_id)),
        ("category", category),
        ("severity", severity),
        ("status", status),
        ("evidence_type", evidence_type),
        ("wcag_criterion", wcag_criterion if wcag_criterion else None),
        ("location", dict(location)),
        ("evidence", str(evidence) if str(evidence).strip() else "No snippet available."),
        ("explanation", str(explanation)),
        ("remediation", str(remediation)),
    ])


# ---------------------------------------------------------------------------
# Human verification checklist (appended once per report)
# ---------------------------------------------------------------------------

_HUMAN_CHECKS = (
    ("human-keyboard-operation", "2.1.1 Keyboard; 2.1.2 No Keyboard Trap; 2.4.3 Focus Order; 2.4.7 Focus Visible",
     "Keyboard operation, focus order, visible focus and keyboard traps",
     "Static scanning cannot operate the page. Tab through every interactive control, including dialogs, "
     "menus, carousels and SPA route changes, and confirm focus is visible, ordered logically and never trapped.",
     "Fix any control that cannot be reached or operated with the keyboard, and manage focus on dynamic changes."),
    ("human-screen-reader", "4.1.2 Name, Role, Value; 3.3.1 Error Identification; 4.1.3 Status Messages",
     "Screen-reader names, roles, states and Hebrew error messages",
     "Accessible names, expanded/selected/checked states, live announcements and Hebrew validation messages "
     "must be confirmed with a screen reader (NVDA, JAWS, VoiceOver) on the rendered page.",
     "Correct names, states and announcements that a screen reader does not convey in Hebrew or English."),
    ("human-alt-quality", "1.1.1 Non-text Content; 1.2.2 Captions (Prerecorded)",
     "Usefulness of alternative text, decorative markings and captions",
     "A present alt attribute proves nothing about its meaning. Review whether each text alternative conveys "
     "the purpose of the image and whether empty alt is limited to decorative images.",
     "Rewrite alternatives that are generic, redundant or missing meaning; caption or transcribe media."),
    ("human-rendered-contrast", "1.4.3 Contrast (Minimum); 1.4.11 Non-text Contrast",
     "Rendered text and component contrast in all relevant states",
     "Measure actual foreground/background combinations in the browser, including focus, hover, validation, "
     "transparency, images and gradients. Confirm text size and weight; literal CSS pairs do not establish "
     "rendered contrast.",
     "Adjust the effective colours and verify against the applicable text or component threshold."),
    ("human-zoom-reflow-motion", "1.4.4 Resize Text; 1.4.10 Reflow; 2.3.3 Animation from Interactions (AAA, advisory)",
     "200% zoom, 320 CSS px reflow and reduced motion",
     "Zoom the browser to 200%, then view at 320 CSS px width (400% zoom on a 1280 px viewport) and enable "
     "the reduced-motion preference; verify no loss of content or avoidable two-dimensional scrolling. "
     "Apply the reflow exceptions for content requiring a two-dimensional layout. Reduced-motion review "
     "under 2.3.3 is an additional AAA recommendation, not an automatic AA failure.",
     "Use responsive layouts, relative units and prefers-reduced-motion media queries."),
    ("human-bidi-readability", "1.3.2 Meaningful Sequence; 3.1.2 Language of Parts",
     "Hebrew reading order and mixed Hebrew/English (bidi) readability",
     "Check punctuation placement, numbers, phone numbers, email addresses, URLs and Latin brand names inside "
     "Hebrew sentences, plus Hebrew inside English pages, on the rendered page.",
     "Set the base direction correctly and wrap opposite-direction fragments with dir/lang or bdi."),
    ("human-statement-review", None,
     "Accessibility statement content and legal review",
     "Any statement candidate found by the scanner is only an indicator. Its content, accuracy and the "
     "applicable Israeli requirements (IS 5568 and official sources) require professional review.",
     "Have a qualified accessibility professional review the statement and the legal obligations; the tool "
     "does not determine applicability or compliance."),
)


def human_checks(location):
    """Return the standard human verification checklist for one report."""
    results = []
    for rule_id, wcag, evidence, explanation, remediation in _HUMAN_CHECKS:
        results.append(finding(rule_id, HUMAN, "info", "human-review-required",
                               "human-verification-required", wcag, location, evidence,
                               explanation, remediation))
    return results


# ---------------------------------------------------------------------------
# Report assembly
# ---------------------------------------------------------------------------

def _report_location(target, mode):
    if mode == "url":
        return {"url": target, "selector": None}
    return {"file": target, "line": None}


def _limitations(mode, files_scanned):
    items = [
        "Static analysis only: JavaScript was not executed and nothing was rendered. Runtime-injected content, "
        "single-page-application routes, dialogs, menus, validation messages and authenticated states were not tested.",
        "A pass applies only to the narrow static fact named in that finding. Absence of automated findings is not "
        "evidence of WCAG conformance, IS 5568 conformance or legal compliance.",
        "Colour contrast is computed only for explicit opaque colour pairs declared in the same rule or style "
        "attribute. Cascade, inheritance, background images, gradients, transparency and rendered font sizes are "
        "not resolved, so unknown backgrounds are reported as not tested.",
        "Severity describes potential user impact and is independent of evidence type; heuristic findings need "
        "confirmation on the rendered page.",
    ]
    if mode == "url":
        items.append("Only the single HTTP response returned for the supplied URL (after redirects) was examined. "
                     "No crawling, form submission or additional requests were performed.")
    else:
        items.append("JSX, TSX, Vue and Svelte templates are matched with regular expressions. Dynamic attributes, "
                     "spread props, slots, parent-provided labels and custom components are not resolved; those "
                     "findings are heuristic and standalone components are not held to document-level rules.")
    if files_scanned == 0:
        items.append("No file or page was scanned successfully, so no automated result exists for this target.")
    return items


def _unique_ids(findings):
    counts = {}
    for item in findings:
        counts[item["id"]] = counts.get(item["id"], 0) + 1
    seen = {}
    for item in findings:
        base = item["id"]
        if counts[base] > 1:
            seen[base] = seen.get(base, 0) + 1
            item["id"] = "{}-{}".format(base, seen[base])
    return findings


def build_report(target, mode, findings, files_scanned, errors, metadata):
    """Assemble the final report dictionary from scanner findings."""
    location = _report_location(target, mode)
    findings = [dict(item) for item in findings]
    errors = list(errors or [])
    for index, error in enumerate(errors, 1):
        findings.append(finding(
            "coverage-operational-error", HUMAN, "info", "not-tested", "human-verification-required", None,
            location, str(error),
            "An operational error interrupted the audit, so the affected scope has no automated result. "
            "This is incomplete coverage, never a clean result.",
            "Resolve the error (path, network, dependency or file encoding) and rerun the audit."))
    findings.extend(human_checks(location))
    findings.sort(key=lambda item: (CATEGORIES.index(item["category"]),
                                    _STATUS_ORDER.get(item["status"], 9),
                                    _SEVERITY_ORDER.get(item["severity"], 9)))
    _unique_ids(findings)

    actionable = [item for item in findings if item["status"] in ACTIONABLE]
    severity_counts = OrderedDict((name, 0) for name in SEVERITIES)
    for item in actionable:
        severity_counts[item["severity"]] += 1
    status_counts = OrderedDict((name, 0) for name in STATUSES)
    for item in findings:
        status_counts[item["status"]] += 1
    category_counts = OrderedDict((name, 0) for name in CATEGORIES)
    for item in actionable:
        category_counts[item["category"]] += 1

    summary = OrderedDict([
        ("files_scanned", files_scanned),
        ("actionable_findings", len(actionable)),
        ("actionable_by_severity", severity_counts),
        ("actionable_by_category", category_counts),
        ("by_status", status_counts),
        ("passed_checks", status_counts["pass"]),
        ("not_tested", status_counts["not-tested"]),
        ("human_review_required", status_counts["human-review-required"]),
        ("operational_errors", len(errors)),
        ("overall_conformance", "not determined"),
    ])
    scope = OrderedDict([
        ("target", target),
        ("mode", mode),
        ("files_scanned", files_scanned),
        ("rendered", False),
        ("javascript_executed", False),
    ])
    return OrderedDict([
        ("version", VERSION),
        ("tool", "israeli-accessibility-auditor"),
        ("scope", scope),
        ("limitations", _limitations(mode, files_scanned)),
        ("errors", errors),
        ("summary", summary),
        ("metadata", dict(metadata or {})),
        ("findings", findings),
        ("disclaimer", DISCLAIMER),
    ])


DISCLAIMER = (
    "This report is technical assistance produced by static analysis. It is not an accessibility certificate, "
    "a legal opinion, proof of compliance with Israeli law or IS 5568, or a substitute for testing with assistive "
    "technology, real users and a qualified accessibility professional. The Israeli legal baseline (IS 5568 and "
    "official sources) is distinct from the recommended engineering target, WCAG 2.2 AA."
)


# ---------------------------------------------------------------------------
# Markdown rendering (all untrusted text is escaped)
# ---------------------------------------------------------------------------

def _text(value):
    """Escape untrusted prose so site markup never becomes report markup."""
    text = " ".join(str(value if value is not None else "").split())
    return re.sub(r"([\\`*_{}\[\]()#!|])", r"\\\1", html.escape(text, quote=False))


def _code(value):
    """Inline code with backticks neutralised."""
    return "`" + str(value if value is not None else "").replace("`", "'").replace("\r", " ").replace("\n", " ") + "`"


def _block(value):
    """Fenced block whose fence is longer than any backtick run in the content."""
    text = str(value if value is not None else "")
    longest = max((len(run) for run in re.findall(r"`+", text)), default=0)
    fence = "`" * max(3, longest + 1)
    return "{}text\n{}\n{}".format(fence, html.escape(text, quote=False), fence)


def _location_line(location):
    if "url" in location:
        parts = ["URL " + _code(location.get("url"))]
        if location.get("selector"):
            parts.append("selector " + _code(location["selector"]))
        if location.get("line"):
            parts.append("line {}".format(int(location["line"])))
        return ", ".join(parts)
    parts = ["File " + _code(location.get("file"))]
    if location.get("line"):
        parts.append("line {}".format(int(location["line"])))
    if location.get("selector"):
        parts.append("selector " + _code(location["selector"]))
    return ", ".join(parts)


def render_markdown(report):
    summary = report["summary"]
    scope = report["scope"]
    lines = ["# Accessibility audit report", ""]
    lines.append("- Target: {} (mode: {})".format(_code(scope["target"]), _text(scope["mode"])))
    lines.append("- Tool: israeli-accessibility-auditor {}".format(_text(report["version"])))
    lines.append("- Files or pages scanned: {}".format(int(scope["files_scanned"])))
    lines.append("- JavaScript executed / page rendered: no")
    lines.append("- Overall conformance: not determined (this tool never certifies a site)")
    lines.append("")
    lines.append("## Scope and limitations")
    lines.append("")
    for item in report["limitations"]:
        lines.append("- " + _text(item))
    lines.append("")
    lines.append("## Operational errors")
    lines.append("")
    if report["errors"]:
        for item in report["errors"]:
            lines.append("- " + _text(item))
    else:
        lines.append("None recorded.")
    lines.append("")
    lines.append("## Findings by category")
    lines.append("")
    by_category = OrderedDict((name, []) for name in CATEGORIES)
    for item in report["findings"]:
        by_category[item["category"]].append(item)
    for number, name in enumerate(CATEGORIES, 1):
        lines.append("### {}. {}".format(number, name))
        lines.append("")
        items = by_category[name]
        if not items:
            lines.append("No automated findings recorded in this category. This is not a pass; see the "
                         "limitations and the human verification checklist.")
            lines.append("")
            continue
        for item in items:
            lines.append("#### {} — {}".format(_code(item["id"]), _text(item["status"])))
            lines.append("")
            lines.append("- Severity: {}; evidence type: {}; WCAG: {}".format(
                _text(item["severity"]), _text(item["evidence_type"]),
                _text(item["wcag_criterion"]) if item["wcag_criterion"] else "none (indicator or best practice)"))
            lines.append("- Location: " + _location_line(item["location"]))
            lines.append("- Explanation: " + _text(item["explanation"]))
            lines.append("- Remediation: " + _text(item["remediation"]))
            lines.append("- Evidence:")
            lines.append("")
            lines.append(_block(item["evidence"]))
            lines.append("")
    lines.append("## Summary")
    lines.append("")
    lines.append("Actionable findings (status fail or warning) by severity:")
    lines.append("")
    lines.append("| Severity | Count |")
    lines.append("| --- | --- |")
    for name, count in summary["actionable_by_severity"].items():
        lines.append("| {} | {} |".format(name, count))
    lines.append("")
    lines.append("- Passed checks (narrow static facts only): {}".format(summary["passed_checks"]))
    lines.append("- Checks that could not be performed (not-tested): {}".format(summary["not_tested"]))
    lines.append("- Items requiring human verification: {}".format(summary["human_review_required"]))
    lines.append("- Operational errors: {}".format(summary["operational_errors"]))
    lines.append("")
    lines.append("## Required manual checks")
    lines.append("")
    manual = [item for item in report["findings"] if item["status"] == "human-review-required"]
    for item in manual:
        lines.append("- {}: {}".format(_code(item["id"]), _text(item["evidence"])))
    if not manual:
        lines.append("- None listed.")
    lines.append("")
    lines.append("## Disclaimer")
    lines.append("")
    lines.append(_text(report["disclaimer"]))
    lines.append("")
    return "\n".join(lines)


def write_reports(report, output):
    """Write accessibility-report.md and accessibility-report.json into ``output``."""
    os.makedirs(output, exist_ok=True)
    json_path = os.path.join(output, "accessibility-report.json")
    md_path = os.path.join(output, "accessibility-report.md")
    with open(json_path, "w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    with open(md_path, "w", encoding="utf-8") as handle:
        handle.write(render_markdown(report))
