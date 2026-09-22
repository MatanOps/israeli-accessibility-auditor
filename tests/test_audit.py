#!/usr/bin/env python3
"""Black-box regression tests: python -m unittest discover -s tests.

Optional: python tests/test_audit.py /path/to/repo.

Tests use local files and a temporary loopback HTTP server only. No public
websites, accounts, upstream repositories, or third-party test libraries.
"""
from __future__ import annotations

import argparse
import functools
import http.server
import json
import os
from pathlib import Path
import re
import runpy
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import unittest


REPO = Path(__file__).resolve().parents[1]
unittest_args = []
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("repo", type=Path, nargs="?", default=REPO)
    arguments, unittest_args = parser.parse_known_args()
    REPO = arguments.repo.resolve()
CLI = REPO / "scripts" / "audit.py"
FIXTURES = REPO / "tests" / "fixtures"

REQUIRED = {
    "id", "category", "severity", "status", "evidence_type",
    "wcag_criterion", "location", "evidence", "explanation", "remediation",
}
STATUSES = {"fail", "pass", "warning", "not-tested", "human-review-required"}
SEVERITIES = {"critical", "serious", "moderate", "minor", "info"}
EVIDENCE_TYPES = {"automatically-verified", "heuristic", "human-verification-required"}
CATEGORIES = [
    "Images and media", "Semantic structure and landmarks", "Headings",
    "Keyboard and focus", "Forms, labels and validation", "Links and buttons",
    "ARIA, names, roles and states", "Tables", "Contrast and visual presentation",
    "Zoom, reflow and reduced motion", "Hebrew, RTL and bidirectional content",
    "Language metadata", "Accessibility statement indicators", "Human verification required",
]


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *_args):
        pass


class AcceptanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not CLI.is_file():
            raise RuntimeError(f"Missing entry point: {CLI}")
        cls.temp = tempfile.TemporaryDirectory(prefix="a11y acceptance ")
        cls.root = Path(cls.temp.name)
        web = cls.root / "local website"
        web.mkdir()
        shutil.copy(FIXTURES / "accessible.html", web / "accessible.html")
        (web / "spa.html").write_text(
            '<!doctype html><html lang="he" dir="rtl"><head><title>SPA fixture</title></head>'
            '<body><div id="root"></div><script src="app.js"></script></body></html>',
            encoding="utf-8",
        )
        (web / "identical-images.html").write_text(
            cls.document('<section><img src="same.svg"><img src="same.svg"></section>'),
            encoding="utf-8",
        )
        handler = functools.partial(QuietHandler, directory=str(web))
        cls.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base_url = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=3)
        cls.temp.cleanup()

    def audit(self, *args, output_name=None, python_flags=(), expected=None):
        output = self.root / (output_name or self.id().split(".")[-1])
        result = subprocess.run(
            [sys.executable, *python_flags, str(CLI), *map(str, args), "--output", str(output)],
            cwd=str(REPO), text=True, capture_output=True, timeout=45,
            env={**os.environ, "PYTHONUTF8": "1"},
        )
        message = f"exit={result.returncode}\nstdout={result.stdout}\nstderr={result.stderr}"
        if expected is not None:
            self.assertEqual(result.returncode, expected, message)
        self.assertTrue((output / "accessibility-report.md").is_file(), message)
        self.assertTrue((output / "accessibility-report.json").is_file(), message)
        data = json.loads((output / "accessibility-report.json").read_text(encoding="utf-8"))
        markdown = (output / "accessibility-report.md").read_text(encoding="utf-8")
        self.assertIsInstance(data, dict)
        self.assertIsInstance(data.get("findings"), list)
        for finding in data["findings"]:
            self.assertTrue(REQUIRED <= finding.keys(), f"Missing fields: {REQUIRED - finding.keys()}")
            self.assertIn(finding["status"], STATUSES)
            self.assertIn(finding["severity"], SEVERITIES)
            self.assertIn(finding["evidence_type"], EVIDENCE_TYPES)
            self.assertIn(finding["category"].rstrip("."), CATEGORIES)
            self.assertIsInstance(finding["location"], dict)
            self.assertTrue({"file", "line"} <= finding["location"].keys() or
                            {"url", "selector"} <= finding["location"].keys(), finding)
            for key in ("id", "evidence", "explanation", "remediation"):
                self.assertIsInstance(finding[key], str)
                self.assertTrue(finding[key].strip(), f"Empty {key}: {finding}")
        self.assertGreater(len(markdown), 100)
        self.assertIn("disclaimer", markdown.lower())
        if result.returncode != 2:
            actionable = any(f["status"] in {"fail", "warning"} for f in data["findings"])
            self.assertEqual(result.returncode, int(actionable), message)
        return data["findings"], markdown, result

    def project(self, name, source=None, filename="index.html"):
        project = self.root / name
        project.mkdir(exist_ok=True)
        if source is not None:
            (project / filename).write_text(source, encoding="utf-8")
        return project

    def fixture_project(self, name):
        project = self.project(name)
        shutil.copy(FIXTURES / f"{name}.html", project / "index.html")
        return project

    @staticmethod
    def document(body):
        return ('<!doctype html><html lang="en" dir="ltr"><head><meta charset="utf-8">'
                '<title>Acceptance fixture</title></head><body><main><h1>Fixture</h1>'
                + body + '</main></body></html>')

    @staticmethod
    def actionable(findings, category):
        return [f for f in findings if f["status"] in {"fail", "warning"}
                and f["category"].rstrip(".") == category]

    def test_accessible_fixture_has_no_failures(self):
        findings, _, _ = self.audit("--path", self.fixture_project("accessible"))
        failures = [f for f in findings if f["status"] == "fail"]
        self.assertEqual(failures, [], json.dumps(failures, ensure_ascii=False, indent=2))
        warnings = [f for f in findings if f["status"] == "warning"]
        self.assertLessEqual(len(warnings), 3, json.dumps(warnings, ensure_ascii=False, indent=2))
        # Manual and untested checks must survive a clean automated scan.
        self.assertTrue(any(f["status"] in {"not-tested", "human-review-required"} for f in findings))

    def test_inaccessible_fixture_covers_requested_classes(self):
        findings, markdown, _ = self.audit("--path", self.fixture_project("inaccessible-hebrew"), expected=1)
        actionable = [f for f in findings if f["status"] in {"fail", "warning"}]
        categories = {f["category"].rstrip(".") for f in actionable}
        for category in (
            "Images and media", "Forms, labels and validation", "Headings",
            "Links and buttons", "Language metadata", "Hebrew, RTL and bidirectional content",
            "ARIA, names, roles and states", "Tables", "Contrast and visual presentation",
        ):
            self.assertIn(category, categories, json.dumps(actionable, ensure_ascii=False, indent=2))
        nonsemantic = [f for f in actionable if "onclick" in str(f["evidence"]).lower()]
        self.assertTrue(nonsemantic, "Missing evidence for the nonsemantic click handler")
        for category in CATEGORIES:
            self.assertIn(category.lower(), markdown.lower())

    def test_paths_with_spaces(self):
        source = (FIXTURES / "inaccessible-hebrew.html").read_text(encoding="utf-8")
        project = self.project("project with spaces", source, "page with spaces.html")
        findings, _, _ = self.audit("--path", project, output_name="report with spaces", expected=1)
        self.assertTrue(any("page with spaces.html" in str(f["location"]) for f in findings))

    def test_empty_project_reports_not_tested(self):
        project = self.project("empty project")
        (project / "readme.txt").write_text("No frontend files.", encoding="utf-8")
        findings, _, result = self.audit("--path", project)
        self.assertIn(result.returncode, {0, 1, 2})
        self.assertTrue(any(f["status"] == "not-tested" for f in findings))
        self.assertFalse(any(f["status"] == "pass" for f in findings))

    def test_unavailable_url_reports_operational_error(self):
        with socket.socket() as unused_socket:
            unused_socket.bind(("127.0.0.1", 0))
            port = unused_socket.getsockname()[1]
        self.audit("--url", f"http://127.0.0.1:{port}/unavailable", expected=2)

    def test_nonexistent_path_reports_operational_error(self):
        self.audit("--path", self.root / "does not exist", expected=2)

    def test_spa_is_not_tested(self):
        findings, _, _ = self.audit("--url", self.base_url + "/spa.html")
        self.assertTrue(any(f["status"] == "not-tested" and
                            any(word in (f["explanation"] + f["evidence"]).lower()
                                for word in ("render", "spa", "javascript", "dynamic"))
                            for f in findings), findings)

    def test_url_returns_selector_locations(self):
        findings, _, _ = self.audit("--url", self.base_url + "/accessible.html")
        self.assertTrue(any("url" in f["location"] for f in findings))

    def test_identical_url_images_keep_distinct_occurrence_selectors(self):
        findings, _, _ = self.audit("--url", self.base_url + "/identical-images.html", expected=1)
        images = self.actionable(findings, "Images and media")
        self.assertEqual(len(images), 2, "Both identical missing-alt image occurrences must be reported")
        selectors = {finding["location"].get("selector") for finding in images}
        self.assertEqual(len(selectors), 2, "Equal markup must not collapse separate element locations")
        self.assertTrue(any(":nth-of-type(1)" in selector for selector in selectors), selectors)
        self.assertTrue(any(":nth-of-type(2)" in selector for selector in selectors), selectors)

    def test_meaningless_names_do_not_hide_findings(self):
        source = (FIXTURES / "accessible.html").read_text(encoding="utf-8")
        source = source.replace('alt="ריבוע שחור לדוגמה"', 'alt="image"')
        source = source.replace('שם לדוגמה</label>', '   </label>')
        project = self.project("bogus names", source)
        findings, _, _ = self.audit("--path", project, expected=1)
        actionable_categories = {f["category"].rstrip(".") for f in findings if f["status"] in {"fail", "warning"}}
        self.assertIn("Images and media", actionable_categories)
        self.assertIn("Forms, labels and validation", actionable_categories)

    def test_status_role_does_not_require_redundant_aria_live(self):
        project = self.project("implicit live region", self.document('<div role="status">Saved</div>'))
        findings, _, _ = self.audit("--path", project)
        self.assertEqual(self.actionable(findings, "ARIA, names, roles and states"), [],
                         "role=status already has implicit live-region semantics")

    def test_thead_is_not_a_header_cell(self):
        project = self.project("thead without headers", self.document(
            '<table><thead><tr><td>Item</td><td>Count</td></tr></thead>'
            '<tbody><tr><td>Example</td><td>2</td></tr></tbody></table>'))
        findings, _, _ = self.audit("--path", project, expected=1)
        self.assertTrue(self.actionable(findings, "Tables"), "thead must not count as a th header cell")

    def test_plain_paragraph_is_not_a_vague_link(self):
        project = self.project("ordinary paragraph", self.document('<p>more</p>'))
        findings, _, _ = self.audit("--path", project)
        self.assertEqual(self.actionable(findings, "Links and buttons"), [],
                         "A vague-link rule must inspect anchors, not ordinary text")

    def test_broken_labelledby_does_not_supply_a_label(self):
        for name, extra in (("missing label target", ""), ("empty label target", '<span id="missing"> </span>')):
            with self.subTest(case=name):
                project = self.project(name, self.document(
                    extra + '<input id="example" type="text" aria-labelledby="missing">'))
                findings, _, _ = self.audit("--path", project, output_name="report " + name, expected=1)
                self.assertTrue(self.actionable(findings, "Forms, labels and validation"),
                                "A missing or empty IDREF target does not give an input a name")

    def test_complex_colors_do_not_become_verified_solid_pairs(self):
        cases = {
            "transparent hex": "color:#ffffff;background-color:#ffffff00",
            "alpha function": "color:#ffffff;background-color:rgba(255,255,255,0.1)",
            "gradient": "color:#ffffff;background:linear-gradient(#ffffff,#000000)",
        }
        for name, declarations in cases.items():
            with self.subTest(case=name):
                project = self.project(name, self.document(f'<p style="{declarations}">Visible text</p>'))
                findings, _, _ = self.audit("--path", project, output_name="report " + name)
                contrast = [f for f in findings if f["category"].rstrip(".") == "Contrast and visual presentation"]
                claims = [f for f in contrast if f["status"] == "pass" or (
                    f["status"] == "fail" and f["evidence_type"] == "automatically-verified")]
                self.assertEqual(claims, [], "Compositing or gradient context must not become an opaque solid-pair verdict")

    def test_component_files_do_not_require_document_metadata(self):
        cases = {
            "jsx": 'export function Card() { return <section aria-label="Details"><h2>Card</h2><button type="button">Open details</button></section>; }',
            "tsx": 'export function Card() { return <section aria-label="Details"><h2>Card</h2><button type="button">Open details</button></section>; }',
            "vue": '<template><section aria-label="Details"><h2>Card</h2><button type="button">Open details</button></section></template>',
            "svelte": '<section aria-label="Details"><h2>Card</h2><button type="button">Open details</button></section>',
        }
        for extension, source in cases.items():
            with self.subTest(extension=extension):
                project = self.project("component " + extension, source, "Card." + extension)
                findings, _, _ = self.audit("--path", project, output_name="report component " + extension)
                for category in ("Language metadata", "Semantic structure and landmarks"):
                    self.assertEqual(self.actionable(findings, category), [],
                                     f"Standalone {extension} components do not own html/lang/title/main")

    def test_stylesheet_contrast_is_scanned_without_document_requirements(self):
        project = self.project("stylesheet only", ".example {color:#ffffff;background-color:#ffffff;}", "style.css")
        findings, _, _ = self.audit("--path", project, expected=1)
        self.assertTrue(self.actionable(findings, "Contrast and visual presentation"),
                        "A literal same-color CSS pair should flag potential invisible text")
        for category in ("Language metadata", "Semantic structure and landmarks"):
            self.assertEqual(self.actionable(findings, category), [],
                             "A stylesheet cannot own document metadata or landmarks")

    def test_hidden_images_do_not_name_parent_buttons(self):
        cases = {
            "hidden image": '<button type="button"><img hidden alt="Save"></button>',
            "aria hidden image": '<button type="button"><img aria-hidden="true" alt="Save"></button>',
            "hidden image ancestor": '<button type="button"><span hidden><img alt="Save"></span></button>',
            "aria hidden image ancestor": '<button type="button"><span aria-hidden="true"><img alt="Save"></span></button>',
        }
        for name, body in cases.items():
            with self.subTest(case=name):
                project = self.project(name, self.document(body))
                findings, _, _ = self.audit("--path", project, output_name="report " + name)
                self.assertTrue(self.actionable(findings, "Links and buttons"),
                                "Hidden descendant alternative text cannot supply the visible button's name")

    def test_hidden_ancestor_inputs_do_not_require_visible_labels(self):
        project = self.project("hidden ancestor input", self.document(
            '<section hidden><input type="text" id="hidden-field"></section>'))
        findings, _, _ = self.audit("--path", project)
        self.assertEqual(self.actionable(findings, "Forms, labels and validation"), [],
                         "A control in a hidden subtree is not a visible unlabeled form control")

    def test_aria_named_heading_is_not_empty(self):
        project = self.project("aria named heading", self.document('<h2 aria-label="Dashboard"></h2>'))
        findings, _, _ = self.audit("--path", project)
        failures = [f for f in findings if f["category"].rstrip(".") == "Headings" and f["status"] == "fail"]
        self.assertEqual(failures, [], "An explicit accessible name prevents a heading from being unnamed")

    def test_hidden_and_template_content_has_no_verified_image_or_heading_failures(self):
        cases = {
            "hidden image and heading": '<img hidden src="x"><h1 hidden></h1>',
            "template image and heading": '<template><img src="x"><h1></h1></template>',
        }
        for name, body in cases.items():
            with self.subTest(case=name):
                project = self.project(name, self.document(body))
                findings, _, _ = self.audit("--path", project, output_name="report " + name)
                failures = [f for f in findings if f["category"].rstrip(".") in {"Images and media", "Headings"}
                            and f["status"] == "fail" and f["evidence_type"] == "automatically-verified"]
                self.assertEqual(failures, [], "Hidden or inert template content is not rendered page content")

    def test_html_labels_do_not_name_nonlabelable_anchors(self):
        cases = {
            "label for anchor": '<label for="x">Name</label><a id="x" href="/next"></a>',
            "label wraps anchor": '<label>Name<a href="/next"></a></label>',
        }
        for name, body in cases.items():
            with self.subTest(case=name):
                project = self.project(name, self.document(body))
                findings, _, _ = self.audit("--path", project, output_name="report " + name)
                self.assertTrue(self.actionable(findings, "Links and buttons"),
                                "HTML labels only supply names to labelable controls, not anchors")

    def test_jsx_dynamic_style_context_prevents_verified_contrast(self):
        cases = {
            "jsx image style": "color:'#fff',backgroundColor:'#fff',backgroundImage:'url(hero.jpg)'",
            "jsx opacity style": "color:'#fff',backgroundColor:'#fff',opacity:0.2",
            "jsx spread style": "color:'#fff',backgroundColor:'#fff',...rest",
        }
        for name, declarations in cases.items():
            with self.subTest(case=name):
                source = "export function Example() { return <p style={{" + declarations + "}}>Text</p>; }"
                project = self.project(name, source, "Example.jsx")
                findings, _, _ = self.audit("--path", project, output_name="report " + name)
                contrast = [f for f in findings if f["category"].rstrip(".") == "Contrast and visual presentation"]
                self.assertTrue(contrast, "Unresolved JSX style context needs explicit contrast coverage")
                verified = [f for f in contrast if f["evidence_type"] == "automatically-verified"
                            and f["status"] in {"pass", "fail"}]
                self.assertEqual(verified, [], "Images, opacity, or spreads can invalidate extracted literal pairs")
                self.assertTrue(any(f["status"] == "not-tested" or f["evidence_type"] == "heuristic" for f in contrast))

    def test_template_dynamic_image_attributes_are_not_verified_missing_alt(self):
        cases = {
            "svelte shorthand image": ("Example.svelte", '<img {src} {alt}/>'),
            "vue bound image": ("Example.vue", '<template><img v-bind="imgAttrs"></template>'),
        }
        for name, (filename, source) in cases.items():
            with self.subTest(case=name):
                project = self.project(name, source, filename)
                findings, _, _ = self.audit("--path", project, output_name="report " + name)
                failures = [f for f in findings if f["category"].rstrip(".") == "Images and media"
                            and f["status"] == "fail" and f["evidence_type"] == "automatically-verified"]
                self.assertEqual(failures, [], "Dynamic shorthand or bindings may supply the image alternative")

    def test_hidden_controls_removed_from_tab_order_have_no_focusability_warning(self):
        cases = {
            "html hidden tabindex": ("index.html", self.document('<button aria-hidden="true" tabindex="-1">x</button>')),
            "html hidden disabled": ("index.html", self.document('<button aria-hidden="true" disabled>x</button>')),
            "jsx hidden tabindex": ("Example.jsx", 'export function Example() { return <button aria-hidden="true" tabIndex="-1">x</button>; }'),
            "jsx hidden disabled": ("Example.jsx", 'export function Example() { return <button aria-hidden="true" disabled>x</button>; }'),
        }
        for name, (filename, source) in cases.items():
            with self.subTest(case=name):
                project = self.project(name, source, filename)
                findings, _, _ = self.audit("--path", project, output_name="report " + name)
                self.assertEqual(self.actionable(findings, "Keyboard and focus"), [],
                                 "Explicitly removed tab stops or disabled controls are not default keyboard targets")

    def test_template_hidden_images_have_no_verified_missing_alt_failure(self):
        for extension in ("jsx", "tsx", "vue", "svelte"):
            with self.subTest(extension=extension):
                markup = '<img aria-hidden="true" src="icon.svg"/>'
                if extension in {"jsx", "tsx"}:
                    markup = "export function Icon() { return " + markup + "; }"
                elif extension == "vue":
                    markup = "<template>" + markup + "</template>"
                project = self.project("template hidden image " + extension, markup, "Icon." + extension)
                findings, _, _ = self.audit("--path", project, output_name="report template hidden image " + extension)
                failures = [f for f in findings if f["category"].rstrip(".") == "Images and media"
                            and f["status"] == "fail" and f["evidence_type"] == "automatically-verified"]
                self.assertEqual(failures, [], "Explicitly hidden template images are not announced as missing alternatives")

    def test_operational_error_does_not_inject_markdown(self):
        malicious = "missing\n\n# Injected heading\n\n![injected](https://example.invalid/image.png)\n"
        _, markdown, _ = self.audit("--path", self.root / malicious, expected=2)
        # CLI error formatting may repr-escape newlines before the renderer sees
        # them. Also test a literal operational error at the report boundary.
        report_api = runpy.run_path(str(REPO / "scripts" / "report.py"))
        report = report_api["build_report"]("fixture", "path", [], 0, [malicious], {})
        direct_markdown = report_api["render_markdown"](report)
        for source, rendered in (("CLI", markdown), ("renderer", direct_markdown)):
            with self.subTest(source=source):
                # Literal reproduction inside evidence fences or code is safe.
                prose, fence = [], None
                for line in rendered.splitlines():
                    match = re.match(r"^(`{3,}|~{3,})", line)
                    if match:
                        delimiter = match.group(1)
                        if fence is None:
                            fence = delimiter
                        elif delimiter[0] == fence[0] and len(delimiter) >= len(fence):
                            fence = None
                        continue
                    if fence is None:
                        prose.append(re.sub(r"`[^`]*`", "", line))
                prose = "\n".join(prose)
                self.assertNotRegex(prose, r"(?m)^# Injected heading\s*$")
                self.assertNotRegex(prose, r"(?<!\\)!\[injected\]\(https://example\.invalid/image\.png\)")

    def test_background_images_prevent_verified_inline_contrast(self):
        for name, foreground in (("background image false pass", "#000000"),
                                 ("background image false fail", "#ffffff")):
            with self.subTest(case=name):
                style = f"color:{foreground};background-color:#ffffff;background-image:linear-gradient(#ffffff,#000000)"
                project = self.project(name, self.document(f'<p style="{style}">Text</p>'))
                findings, _, _ = self.audit("--path", project, output_name="report " + name)
                contrast = [f for f in findings if f["category"].rstrip(".") == "Contrast and visual presentation"]
                self.assertTrue(contrast, "Unresolved image backgrounds need explicit contrast coverage")
                verified = [f for f in contrast if f["evidence_type"] == "automatically-verified"
                            and f["status"] in {"pass", "fail"}]
                self.assertEqual(verified, [], "A background image can cover the declared solid background")

    def test_relative_font_units_do_not_prove_large_text(self):
        for unit in ("em", "rem"):
            with self.subTest(unit=unit):
                style = f"color:#777777;background-color:#ffffff;font-size:2{unit}"
                project = self.project("relative font " + unit, self.document(f'<p style="{style}">Text</p>'))
                findings, _, _ = self.audit("--path", project, output_name="report relative font " + unit)
                contrast = [f for f in findings if f["category"].rstrip(".") == "Contrast and visual presentation"]
                self.assertTrue(contrast)
                self.assertFalse(any(f["status"] == "pass" for f in contrast),
                                 "An unresolved inherited or root font size cannot establish large-text eligibility")

    def test_css_precedence_does_not_produce_opposite_contrast_verdict(self):
        # Conservative warnings/not-tested are allowed; an opposite literal-pair
        # verdict is not. This checks declaration precedence, not computed CSS.
        cases = {
            "important foreground false fail": ("color:#000!important;color:#fff;background:#fff", "fail"),
            "important foreground false pass": ("color:#fff!important;color:#000;background:#fff", "pass"),
            "important background false fail": ("color:#000;background-color:#fff!important;background:#000", "fail"),
            "repeated longhand false fail": ("color:#000;background-color:#fff;background:#000;background-color:#fff", "fail"),
            "repeated shorthand false fail": ("color:#000;background:#fff;background-color:#000;background:#fff", "fail"),
            "repeated shorthand false pass": ("color:#000;background:#000;background-color:#fff;background:#000", "pass"),
        }
        for name, (declarations, forbidden) in cases.items():
            with self.subTest(case=name):
                project = self.project(name, ".example {" + declarations + ";}", "style.css")
                findings, _, _ = self.audit("--path", project, output_name="report " + name)
                contrast = [f for f in findings if f["category"].rstrip(".") == "Contrast and visual presentation"]
                self.assertTrue(contrast)
                self.assertFalse(any(f["status"] == forbidden for f in contrast),
                                 f"CSS declaration precedence does not support a {forbidden} verdict in {name}")

    def test_missing_dependency_has_clear_operational_error(self):
        requirements = REPO / "requirements.txt"
        entries = [line.strip() for line in requirements.read_text(encoding="utf-8").splitlines()
                   if line.strip() and not line.lstrip().startswith("#")]
        if not entries:
            self.skipTest("No third-party runtime dependencies declared; missing dependency is inapplicable")
        findings, markdown, result = self.audit("--path", self.fixture_project("accessible"),
                                                python_flags=("-S",), expected=2)
        combined = (result.stdout + result.stderr + markdown).lower()
        self.assertTrue(any(word in combined for word in ("dependency", "dependencies", "install", "requirements")))
        self.assertNotIn("traceback (most recent call last)", result.stderr.lower())


if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0], *unittest_args], verbosity=2)
