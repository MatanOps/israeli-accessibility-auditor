#!/usr/bin/env python3
"""Static scanner for frontend source trees (HTML, JSX, TSX, Vue, Svelte, CSS).

Adapted from ``a11y_scanner.py`` in alirezarezvani/claude-skills.
MIT License, Copyright (c) 2025 Alireza Rezvani. See THIRD_PARTY_NOTICES.md.

Retained from upstream: file collection and skipped directories, the tag and
attribute regular expressions, rule ids and remediation wording for the
component-level checks (images, form names, keyboard handlers, positive
tabindex, ARIA attributes, links, headings, tables, media).
Removed: the CLI, text/JSON formatters, severity-based exit codes, page-level
landmark checks on components and CSS, the role="button" keyboard exemption,
the id-as-label shortcut, the decorative-filename inference, the
role=alert/status aria-live rule, the H1 count rules and the line-level
vague-text regex.
Changed: tags are matched across lines with exact line offsets; comments,
scripts and style blocks are blanked; JSX/Vue/Svelte dynamic values and spread
props make a check heuristic or skip it; .html files are handed to
``html_scanner.scan_html``; CSS goes only through ``contrast_checker``.
Errors are raised, never swallowed.
"""

from __future__ import annotations

import bisect
import os
import re

import contrast_checker
from html_scanner import VALID_ARIA_ATTRS as _VALID_ARIA, VALID_ROLES as _VALID_ROLES
from html_scanner import closest_aria_attr as _closest_aria, scan_html
from report import ARIA, FORMS, HEADINGS, IMAGES, KEYBOARD, LINKS, TABLES, ZOOM, finding

SUPPORTED_EXTENSIONS = {".html", ".htm", ".jsx", ".tsx", ".vue", ".svelte", ".css"}
SKIPPED_DIRS = {"node_modules", ".git", "dist", "build", "__pycache__", ".next", ".nuxt", ".svelte-kit",
                "vendor", "coverage", "out", ".output", ".cache", "storybook-static"}

TAG_RE = re.compile(r"<([A-Za-z][\w.:-]*)\b((?:\"[^\"]*\"|'[^']*'|\{(?:[^{}]|\{[^{}]*\})*\}|[^>\"'{])*)(/?)>",
                    re.DOTALL)
ATTR_RE = re.compile(r"""([@:#\w.\-\[\]()]+)\s*=\s*(?:"([^"]*)"|'([^']*)'|(\{(?:[^{}]|\{[^{}]*\})*\})|([^\s>]+))""")
SPREAD_RE = re.compile(r"\{\s*\.\.\.")
BOOL_ATTR_RE = re.compile(r"(?<![\w\-:@.])([@:\w.\-]+)(?=\s|/?>|$)")
EXPR_RE = re.compile(r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}")

CLICK_ATTRS = {"onclick", "@click", "v-on:click", "on:click", "onclick.prevent", "@click.prevent",
               "on:click|preventdefault", "(click)", "ng-click", "x-on:click"}
KEY_ATTR_RE = re.compile(r"key(down|up|press)", re.IGNORECASE)
NATIVE_INTERACTIVE = {"button", "input", "select", "textarea", "summary", "option", "label", "details"}
NON_INTERACTIVE_CLICK_TARGETS = {"div", "span", "td", "tr", "li", "p", "section", "article", "img", "svg",
                                 "i", "b", "strong", "em", "label", "header", "footer", "nav", "main", "aside",
                                 "ul", "ol", "figure", "h1", "h2", "h3", "h4", "h5", "h6"}
INTERACTIVE_ROLES = {"button", "link", "checkbox", "radio", "switch", "tab", "menuitem", "menuitemcheckbox",
                     "menuitemradio", "option", "slider", "spinbutton", "textbox", "combobox", "searchbox",
                     "treeitem"}
VAGUE_LINK_TEXT = {
    "click here", "click", "here", "read more", "more", "link", "this", "learn more", "details", "more info",
    "continue", "go", "לחץ כאן", "לחצו כאן", "לחצי כאן", "כאן", "קרא עוד", "קראו עוד", "עוד", "לפרטים", "פרטים",
    "המשך", "קישור", "לחץ", "לחצו", "מידע נוסף",
}
GENERIC_ALT = {"image", "img", "picture", "photo", "graphic", "icon", "spacer", "untitled", "placeholder",
               "alt", "תמונה", "אייקון", "צילום", "לוגו"}


# ---------------------------------------------------------------------------
# File collection
# ---------------------------------------------------------------------------
def collect_files(path):
    """Recursively collect supported files; unreadable directories raise OSError."""
    files = []
    if os.path.isfile(path):
        if os.path.splitext(path)[1].lower() in SUPPORTED_EXTENSIONS:
            files.append(path)
        return files
    if not os.path.isdir(path):
        raise OSError("Not a file or directory: {}".format(path))
    if not os.access(path, os.R_OK | os.X_OK):
        raise OSError("Directory is not readable: {}".format(path))

    def on_error(error):
        raise error

    for root, dirs, filenames in os.walk(path, onerror=on_error):
        dirs[:] = sorted(d for d in dirs if d not in SKIPPED_DIRS and not d.startswith("."))
        for filename in filenames:
            if filename.startswith("."):
                continue
            if os.path.splitext(filename)[1].lower() in SUPPORTED_EXTENSIONS:
                files.append(os.path.join(root, filename))
    files.sort()
    return files


# ---------------------------------------------------------------------------
# Text preparation (line numbers are preserved everywhere)
# ---------------------------------------------------------------------------
def _blank(match):
    return re.sub(r"[^\n]", " ", match.group(0))


HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
JSX_COMMENT_RE = re.compile(r"\{\s*/\*.*?\*/\s*\}", re.DOTALL)
BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
LINE_COMMENT_RE = re.compile(r"(?m)^\s*//[^\n]*")
SCRIPT_BLOCK_RE = re.compile(r"<script\b[^>]*>.*?</script\s*>", re.DOTALL | re.IGNORECASE)
STYLE_BLOCK_RE = re.compile(r"<style\b[^>]*>(.*?)</style\s*>", re.DOTALL | re.IGNORECASE)


def prepare_template(text, extension):
    """Blank comments plus <script>/<style> blocks; return (template_text, style_blocks)."""
    styles = []
    for match in STYLE_BLOCK_RE.finditer(text):
        styles.append((text.count("\n", 0, match.start(1)) + 1, match.group(1)))
    text = HTML_COMMENT_RE.sub(_blank, text)
    text = JSX_COMMENT_RE.sub(_blank, text)
    if extension in (".jsx", ".tsx"):
        text = BLOCK_COMMENT_RE.sub(_blank, text)
        text = LINE_COMMENT_RE.sub(_blank, text)
    text = SCRIPT_BLOCK_RE.sub(_blank, text)
    text = STYLE_BLOCK_RE.sub(_blank, text)
    return text, styles


class _Lines(object):
    def __init__(self, text):
        self.text = text
        self.starts = [0] + [m.end() for m in re.finditer(r"\n", text)]
        self.lines = text.split("\n")

    def line_of(self, offset):
        return bisect.bisect_right(self.starts, offset)

    def snippet(self, offset, limit=160):
        line = self.line_of(offset)
        text = self.lines[line - 1].strip()
        return text if len(text) <= limit else text[:limit] + "..."


# ---------------------------------------------------------------------------
# Attribute parsing
# ---------------------------------------------------------------------------
class _Attrs(object):
    """Parsed attributes with dynamic-value awareness."""

    def __init__(self, attr_str):
        self.values = {}
        self.dynamic = set()
        self.spread = bool(SPREAD_RE.search(attr_str))
        for match in ATTR_RE.finditer(attr_str):
            raw_name = match.group(1)
            value = match.group(2) if match.group(2) is not None else match.group(3)
            if value is None:
                value = match.group(4) if match.group(4) is not None else match.group(5)
            name = self._normalise(raw_name)
            if match.group(4) is not None or raw_name.startswith((":", "v-bind:", "bind:")):
                self.dynamic.add(name)
            self.values[name] = value if value is not None else ""
        cleaned = ATTR_RE.sub(" ", attr_str)
        cleaned = EXPR_RE.sub(" ", cleaned)
        for match in BOOL_ATTR_RE.finditer(cleaned):
            name = self._normalise(match.group(1))
            if name and name not in self.values and not name.startswith("/"):
                self.values[name] = True

    @staticmethod
    def _normalise(name):
        lowered = name
        for prefix in ("v-bind:", "bind:", ":"):
            if lowered.startswith(prefix):
                lowered = lowered[len(prefix):]
                break
        if lowered == "htmlFor":
            return "for"
        if lowered == "className":
            return "class"
        # React camelCase (tabIndex, onClick, autoFocus) and HTML are matched case-insensitively.
        return lowered.lower()

    def has(self, name):
        return name in self.values

    def get(self, name):
        value = self.values.get(name)
        return value if isinstance(value, str) else ("" if value is True else None)

    def is_dynamic(self, name):
        return name in self.dynamic

    def literal(self, name):
        """A static string value, or None when absent or dynamic."""
        if name in self.dynamic or name not in self.values:
            return None
        value = self.values[name]
        return value if isinstance(value, str) else ""

    def names(self):
        return list(self.values)


def _strip_markup(inner):
    """Text content of an element body: tags and JSX expressions removed."""
    without_tags = re.sub(r"<[^>]*>", " ", inner)
    return " ".join(EXPR_RE.sub(" ", without_tags).split())


def _inner_content(text, start, tag):
    """Content between an opening tag ending at ``start`` and its closing tag (non-nested, best effort)."""
    close = re.compile(r"</{}\s*>".format(re.escape(tag)), re.IGNORECASE)
    match = close.search(text, start)
    if not match:
        return None
    return text[start:match.start()]


def _has_click_handler(attrs):
    return [name for name in attrs.names() if name.lower() in CLICK_ATTRS or name.lower().startswith(("onclick", "@click", "on:click"))]


def _has_key_handler(attrs):
    return any(KEY_ATTR_RE.search(name) for name in attrs.names())


def _explicit_or_wrapping_label(text, tag_start, attrs, ids_with_labels):
    identifier = attrs.literal("id")
    if identifier and identifier in ids_with_labels:
        return True
    # Wrapped in <label>: the nearest preceding <label ...> not yet closed.
    before = text[:tag_start]
    last_open = before.rfind("<label")
    if last_open != -1 and before.rfind("</label") < last_open:
        return True
    return False


# ---------------------------------------------------------------------------
# Component (JSX/TSX/Vue/Svelte fragment) checks
# ---------------------------------------------------------------------------
def scan_template(text, path, extension):
    """Regex-based checks for a component or template file."""
    template, styles = prepare_template(text, extension)
    lines = _Lines(template)
    results = []
    heading_levels = []
    label_targets = set(re.findall(r"<label\b[^>]*\b(?:for|htmlFor)\s*=\s*[\"']([^\"']+)[\"']", template))

    def loc(offset):
        return {"file": path, "line": lines.line_of(offset)}

    for match in TAG_RE.finditer(template):
        tag = match.group(1)
        if tag.startswith("/") or tag != tag.lower():
            continue  # closing tag or a custom component; native rules do not apply
        attrs = _Attrs(match.group(2))
        offset = match.start()
        location = loc(offset)
        snippet = lines.snippet(offset)
        opening = match.group(0) if len(match.group(0)) <= 200 else match.group(0)[:200] + "..."
        evidence_type = "heuristic" if (attrs.spread or attrs.dynamic) else "automatically-verified"

        # Images -----------------------------------------------------------
        if tag == "img":
            if not attrs.has("alt") and not attrs.spread and not attrs.has("aria-label") \
                    and not attrs.has("aria-labelledby"):
                if attrs.dynamic:
                    results.append(finding("img-alt-missing", IMAGES, "critical", "warning", "heuristic",
                                           "1.1.1 Non-text Content", location, opening,
                                           "The img element has no alt attribute and no ARIA name in this template. "
                                           "Other attributes are bound dynamically, so alt may still be supplied "
                                           "at runtime (for example through inherited attributes).",
                                           'Add an explicit alt="description" or alt="" for decorative images.'))
                else:
                    results.append(finding("img-alt-missing", IMAGES, "critical", "fail", "automatically-verified",
                                           "1.1.1 Non-text Content", location, opening,
                                           "The img element has no alt attribute and no ARIA name in this template.",
                                           'Add alt="description" or alt="" for decorative images.'))
            else:
                alt = attrs.literal("alt")
                if alt is not None and alt.strip().lower() in GENERIC_ALT:
                    results.append(finding("img-alt-generic", IMAGES, "moderate", "warning", "heuristic",
                                           "1.1.1 Non-text Content", location, opening,
                                           'The alt text "{}" looks like a placeholder rather than a description.'.format(alt),
                                           "Describe the purpose of the image, or use alt=\"\" if it is decorative."))
                role = (attrs.literal("role") or "").lower()
                if role in ("presentation", "none") and alt:
                    results.append(finding("img-decorative-role-with-alt", IMAGES, "moderate", "warning", "heuristic",
                                           "1.1.1 Non-text Content", location, opening,
                                           'The image is role="{}" but also has alt text.'.format(role),
                                           'Use alt="" for decorative images and drop the role, or keep meaningful alt.'))

        # Form controls ----------------------------------------------------
        if tag in ("input", "select", "textarea"):
            input_type = (attrs.literal("type") or "text").lower() if tag == "input" else tag
            if input_type == "hidden" or attrs.spread:
                pass
            elif input_type == "image":
                if not attrs.has("alt") and not attrs.has("aria-label") and not attrs.has("aria-labelledby"):
                    results.append(finding("input-image-alt-missing", IMAGES, "critical", "fail", evidence_type,
                                           "1.1.1 Non-text Content", location, opening,
                                           "An image button has no alt text or ARIA name.",
                                           "Add alt describing the button action."))
            else:
                named = (attrs.has("aria-label") and (attrs.literal("aria-label") is None or attrs.literal("aria-label").strip()))
                named = named or attrs.has("aria-labelledby") or attrs.has("title")
                named = named or (input_type in ("submit", "reset", "button") and attrs.has("value"))
                named = named or input_type in ("submit", "reset")
                named = named or _explicit_or_wrapping_label(template, offset, attrs, label_targets)
                if not named:
                    if attrs.has("placeholder"):
                        results.append(finding("form-control-placeholder-only", FORMS, "serious", "warning", "heuristic",
                                               "3.3.2 Labels or Instructions", location, opening,
                                               "Only a placeholder names this control in this file; placeholders "
                                               "vanish on input and are not a label.",
                                               "Add a visible <label> associated with the control."))
                    else:
                        results.append(finding("form-control-no-name", FORMS, "critical", "warning", "heuristic",
                                               "4.1.2 Name, Role, Value", location, opening,
                                               "No label association (label[for]/htmlFor, wrapping label, aria-label, "
                                               "aria-labelledby or title) was found for this {} in this file. A parent "
                                               "component may supply one; an id alone is not a label.".format(input_type),
                                               "Associate a visible label with the control."))

        # Keyboard ---------------------------------------------------------
        click = _has_click_handler(attrs)
        if click and tag not in NATIVE_INTERACTIVE and not (tag == "a" and attrs.has("href")):
            role = (attrs.literal("role") or "").lower()
            if not (role in INTERACTIVE_ROLES and attrs.has("tabindex") and _has_key_handler(attrs)):
                results.append(finding("click-handler-nonsemantic", KEYBOARD, "serious", "warning", "heuristic",
                                       "2.1.1 Keyboard", location, opening,
                                       "A <{}> element has a click handler ({}) but is not a native control and does "
                                       "not combine an interactive role, tabindex and a keyboard handler. Framework "
                                       "wrappers or delegated handlers are not resolved.".format(tag, ", ".join(click)),
                                       "Use <button>, or add role, tabIndex={0} and Enter/Space key handling."))
        tabindex = attrs.literal("tabindex") or attrs.literal("tabIndex")
        if tabindex and tabindex.strip().lstrip("-").isdigit() and int(tabindex) > 0:
            results.append(finding("tabindex-positive", KEYBOARD, "moderate", "warning", "heuristic",
                                   "2.4.3 Focus Order", location, opening,
                                   'tabindex="{}" forces a custom tab order.'.format(tabindex.strip()),
                                   'Use tabindex="0" or "-1" and rely on DOM order.'))
        if attrs.literal("aria-hidden") == "true" and (
                tag in ("button", "select", "textarea", "input") or (tag == "a" and attrs.has("href"))):
            results.append(finding("aria-hidden-focusable", KEYBOARD, "serious", "warning", "heuristic",
                                   "4.1.2 Name, Role, Value", location, opening,
                                   'aria-hidden="true" on a natively focusable element.',
                                   "Remove aria-hidden or make the element unfocusable."))

        # ARIA -------------------------------------------------------------
        for name in attrs.names():
            lowered = name.lower()
            if lowered.startswith("aria-") and lowered not in _VALID_ARIA:
                closest = _closest_aria(lowered)
                if closest:
                    results.append(finding("aria-attribute-misspelled", ARIA, "serious", "fail", "automatically-verified",
                                           "4.1.2 Name, Role, Value", location, opening,
                                           '"{}" is not a WAI-ARIA attribute; it looks like a misspelling of "{}".'.format(name, closest),
                                           'Rename the attribute to "{}".'.format(closest)))
                else:
                    results.append(finding("aria-attribute-unknown", ARIA, "moderate", "warning", "heuristic",
                                           "4.1.2 Name, Role, Value", location, opening,
                                           '"{}" is not in the bundled WAI-ARIA 1.2 attribute list.'.format(name),
                                           "Verify the attribute against the current WAI-ARIA specification."))
        role_literal = attrs.literal("role")
        if role_literal is not None and role_literal.strip():
            tokens = role_literal.split()
            if not any(t.lower() in _VALID_ROLES or t.lower().startswith(("doc-", "graphics-")) for t in tokens):
                results.append(finding("role-invalid", ARIA, "moderate", "fail", "automatically-verified",
                                       "4.1.2 Name, Role, Value", location, opening,
                                       'role="{}" is not a WAI-ARIA role.'.format(role_literal),
                                       "Use a valid ARIA role or remove the attribute."))

        # Links and buttons ------------------------------------------------
        if tag in ("a", "button") and not match.group(3):
            inner = _inner_content(template, match.end(), tag)
            if inner is not None and not attrs.spread:
                has_children = bool(re.search(r"<[A-Z][\w.]*", inner)) or "<slot" in inner \
                    or "<img" in inner or "<svg" in inner or "<i " in inner or "<i>" in inner
                has_expr = bool(EXPR_RE.search(inner)) or has_children or "{{" in inner
                text_content = _strip_markup(inner)
                aria_named = (attrs.has("aria-label") or attrs.has("aria-labelledby") or attrs.has("title"))
                if tag == "a" and not attrs.has("href"):
                    pass
                elif not text_content and not aria_named and has_expr and not has_children:
                    pass  # content is an expression such as {label}; unknowable statically
                elif not text_content and not aria_named:
                    if has_expr:
                        results.append(finding("{}-no-static-name".format("link" if tag == "a" else "button"), LINKS,
                                               "serious", "warning", "heuristic",
                                               "2.4.4 Link Purpose (In Context)" if tag == "a" else "4.1.2 Name, Role, Value",
                                               location, opening,
                                               "The {} has no static text; its content comes from expressions, "
                                               "child components, images or slots that were not resolved.".format(tag),
                                               "Ensure the rendered content or an aria-label provides a name."))
                    else:
                        results.append(finding("{}-no-name".format("link" if tag == "a" else "button"), LINKS,
                                               "critical", "fail", "automatically-verified",
                                               "2.4.4 Link Purpose (In Context)" if tag == "a" else "4.1.2 Name, Role, Value",
                                               location, opening,
                                               "The {} element is empty and has no ARIA name.".format(tag),
                                               "Add text content or an aria-label."))
                elif tag == "a" and text_content and not has_expr:
                    normalised = re.sub(r"[\s\.\!\:\>\»\«\…]+", " ", text_content.lower()).strip()
                    if normalised in VAGUE_LINK_TEXT:
                        results.append(finding("link-vague-text", LINKS, "moderate", "warning", "heuristic",
                                               "2.4.4 Link Purpose (In Context)", location, opening + text_content[:40],
                                               'The link text "{}" does not describe the destination on its own.'.format(text_content[:40]),
                                               "Use link text that states the destination or action."))
            href = attrs.literal("href")
            if tag == "a" and href is not None and (href.strip() == "#" or href.strip().lower().startswith("javascript:")):
                results.append(finding("link-placeholder-href", LINKS, "minor", "warning", "heuristic",
                                       "2.4.4 Link Purpose (In Context)", location, opening,
                                       'href="{}" indicates a script-driven control presented as a link.'.format(href.strip()[:30]),
                                       "Use a <button> for actions."))

        # Headings ---------------------------------------------------------
        if re.match(r"^h[1-6]$", tag):
            heading_levels.append((int(tag[1]), offset, opening))

        # Tables -----------------------------------------------------------
        if tag == "table":
            role = (attrs.literal("role") or "").lower()
            inner = _inner_content(template, match.end(), "table")
            if role not in ("presentation", "none") and inner is not None and not attrs.spread:
                has_header = re.search(r"<th\b", inner) or re.search(r'role\s*=\s*["\'](columnheader|rowheader)', inner)
                has_cells = re.search(r"<td\b", inner)
                dynamic_rows = bool(EXPR_RE.search(inner)) or bool(re.search(r"<[A-Z][\w.]*", inner)) \
                    or "v-for" in inner or "{#each" in inner
                if has_cells and not has_header:
                    results.append(finding("table-no-header-cells", TABLES, "serious", "warning", "heuristic",
                                           "1.3.1 Info and Relationships", location, opening,
                                           "The table markup has td cells but no th or header-role cells in this "
                                           "file{}. Data tables need headers; layout tables should be "
                                           "role=\"presentation\".".format(
                                               " (rows are partly generated dynamically)" if dynamic_rows else ""),
                                           "Mark header cells with <th scope=\"col|row\"> for data tables."))

        # Media ------------------------------------------------------------
        if tag == "video":
            inner = _inner_content(template, match.end(), "video") or ""
            if not re.search(r'<track\b[^>]*kind\s*=\s*["\'](captions|subtitles)', inner) and not EXPR_RE.search(inner):
                results.append(finding("video-no-caption-track", IMAGES, "serious", "warning", "heuristic",
                                       "1.2.2 Captions (Prerecorded)", location, opening,
                                       "No caption or subtitle track is declared for this video in this file.",
                                       'Add <track kind="captions"> or confirm captions are provided otherwise.'))
        if tag in ("video", "audio") and attrs.has("autoplay") and not attrs.has("muted") and not attrs.has("controls"):
            results.append(finding("media-autoplay-no-controls", IMAGES, "serious", "warning", "heuristic",
                                   "1.4.2 Audio Control", location, opening,
                                   "Media autoplays without controls and is not muted.",
                                   "Add controls, mute autoplaying media, or avoid autoplay."))

        # Zoom -------------------------------------------------------------
        if tag == "meta" and (attrs.literal("name") or "").lower() == "viewport":
            content = (attrs.literal("content") or "").lower().replace(" ", "")
            scale = re.search(r"maximum-scale=([0-9.]+)", content)
            if "user-scalable=no" in content or "user-scalable=0" in content or (scale and float(scale.group(1)) < 2):
                results.append(finding("viewport-zoom-restricted", ZOOM, "serious", "fail", "automatically-verified",
                                       "1.4.4 Resize Text", location, opening,
                                       "The viewport meta tag disables or limits pinch zoom.",
                                       "Remove user-scalable=no and any maximum-scale below 2."))

        # Inline styles ----------------------------------------------------
        style = attrs.literal("style")
        if style:
            entry, unknown = contrast_checker.inline_style_pair(style)
            if entry:
                results.append(contrast_checker.pair_finding(entry, location, "style attribute on <{}>".format(tag)))
        elif attrs.is_dynamic("style") and attrs.get("style"):
            object_literal = attrs.get("style")
            values = dict(re.findall(r"(color|backgroundColor|background|fontSize|fontWeight)\s*:\s*[\"'`]([^\"'`]+)[\"'`]",
                                     object_literal))
            if "color" in values and ("backgroundColor" in values or "background" in values):
                css_like = "color:{};background-color:{};".format(values["color"], values.get("backgroundColor") or values.get("background"))
                if "fontSize" in values:
                    css_like += "font-size:{};".format(values["fontSize"])
                if "fontWeight" in values:
                    css_like += "font-weight:{};".format(values["fontWeight"])
                entry, unknown = contrast_checker.inline_style_pair(css_like)
                if entry:
                    results.append(contrast_checker.pair_finding(entry, location, "style object on <{}>".format(tag)))

    previous = None
    for level, offset, opening in heading_levels:
        if previous is not None and level > previous + 1:
            results.append(finding("heading-level-skipped", HEADINGS, "moderate", "warning", "heuristic",
                                   "1.3.1 Info and Relationships", loc(offset), opening,
                                   "Within this file the heading level jumps from h{} to h{}. Component composition "
                                   "may change the final outline.".format(previous, level),
                                   "Use consecutive heading levels that reflect the rendered outline."))
        previous = level

    for base_line, css_text in styles:
        def make_location(line, _base=base_line):
            return {"file": path, "line": _base + line - 1}
        results.extend(contrast_checker.css_findings(css_text, make_location, "embedded <style> in {}".format(
            os.path.basename(path))))
    return results


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------
def scan_css(text, path):
    return contrast_checker.css_findings(text, lambda line: {"file": path, "line": line}, os.path.basename(path))


def scan_file(path):
    """Scan one file by extension. Read/parse errors propagate to the caller."""
    extension = os.path.splitext(path)[1].lower()
    if extension not in SUPPORTED_EXTENSIONS:
        raise ValueError("Unsupported file type: {}".format(path))
    with open(path, "rb") as handle:
        raw = handle.read()
    if extension in (".html", ".htm"):
        return scan_html(raw, {"file": path})
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise UnicodeError("{} is not valid UTF-8; the file was not scanned".format(path))
    if extension == ".css":
        return scan_css(text, path)
    return scan_template(text, path, extension)
