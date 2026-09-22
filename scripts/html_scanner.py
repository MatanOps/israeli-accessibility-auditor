#!/usr/bin/env python3
"""Static HTML accessibility checks with Hebrew/RTL context (BeautifulSoup based).

Adapted from ``audit_a11y.py`` in skills-il/localization
(``israeli-accessibility-compliance/scripts/audit_a11y.py``).
MIT License, Copyright (c) 2026 Skills IL (Yootech). See THIRD_PARTY_NOTICES.md.

Retained from upstream: the BeautifulSoup traversal, the tree-based label
association (explicit, implicit, ARIA), heading-skip detection, skip-link and
accessibility-statement keyword heuristics and the byte-preserving parsing
approach for Hebrew pages.
Removed: HTTP fetching, the pass/fail score, the "NON-COMPLIANT" verdict and
the assumption that every page must be Hebrew and RTL.
Changed: language and direction are inferred from the visible content, ARIA
references are resolved to non-empty targets, and every check produces the
project finding model (status, evidence type, severity, WCAG criterion).
Consolidated here (instead of the regex source scanner) are the image, name,
ARIA attribute, table header, media, viewport and inline/embedded contrast
checks for complete HTML documents and fragments.
"""

from __future__ import annotations

import re

from bs4 import BeautifulSoup
from bs4.element import Comment, NavigableString, Tag

import contrast_checker
from report import (ARIA, CONTRAST, FORMS, HEADINGS, HEBREW, IMAGES, KEYBOARD, LANGUAGE, LINKS,
                    STATEMENT, STRUCTURE, TABLES, ZOOM, finding)

# ---------------------------------------------------------------------------
# Vocabularies
# ---------------------------------------------------------------------------
VALID_ARIA_ATTRS = {
    "aria-activedescendant", "aria-atomic", "aria-autocomplete", "aria-braillelabel",
    "aria-brailleroledescription", "aria-busy", "aria-checked", "aria-colcount", "aria-colindex",
    "aria-colindextext", "aria-colspan", "aria-controls", "aria-current", "aria-describedby",
    "aria-description", "aria-details", "aria-disabled", "aria-dropeffect", "aria-errormessage",
    "aria-expanded", "aria-flowto", "aria-grabbed", "aria-haspopup", "aria-hidden", "aria-invalid",
    "aria-keyshortcuts", "aria-label", "aria-labelledby", "aria-level", "aria-live", "aria-modal",
    "aria-multiline", "aria-multiselectable", "aria-orientation", "aria-owns", "aria-placeholder",
    "aria-posinset", "aria-pressed", "aria-readonly", "aria-relevant", "aria-required",
    "aria-roledescription", "aria-rowcount", "aria-rowindex", "aria-rowindextext", "aria-rowspan",
    "aria-selected", "aria-setsize", "aria-sort", "aria-valuemax", "aria-valuemin", "aria-valuenow",
    "aria-valuetext",
}

VALID_ROLES = {
    "alert", "alertdialog", "application", "article", "banner", "blockquote", "button", "caption", "cell",
    "checkbox", "code", "columnheader", "combobox", "complementary", "contentinfo", "definition", "deletion",
    "dialog", "directory", "document", "emphasis", "feed", "figure", "form", "generic", "grid", "gridcell",
    "group", "heading", "img", "image", "insertion", "link", "list", "listbox", "listitem", "log", "main",
    "marquee", "math", "menu", "menubar", "menuitem", "menuitemcheckbox", "menuitemradio", "meter",
    "navigation", "none", "note", "option", "paragraph", "presentation", "progressbar", "radio", "radiogroup",
    "region", "row", "rowgroup", "rowheader", "scrollbar", "search", "searchbox", "separator", "slider",
    "spinbutton", "status", "strong", "subscript", "suggestion", "superscript", "switch", "tab", "table",
    "tablist", "tabpanel", "term", "textbox", "time", "timer", "toolbar", "tooltip", "tree", "treegrid",
    "treeitem",
}
INTERACTIVE_ROLES = {"button", "link", "checkbox", "radio", "switch", "tab", "menuitem", "menuitemcheckbox",
                     "menuitemradio", "option", "slider", "spinbutton", "textbox", "combobox", "searchbox",
                     "treeitem", "gridcell", "scrollbar"}
REQUIRED_STATES = {"checkbox": "aria-checked", "switch": "aria-checked", "radio": "aria-checked",
                   "heading": "aria-level", "slider": "aria-valuenow", "combobox": "aria-expanded",
                   "scrollbar": "aria-valuenow"}
IDREF_ATTRS = ("aria-labelledby", "aria-describedby", "aria-controls", "aria-owns", "aria-activedescendant",
               "aria-errormessage", "aria-flowto", "aria-details")

CLICK_ATTRS = ("onclick", "@click", "v-on:click", "on:click", "ng-click", "(click)", "x-on:click")
KEY_ATTR_RE = re.compile(r"key(down|up|press)", re.IGNORECASE)
NATIVE_INTERACTIVE = {"button", "input", "select", "textarea", "summary", "option", "label", "details"}

VAGUE_LINK_TEXT = {
    "click here", "click", "here", "read more", "more", "link", "this", "learn more", "details", "more info",
    "continue", "go", "לחץ כאן", "לחצו כאן", "לחצי כאן", "כאן", "קרא עוד", "קראו עוד", "עוד", "לפרטים", "פרטים",
    "המשך", "קישור", "לחץ", "לחצו", "מידע נוסף",
}
GENERIC_ALT = {"image", "img", "picture", "photo", "graphic", "icon", "spacer", "untitled", "placeholder",
               "alt", "תמונה", "אייקון", "צילום", "לוגו"}
SKIP_LINK_RE = re.compile(r"(skip|jump|דלג|דילוג|לתוכן|לניווט)", re.IGNORECASE)
STATEMENT_RE = re.compile(r"(הצהרת\s*נגישות|accessibility\s*statement|negishut|נגישות|accessibility)",
                          re.IGNORECASE)
HEBREW_RE = re.compile("[\u0590-\u05FF]")
LATIN_RE = re.compile(r"[A-Za-z]")
LANG_TAG_RE = re.compile(r"^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{1,8})*$")
NON_VISIBLE_TAGS = {"script", "style", "template", "noscript", "head", "title", "meta", "link"}


# ---------------------------------------------------------------------------
# Context and helpers
# ---------------------------------------------------------------------------
class _Context(object):
    def __init__(self, soup, location):
        self.soup = soup
        self.file = location.get("file")
        self.url = location.get("url")
        self.ids = {}
        for tag in soup.find_all(True):
            identifier = _attr(tag, "id")
            if identifier:
                self.ids.setdefault(identifier, []).append(tag)
        self.labels_for = {}
        for label in soup.find_all("label"):
            target = _attr(label, "for")
            if target:
                self.labels_for.setdefault(target, []).append(label)
        self.is_document = (soup.find("html") is not None or soup.find("head") is not None
                            or soup.find("body") is not None)
        body_text = visible_text(soup.body or soup)
        title = soup.title.get_text(" ", strip=True) if soup.title else ""
        self.hebrew_letters = len(HEBREW_RE.findall(body_text + title))
        self.latin_letters = len(LATIN_RE.findall(body_text + title))
        self.visible_length = len(body_text.strip())
        self.hebrew_dominant = self.hebrew_letters >= 10 and self.hebrew_letters > self.latin_letters
        self.latin_dominant = self.latin_letters >= 10 and self.latin_letters > self.hebrew_letters * 2

    def loc(self, tag=None, line=None):
        selector = selector_for(tag) if isinstance(tag, Tag) else None
        if line is None and isinstance(tag, Tag):
            line = tag.sourceline
        if self.url is not None:
            return {"url": self.url, "selector": selector, "line": line}
        return {"file": self.file, "line": line, "selector": selector}

    def target_exists(self, fragment):
        return bool(fragment) and fragment in self.ids


def _attr(tag, name):
    value = tag.get(name)
    if value is None:
        return None
    if isinstance(value, list):
        return " ".join(str(item) for item in value)
    return str(value)


def _has(tag, name):
    return name in tag.attrs


def opening_tag(tag, limit=200):
    parts = [tag.name]
    for key, value in tag.attrs.items():
        if isinstance(value, list):
            value = " ".join(str(item) for item in value)
        if value is True or value == "":
            parts.append(key)
        else:
            parts.append('{}="{}"'.format(key, str(value).replace('"', "&quot;")))
    text = "<" + " ".join(parts) + ">"
    return text if len(text) <= limit else text[:limit] + "..."


def selector_for(tag):
    """Build a simple CSS path (stops at the nearest ancestor with an id)."""
    parts = []
    current = tag
    while isinstance(current, Tag) and current.name not in ("[document]",):
        identifier = _attr(current, "id")
        if identifier and re.match(r"^[A-Za-z_][\w-]*$", identifier):
            parts.append("{}#{}".format(current.name, identifier))
            break
        siblings = [s for s in current.parent.children if isinstance(s, Tag) and s.name == current.name] \
            if isinstance(current.parent, Tag) else []
        if len(siblings) > 1:
            index = next(i for i, sibling in enumerate(siblings, 1) if sibling is current)
            parts.append("{}:nth-of-type({})".format(current.name, index))
        else:
            parts.append(current.name)
        current = current.parent
    return " > ".join(reversed(parts))


def visible_text(root):
    """Text a sighted user could see (scripts, styles, templates and comments excluded)."""
    if root is None:
        return ""
    pieces = []
    for node in root.descendants:
        if isinstance(node, Comment) or not isinstance(node, NavigableString):
            continue
        parent = node.parent
        if isinstance(parent, Tag) and (parent.name in NON_VISIBLE_TAGS
                                        or parent.find_parent(list(NON_VISIBLE_TAGS)) is not None):
            continue
        pieces.append(str(node))
    return " ".join(" ".join(pieces).split())


def _is_hidden(tag):
    return _attr(tag, "aria-hidden") == "true" or _has(tag, "hidden")


def hidden_in_tree(tag, stop=None):
    """Recognise explicit hidden subtrees; CSS visibility remains untested."""
    while isinstance(tag, Tag) and tag is not stop:
        if _is_hidden(tag) or tag.name in ("template", "script", "style"):
            return True
        tag = tag.parent
    return False


def name_text(root, exclude=None):
    """Approximate text content usable as an accessible name.

    Includes img alt, svg <title> and aria-label of descendants; skips
    aria-hidden subtrees and the excluded control. Not the full accessible
    name computation; dynamic cases stay heuristic.
    """
    pieces = []
    for node in root.descendants:
        if isinstance(node, Comment):
            continue
        if isinstance(node, Tag):
            if node is exclude or hidden_in_tree(node, stop=root):
                continue
            if node.name == "img":
                pieces.append(_attr(node, "alt") or "")
            elif node.name == "title" and node.find_parent("svg") is not None:
                pieces.append(node.get_text(" ", strip=True))
            elif _attr(node, "aria-label"):
                pieces.append(_attr(node, "aria-label"))
            continue
        if not isinstance(node, NavigableString):
            continue
        parent = node.parent
        skip = False
        while isinstance(parent, Tag) and parent is not root:
            if parent is exclude or _is_hidden(parent) or parent.name in ("script", "style", "template",
                                                                          "select", "textarea", "title"):
                skip = True
                break
            parent = parent.parent
        if not skip:
            pieces.append(str(node))
    return " ".join(" ".join(pieces).split())


def resolve_idrefs(ctx, value):
    """Return (text, missing_ids) for a space separated IDREF list."""
    texts, missing = [], []
    for identifier in (value or "").split():
        targets = ctx.ids.get(identifier)
        if not targets:
            missing.append(identifier)
            continue
        target = targets[0]
        text = _attr(target, "aria-label") or name_text(target) or _attr(target, "alt") or ""
        if target.name == "input" and not text:
            text = _attr(target, "value") or ""
        if text.strip():
            texts.append(text.strip())
    return " ".join(texts).strip(), missing


def accessible_name(ctx, tag):
    """Return (source, text) for the first non-empty name candidate, or (None, note)."""
    if _has(tag, "aria-labelledby"):
        text, missing = resolve_idrefs(ctx, _attr(tag, "aria-labelledby"))
        if text:
            return "aria-labelledby", text
        note = "aria-labelledby references {}".format(
            "missing ids " + ", ".join(missing) if missing else "elements without text")
    else:
        note = ""
    if (_attr(tag, "aria-label") or "").strip():
        return "aria-label", _attr(tag, "aria-label").strip()
    labelable = tag.name in ("button", "input", "meter", "output", "progress", "select", "textarea")
    identifier = _attr(tag, "id")
    if labelable and identifier:
        for label in ctx.labels_for.get(identifier, []):
            text = name_text(label, exclude=tag)
            if text:
                return "label[for]", text
    parent_label = tag.find_parent("label") if labelable else None
    if parent_label is not None:
        text = name_text(parent_label, exclude=tag)
        if text:
            return "wrapping label", text
    if tag.name in ("button", "a", "summary") or _attr(tag, "role") in INTERACTIVE_ROLES:
        text = name_text(tag)
        if text:
            return "content", text
    if tag.name == "input":
        input_type = (_attr(tag, "type") or "text").lower()
        if input_type == "image" and (_attr(tag, "alt") or "").strip():
            return "alt", _attr(tag, "alt").strip()
        if input_type in ("submit", "reset", "button") and (_attr(tag, "value") or "").strip():
            return "value", _attr(tag, "value").strip()
        if input_type in ("submit", "reset"):
            return "browser default", input_type
    if (_attr(tag, "title") or "").strip():
        return "title", _attr(tag, "title").strip()
    return None, note


def _levenshtein(a, b):
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        current = [i]
        for j, cb in enumerate(b, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (ca != cb)))
        previous = current
    return previous[-1]


def closest_aria_attr(name):
    best, distance = None, 3
    for candidate in VALID_ARIA_ATTRS:
        d = _levenshtein(name, candidate)
        if d < distance:
            best, distance = candidate, d
    return best


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------
def check_language(ctx):
    results = []
    html_tag = ctx.soup.find("html")
    if not ctx.is_document or html_tag is None:
        return results
    lang = _attr(html_tag, "lang")
    location = ctx.loc(html_tag)
    if lang is None or not lang.strip():
        results.append(finding("lang-missing", LANGUAGE, "serious", "fail", "automatically-verified",
                               "3.1.1 Language of Page", location, opening_tag(html_tag),
                               "The html element declares no language, so assistive technology cannot select "
                               "Hebrew or English pronunciation rules.",
                               'Add lang="he" for Hebrew pages or the correct BCP 47 tag, e.g. lang="en".'))
        return results
    primary = lang.strip().split("-")[0].lower()
    evidence = opening_tag(html_tag) + " | visible letters: Hebrew {} / Latin {}".format(
        ctx.hebrew_letters, ctx.latin_letters)
    if not LANG_TAG_RE.match(lang.strip()):
        results.append(finding("lang-invalid", LANGUAGE, "serious", "warning", "heuristic",
                               "3.1.1 Language of Page", location, evidence,
                               'lang="{}" is not a well-formed BCP 47 language tag (expected a 2-3 letter '
                               'primary subtag such as he, he-IL or en).'.format(lang),
                               'Use a valid tag, for example lang="he" or lang="en".'))
    elif ctx.hebrew_dominant and primary not in ("he", "iw"):
        results.append(finding("lang-hebrew-mismatch", LANGUAGE, "serious", "warning", "heuristic",
                               "3.1.1 Language of Page", location, evidence,
                               'The visible content is predominantly Hebrew but the page declares lang="{}". '
                               'Language inference from letter counts is heuristic.'.format(lang),
                               'Declare lang="he" (or he-IL) on the html element when the page is Hebrew.'))
    elif ctx.latin_dominant and primary in ("he", "iw"):
        results.append(finding("lang-hebrew-declared-latin-content", LANGUAGE, "moderate", "warning", "heuristic",
                               "3.1.1 Language of Page", location, evidence,
                               "The page declares Hebrew but the visible content is mostly Latin script. "
                               "This may be a template default; language inference is heuristic.",
                               "Declare the actual primary language of the page."))
    else:
        results.append(finding("lang-declared", LANGUAGE, "info", "pass", "automatically-verified",
                               "3.1.1 Language of Page", location, evidence,
                               'The html element declares lang="{}", which is well-formed and consistent with '
                               'the dominant visible script. Language of parts was not checked.'.format(lang),
                               "None for this check; mark language changes inside the page with lang."))
    return results


def check_title(ctx):
    results = []
    if not ctx.is_document or ctx.soup.find("html") is None:
        return results
    title = ctx.soup.find("title")
    anchor = ctx.soup.find("head") or ctx.soup.find("html")
    if title is None:
        results.append(finding("title-missing", STRUCTURE, "serious", "fail", "automatically-verified",
                               "2.4.2 Page Titled", ctx.loc(anchor), opening_tag(anchor),
                               "The document has no title element.",
                               "Add a descriptive <title> that identifies the page."))
    elif not title.get_text(strip=True):
        results.append(finding("title-empty", STRUCTURE, "serious", "fail", "automatically-verified",
                               "2.4.2 Page Titled", ctx.loc(title), opening_tag(title),
                               "The title element is empty.", "Provide a descriptive page title."))
    else:
        results.append(finding("title-present", STRUCTURE, "info", "pass", "automatically-verified",
                               "2.4.2 Page Titled", ctx.loc(title), title.get_text(" ", strip=True)[:120],
                               "A non-empty title exists. Whether it describes the page is not verified.",
                               "None for this check."))
    return results


def check_direction(ctx):
    results = []
    html_tag = ctx.soup.find("html")
    if not ctx.is_document or html_tag is None or not ctx.hebrew_dominant:
        return results
    body = ctx.soup.find("body")
    declared = (_attr(html_tag, "dir") or "").lower() or ((_attr(body, "dir") or "").lower() if body else "")
    css_rtl = any(re.search(r"direction\s*:\s*rtl", style.get_text() or "", re.IGNORECASE)
                  for style in ctx.soup.find_all("style"))
    if declared == "rtl" or (not declared and css_rtl):
        return results
    evidence = opening_tag(html_tag) + " | visible letters: Hebrew {} / Latin {}".format(
        ctx.hebrew_letters, ctx.latin_letters)
    if declared and declared != "rtl":
        explanation = ('The visible content is predominantly Hebrew but the base direction is dir="{}". '
                       'Hebrew text may still render correctly through the Unicode bidi algorithm, so this is a '
                       'best-practice warning and a candidate for 1.3.2, not an automatic failure.'.format(declared))
    else:
        explanation = ("The visible content is predominantly Hebrew but no base direction is declared on html or "
                       "body, and no direction: rtl rule was found in embedded CSS. Punctuation and mixed numbers "
                       "may misorder; this is heuristic and needs rendered review.")
    results.append(finding("dir-hebrew-not-rtl", HEBREW, "moderate", "warning", "heuristic",
                           "1.3.2 Meaningful Sequence (candidate)", ctx.loc(html_tag), evidence, explanation,
                           'Set dir="rtl" on the html element for Hebrew pages and wrap left-to-right fragments '
                           'with dir="ltr" or <bdi>.'))
    return results


def check_images(ctx):
    results = []
    images = [img for img in ctx.soup.find_all("img") if not hidden_in_tree(img)]
    decorative = []
    missing = 0
    for img in images:
        location = ctx.loc(img)
        snippet = opening_tag(img)
        if not _has(img, "alt"):
            source, text = accessible_name(ctx, img)
            if source in ("aria-label", "aria-labelledby", "title"):
                results.append(finding("img-alt-missing-aria-name", IMAGES, "info", "pass",
                                       "automatically-verified", "1.1.1 Non-text Content", location, snippet,
                                       "The image has no alt attribute but carries a non-empty name via {} "
                                       "({}). Support for this pattern and the usefulness of the text need "
                                       "human review.".format(source, text[:80]),
                                       "Prefer a real alt attribute; keep the ARIA name meaningful."))
                continue
            missing += 1
            results.append(finding("img-alt-missing", IMAGES, "critical", "fail", "automatically-verified",
                                   "1.1.1 Non-text Content", location, snippet,
                                   "The img element has no alt attribute and no ARIA name, so screen readers "
                                   "announce the file name or nothing.",
                                   'Add alt="description" for informative images or alt="" for decorative ones.'))
            continue
        alt = (_attr(img, "alt") or "").strip()
        role = (_attr(img, "role") or "").lower()
        if not alt:
            decorative.append(img)
            continue
        if role in ("presentation", "none"):
            results.append(finding("img-decorative-role-with-alt", IMAGES, "moderate", "warning", "heuristic",
                                   "1.1.1 Non-text Content", location, snippet,
                                   'The image is marked role="{}" but also has alt text, which sends mixed '
                                   'signals to assistive technology.'.format(role),
                                   'Use alt="" without a presentational role for decorative images, or remove '
                                   'the role and keep meaningful alt text.'))
        src = (_attr(img, "src") or "").rsplit("/", 1)[-1].split("?")[0]
        lowered = alt.lower()
        if (lowered in GENERIC_ALT or re.match(r"^[\w-]+\.(png|jpe?g|gif|svg|webp|avif)$", lowered)
                or (src and lowered == src.lower())):
            results.append(finding("img-alt-generic", IMAGES, "moderate", "warning", "heuristic",
                                   "1.1.1 Non-text Content", location, snippet,
                                   'The alt text "{}" looks like a placeholder or file name rather than a '
                                   'description. Meaning depends on context.'.format(alt[:60]),
                                   "Describe the purpose of the image, or use alt=\"\" if it is decorative."))
    if decorative:
        listed = ", ".join(selector_for(img) for img in decorative[:5])
        if len(decorative) > 5:
            listed += ", and {} more".format(len(decorative) - 5)
        results.append(finding("img-alt-empty-review", IMAGES, "info", "human-review-required",
                               "human-verification-required", "1.1.1 Non-text Content", ctx.loc(decorative[0]),
                               "{} image(s) with alt=\"\": {}".format(len(decorative), listed),
                               "Empty alt is valid for decorative images. Whether these images are decorative "
                               "or convey information cannot be decided statically.",
                               "Confirm each empty-alt image is purely decorative; otherwise add a description."))
    if images and missing == 0:
        results.append(finding("img-alt-attribute-present", IMAGES, "info", "pass", "automatically-verified",
                               "1.1.1 Non-text Content", ctx.loc(images[0]),
                               "{} img element(s), all with an alt attribute or ARIA name".format(len(images)),
                               "Every img carries an alt attribute or an ARIA name. This says nothing about the "
                               "quality of the text.", "Review alt text quality (see human checks)."))
    for control in ctx.soup.find_all("input"):
        if hidden_in_tree(control):
            continue
        if (_attr(control, "type") or "").lower() == "image" and not (_attr(control, "alt") or "").strip():
            source, _ = accessible_name(ctx, control)
            if source is None:
                results.append(finding("input-image-alt-missing", IMAGES, "critical", "fail",
                                       "automatically-verified", "1.1.1 Non-text Content", ctx.loc(control),
                                       opening_tag(control), "An image button has no alt text or other name.",
                                       "Add alt describing the button action."))
    for area in ctx.soup.find_all("area"):
        if hidden_in_tree(area):
            continue
        if _has(area, "href") and not (_attr(area, "alt") or "").strip() and accessible_name(ctx, area)[0] is None:
            results.append(finding("area-alt-missing", IMAGES, "serious", "fail", "automatically-verified",
                                   "1.1.1 Non-text Content", ctx.loc(area), opening_tag(area),
                                   "An image-map area link has no alt text.", "Add alt describing the link target."))
    return results


def check_media(ctx):
    results = []
    for video in ctx.soup.find_all("video"):
        tracks = [t for t in video.find_all("track") if (_attr(t, "kind") or "").lower() in ("captions", "subtitles")]
        if not tracks:
            results.append(finding("video-no-caption-track", IMAGES, "serious", "warning", "heuristic",
                                   "1.2.2 Captions (Prerecorded)", ctx.loc(video), opening_tag(video),
                                   "No caption or subtitle track element is present. Captions may be burned in "
                                   "or provided by the player, which cannot be verified statically.",
                                   'Add <track kind="captions" srclang="he"> (and other languages) or confirm '
                                   'captions exist.'))
    for media in ctx.soup.find_all(["video", "audio"]):
        if _has(media, "autoplay") and not _has(media, "muted") and not _has(media, "controls"):
            results.append(finding("media-autoplay-no-controls", IMAGES, "serious", "warning", "heuristic",
                                   "1.4.2 Audio Control", ctx.loc(media), opening_tag(media),
                                   "Media autoplays without visible controls and is not muted. If it has audio "
                                   "longer than three seconds, users cannot stop it.",
                                   "Add the controls attribute, mute autoplaying media, or avoid autoplay."))
    for frame in ctx.soup.find_all("iframe"):
        if accessible_name(ctx, frame)[0] is None and not (_attr(frame, "title") or "").strip():
            results.append(finding("iframe-title-missing", STRUCTURE, "serious", "warning", "heuristic",
                                   "4.1.2 Name, Role, Value", ctx.loc(frame), opening_tag(frame),
                                   "The iframe has no title or ARIA name, so its purpose is not announced. "
                                   "Hidden or decorative frames may be exempt.",
                                   "Add a title attribute describing the embedded content."))
    return results


def check_forms(ctx):
    results = []
    controls = [c for c in ctx.soup.find_all(["input", "select", "textarea"])
                if (_attr(c, "type") or "").lower() != "hidden" and not hidden_in_tree(c)]
    named = 0
    for control in controls:
        input_type = (_attr(control, "type") or "text").lower() if control.name == "input" else control.name
        source, text = accessible_name(ctx, control)
        location = ctx.loc(control)
        snippet = opening_tag(control)
        if source is not None:
            named += 1
            continue
        if input_type == "image":
            continue  # reported under images
        if (_attr(control, "placeholder") or "").strip():
            results.append(finding("form-control-placeholder-only", FORMS, "serious", "warning", "heuristic",
                                   "3.3.2 Labels or Instructions", location, snippet,
                                   "Only a placeholder names this control. Placeholders disappear on input and "
                                   "are not a reliable label, although some browsers expose them as a fallback name.",
                                   "Add a visible <label> associated with the control (for/id or wrapping)."))
            continue
        detail = " ({})".format(text) if text else ""
        results.append(finding("form-control-no-name", FORMS, "critical", "fail", "automatically-verified",
                               "4.1.2 Name, Role, Value", location, snippet,
                               "The {} control has no associated label: no label[for], wrapping label with text, "
                               "aria-label, resolvable aria-labelledby or title{}. An id alone is not a "
                               "label.".format(input_type, detail),
                               "Add <label for=\"id\">visible text</label> or wrap the control in a label with text."))
    if controls and named == len(controls):
        results.append(finding("form-controls-named", FORMS, "info", "pass", "automatically-verified",
                               "4.1.2 Name, Role, Value", ctx.loc(controls[0]),
                               "{} form control(s) with a resolvable static name".format(len(controls)),
                               "Each control has a label association or ARIA name in the static HTML. Name "
                               "quality, instructions and error handling are not verified.",
                               "Review label wording and Hebrew validation messages manually."))
    for target, labels in ctx.labels_for.items():
        if target not in ctx.ids:
            label = labels[0]
            results.append(finding("label-for-missing-target", FORMS, "moderate", "warning", "heuristic",
                                   "1.3.1 Info and Relationships", ctx.loc(label), opening_tag(label),
                                   'label[for="{}"] references no element in this document; the control may be '
                                   'injected later.'.format(target),
                                   "Point the label at an existing control id."))
        elif len(ctx.ids[target]) > 1:
            results.append(finding("label-for-duplicate-id", FORMS, "moderate", "warning", "automatically-verified",
                                   "1.3.1 Info and Relationships", ctx.loc(labels[0]), opening_tag(labels[0]),
                                   'The id "{}" appears {} times, so the label association is ambiguous.'.format(
                                       target, len(ctx.ids[target])),
                                   "Make ids unique."))
    radios = {}
    for radio in ctx.soup.find_all("input"):
        if (_attr(radio, "type") or "").lower() in ("radio", "checkbox") and _attr(radio, "name"):
            radios.setdefault(_attr(radio, "name"), []).append(radio)
    for name, group in radios.items():
        if len(group) > 1 and not any(r.find_parent("fieldset") or r.find_parent(attrs={"role": ["group", "radiogroup"]})
                                      for r in group):
            results.append(finding("form-group-no-fieldset", FORMS, "moderate", "warning", "heuristic",
                                   "1.3.1 Info and Relationships", ctx.loc(group[0]), opening_tag(group[0]),
                                   '{} related inputs named "{}" have no fieldset/legend or group role, so the '
                                   'group question may not be announced.'.format(len(group), name),
                                   "Wrap the group in <fieldset> with a <legend>, or use role=\"group\" with a name."))
    return results


def check_links_buttons(ctx):
    results = []
    for link in ctx.soup.find_all("a"):
        if not _has(link, "href") or hidden_in_tree(link):
            continue
        location = ctx.loc(link)
        snippet = opening_tag(link) + name_text(link)[:60] + "</a>"
        source, text = accessible_name(ctx, link)
        if source is None:
            results.append(finding("link-no-name", LINKS, "critical", "fail", "automatically-verified",
                                   "2.4.4 Link Purpose (In Context)", location, snippet,
                                   "The link has no text, image alt or ARIA name, so its purpose is unknown.",
                                   "Add link text or an aria-label describing the destination."))
            continue
        normalised = re.sub(r"[\s\.\!\:\>\»\«\…]+", " ", text.lower()).strip()
        if normalised in VAGUE_LINK_TEXT:
            results.append(finding("link-vague-text", LINKS, "moderate", "warning", "heuristic",
                                   "2.4.4 Link Purpose (In Context)", location, snippet,
                                   'The link text "{}" does not describe the destination on its own. Surrounding '
                                   'context may still make the purpose clear.'.format(text[:40]),
                                   "Use link text that states the destination or action."))
        href = (_attr(link, "href") or "").strip()
        if href == "#" or href.lower().startswith("javascript:"):
            results.append(finding("link-placeholder-href", LINKS, "minor", "warning", "heuristic",
                                   "2.4.4 Link Purpose (In Context)", location, snippet,
                                   'href="{}" indicates a script-driven control presented as a link.'.format(href[:30]),
                                   "Use a <button> for actions, or a real destination for links."))
    for button in ctx.soup.find_all("button"):
        if hidden_in_tree(button):
            continue
        source, _ = accessible_name(ctx, button)
        if source is None:
            results.append(finding("button-no-name", LINKS, "critical", "fail", "automatically-verified",
                                   "4.1.2 Name, Role, Value", ctx.loc(button), opening_tag(button),
                                   "The button has no text content, image alt or ARIA name.",
                                   "Add visible text or an aria-label to the button."))
    for element in ctx.soup.find_all(attrs={"role": True}):
        role = (_attr(element, "role") or "").split()
        if not role or role[0] not in ("button", "link", "tab", "menuitem", "checkbox", "switch", "radio"):
            continue
        if element.name in ("button", "a", "input"):
            continue
        source, _ = accessible_name(ctx, element)
        if source is None:
            results.append(finding("role-control-no-name", LINKS, "serious", "warning", "heuristic",
                                   "4.1.2 Name, Role, Value", ctx.loc(element), opening_tag(element),
                                   'The element with role="{}" has no static text or ARIA name; content may be '
                                   'injected at runtime.'.format(role[0]),
                                   "Provide text content or aria-label, or use the native element."))
    return results


def check_headings(ctx):
    results = []
    headings = ctx.soup.find_all(lambda t: re.match(r"^h[1-6]$", t.name or "") or
                                 (_attr(t, "role") or "").split()[:1] == ["heading"])
    previous = None
    for heading in headings:
        if hidden_in_tree(heading):
            continue
        if re.match(r"^h[1-6]$", heading.name):
            level = int(heading.name[1])
        else:
            level_attr = _attr(heading, "aria-level") or "2"
            level = int(level_attr) if level_attr.isdigit() else 2
        source, text = accessible_name(ctx, heading)
        if source is None:
            text = name_text(heading)
        snippet = opening_tag(heading) + text[:60]
        if not text:
            results.append(finding("heading-empty", HEADINGS, "serious", "fail", "automatically-verified",
                                   "1.3.1 Info and Relationships", ctx.loc(heading), snippet,
                                   "The heading has no static text, image alternative or resolvable ARIA name. "
                                   "Runtime content remains untested.",
                                   "Add heading text or remove the empty heading element."))
        if previous is not None and level > previous + 1:
            results.append(finding("heading-level-skipped", HEADINGS, "moderate", "warning", "heuristic",
                                   "1.3.1 Info and Relationships", ctx.loc(heading), snippet,
                                   "The heading level jumps from h{} to h{}. Skipped levels can confuse heading "
                                   "navigation; whether the structure is still meaningful needs review.".format(
                                       previous, level),
                                   "Use consecutive heading levels that reflect the content outline."))
        previous = level
    return results


def check_landmarks(ctx):
    results = []
    if not ctx.is_document or ctx.soup.find("html") is None:
        return results
    body = ctx.soup.find("body") or ctx.soup
    mains = [m for m in ctx.soup.find_all(["main"])] + \
            [m for m in ctx.soup.find_all(attrs={"role": "main"}) if m.name != "main"]
    skip_links = []
    for link in ctx.soup.find_all("a", href=True):
        href = (_attr(link, "href") or "").strip()
        text = name_text(link)
        if href.startswith("#") and len(href) > 1 and SKIP_LINK_RE.search(text or ""):
            skip_links.append((link, href[1:]))
    for link, fragment in skip_links:
        if ctx.target_exists(fragment):
            results.append(finding("skip-link-target-present", STRUCTURE, "info", "pass", "automatically-verified",
                                   "2.4.1 Bypass Blocks", ctx.loc(link), opening_tag(link) + name_text(link)[:60],
                                   'A skip link targets the existing id "{}". Focus transfer and visibility when '
                                   'focused are not verified.'.format(fragment),
                                   "Verify keyboard focus moves to the target."))
        else:
            results.append(finding("skip-link-target-missing", STRUCTURE, "serious", "fail", "automatically-verified",
                                   "2.4.1 Bypass Blocks", ctx.loc(link), opening_tag(link) + name_text(link)[:60],
                                   'The skip link points to "#{}" but no element with that id exists in this '
                                   'document.'.format(fragment),
                                   "Give the main content the referenced id, or fix the href."))
    if not mains and not skip_links:
        results.append(finding("bypass-mechanism-not-found", STRUCTURE, "moderate", "warning", "heuristic",
                               "2.4.1 Bypass Blocks", ctx.loc(body), opening_tag(body),
                               "No main landmark and no skip link were found in the static HTML. Repeated "
                               "blocks may still be bypassable through headings or other landmarks, so this is "
                               "a heuristic indicator, not a failure by itself.",
                               "Add a <main> landmark and, for pages with repeated navigation, a skip link."))
    if len(mains) > 1:
        results.append(finding("main-landmark-multiple", STRUCTURE, "minor", "warning", "heuristic",
                               "1.3.1 Info and Relationships", ctx.loc(mains[1]), opening_tag(mains[1]),
                               "{} main landmarks were found; only one should be visible at a time.".format(len(mains)),
                               "Keep a single main landmark, or hide inactive ones."))
    navs = ctx.soup.find_all("nav") + [n for n in ctx.soup.find_all(attrs={"role": "navigation"}) if n.name != "nav"]
    if len(navs) > 1:
        unnamed = [n for n in navs if accessible_name(ctx, n)[0] is None]
        if unnamed:
            results.append(finding("nav-landmarks-unnamed", STRUCTURE, "minor", "warning", "heuristic",
                                   "1.3.1 Info and Relationships", ctx.loc(unnamed[0]), opening_tag(unnamed[0]),
                                   "{} navigation landmarks exist and {} of them have no name, so they cannot be "
                                   "told apart.".format(len(navs), len(unnamed)),
                                   "Add aria-label (e.g. \"ניווט ראשי\") to each navigation landmark."))
    return results


def check_keyboard(ctx):
    results = []
    for element in ctx.soup.find_all(True):
        click_attrs = [a for a in element.attrs if a.lower() in CLICK_ATTRS]
        if click_attrs:
            native = element.name in NATIVE_INTERACTIVE or (element.name == "a" and _has(element, "href"))
            role = (_attr(element, "role") or "").split()[:1]
            has_key = any(KEY_ATTR_RE.search(a) for a in element.attrs)
            tabindex = _attr(element, "tabindex")
            if not native and not (role and role[0] in INTERACTIVE_ROLES and tabindex is not None and has_key):
                results.append(finding("click-handler-nonsemantic", KEYBOARD, "serious", "warning", "heuristic",
                                       "2.1.1 Keyboard", ctx.loc(element), opening_tag(element),
                                       "A {} element has a click handler ({}) but is not a native control and does "
                                       "not combine an interactive role, tabindex and a keyboard handler. Keyboard "
                                       "support may be added by scripts; this needs rendered verification.".format(
                                           element.name, ", ".join(click_attrs)),
                                       "Use <button> or <a href>, or add role, tabindex=\"0\" and Enter/Space "
                                       "handling."))
        tabindex = _attr(element, "tabindex")
        if tabindex and tabindex.strip().lstrip("-").isdigit() and int(tabindex) > 0:
            results.append(finding("tabindex-positive", KEYBOARD, "moderate", "warning", "heuristic",
                                   "2.4.3 Focus Order", ctx.loc(element), opening_tag(element),
                                   'tabindex="{}" forces a custom tab order that is likely to diverge from the '
                                   'visual order.'.format(tabindex.strip()),
                                   'Use tabindex="0" or "-1" and rely on DOM order.'))
        if _attr(element, "aria-hidden") == "true":
            focusable = (element.name in ("button", "select", "textarea") or (element.name == "a" and _has(element, "href"))
                         or (element.name == "input" and (_attr(element, "type") or "").lower() != "hidden")
                         or (tabindex is not None and tabindex.strip() != "-1"))
            if focusable and not _has(element, "disabled") and (tabindex or "").strip() != "-1":
                results.append(finding("aria-hidden-focusable", KEYBOARD, "serious", "warning", "heuristic",
                                       "4.1.2 Name, Role, Value", ctx.loc(element), opening_tag(element),
                                       'aria-hidden="true" is set on an element that is focusable by default; keyboard '
                                       'users would land on an invisible control unless CSS or scripts hide it.',
                                       "Remove aria-hidden, or remove the control from sequential keyboard focus "
                                       "(tabindex=\"-1\"/disabled). Programmatic focus still needs runtime review."))
    return results


def check_aria(ctx):
    results = []
    attribute_count = 0
    for element in ctx.soup.find_all(True):
        for name in list(element.attrs):
            lowered = name.lower()
            if not lowered.startswith("aria-"):
                continue
            attribute_count += 1
            if lowered in VALID_ARIA_ATTRS:
                continue
            closest = closest_aria_attr(lowered)
            if closest:
                results.append(finding("aria-attribute-misspelled", ARIA, "serious", "fail", "automatically-verified",
                                       "4.1.2 Name, Role, Value", ctx.loc(element), opening_tag(element),
                                       '"{}" is not a WAI-ARIA attribute; it looks like a misspelling of "{}" and '
                                       'is ignored by browsers.'.format(name, closest),
                                       'Rename the attribute to "{}".'.format(closest)))
            else:
                results.append(finding("aria-attribute-unknown", ARIA, "moderate", "warning", "heuristic",
                                       "4.1.2 Name, Role, Value", ctx.loc(element), opening_tag(element),
                                       '"{}" is not in the WAI-ARIA 1.2 attribute list bundled with this tool. It may '
                                       'be newer or vendor-specific; browsers ignore unknown attributes.'.format(name),
                                       "Verify the attribute against the current WAI-ARIA specification."))
        role_value = _attr(element, "role")
        if role_value is not None:
            tokens = role_value.split()
            valid = [t for t in tokens if t.lower() in VALID_ROLES or t.lower().startswith(("doc-", "graphics-"))]
            if tokens and not valid:
                results.append(finding("role-invalid", ARIA, "moderate", "fail", "automatically-verified",
                                       "4.1.2 Name, Role, Value", ctx.loc(element), opening_tag(element),
                                       'role="{}" is not a WAI-ARIA role, so browsers fall back to the native '
                                       'semantics.'.format(role_value),
                                       "Use a valid ARIA role or remove the attribute."))
            elif valid:
                required = REQUIRED_STATES.get(valid[0].lower())
                if required and not _has(element, required) and not (
                        valid[0].lower() == "heading" and re.match(r"^h[1-6]$", element.name)):
                    results.append(finding("role-required-state-missing", ARIA, "moderate", "warning", "heuristic",
                                           "4.1.2 Name, Role, Value", ctx.loc(element), opening_tag(element),
                                           'role="{}" requires {} but the static HTML does not set it; scripts may '
                                           'add it at runtime.'.format(valid[0], required),
                                           "Set {} and keep it updated when the state changes.".format(required)))
        for name in IDREF_ATTRS:
            if _has(element, name):
                _, missing = resolve_idrefs(ctx, _attr(element, name))
                if missing:
                    results.append(finding("aria-idref-missing", ARIA, "moderate", "warning", "heuristic",
                                           "4.1.2 Name, Role, Value", ctx.loc(element), opening_tag(element),
                                           '{} references id(s) {} that do not exist in this document; targets may '
                                           'be injected later.'.format(name, ", ".join(missing)),
                                           "Reference existing element ids."))
    for tag_name in ("html", "body"):
        element = ctx.soup.find(tag_name)
        if element is not None and _attr(element, "aria-hidden") == "true":
            results.append(finding("aria-hidden-document", ARIA, "critical", "fail", "automatically-verified",
                                   "4.1.2 Name, Role, Value", ctx.loc(element), opening_tag(element),
                                   'aria-hidden="true" on {} hides the entire page from assistive technology.'.format(tag_name),
                                   "Remove aria-hidden from the document root."))
    return results


def check_tables(ctx):
    results = []
    tables = ctx.soup.find_all("table")
    checked = 0
    for table in tables:
        role = (_attr(table, "role") or "").lower()
        if role in ("presentation", "none"):
            continue
        cells = [c for c in table.find_all(["th", "td"]) if c.find_parent("table") is table]
        if not cells:
            continue
        checked += 1
        headers = [c for c in cells if c.name == "th" or (_attr(c, "role") or "").lower() in ("columnheader", "rowheader")]
        if headers:
            continue
        results.append(finding("table-no-header-cells", TABLES, "serious", "warning", "heuristic",
                               "1.3.1 Info and Relationships", ctx.loc(table), opening_tag(table),
                               "The table has {} cells but no th or header-role cells. If it presents data, "
                               "screen readers cannot associate values with headers; a purely layout table should "
                               "be marked role=\"presentation\" instead.".format(len(cells)),
                               "Mark header cells with <th scope=\"col|row\"> for data tables, or use "
                               "role=\"presentation\" for layout tables."))
    if checked and not any(item["id"] == "table-no-header-cells" for item in results):
        results.append(finding("tables-have-headers", TABLES, "info", "pass", "automatically-verified",
                               "1.3.1 Info and Relationships", ctx.loc(tables[0]),
                               "{} data table(s) contain header cells".format(checked),
                               "Header cells exist. Their scope and correctness are not verified.",
                               "Check header scope and complex table associations manually."))
    return results


def check_viewport(ctx):
    results = []
    for meta in ctx.soup.find_all("meta"):
        if (_attr(meta, "name") or "").lower() != "viewport":
            continue
        content = (_attr(meta, "content") or "").lower().replace(" ", "")
        blocked = "user-scalable=no" in content or "user-scalable=0" in content
        scale = re.search(r"maximum-scale=([0-9.]+)", content)
        if blocked or (scale and float(scale.group(1)) < 2):
            results.append(finding("viewport-zoom-restricted", ZOOM, "serious", "fail", "automatically-verified",
                                   "1.4.4 Resize Text", ctx.loc(meta), opening_tag(meta),
                                   "The viewport meta tag disables or limits pinch zoom, preventing users from "
                                   "enlarging text on mobile devices.",
                                   "Remove user-scalable=no and any maximum-scale below 2."))
    return results


def check_contrast(ctx):
    results = []
    unknown = []
    for element in ctx.soup.find_all(style=True):
        entry, unresolved = contrast_checker.inline_style_pair(_attr(element, "style") or "")
        if entry:
            results.append(contrast_checker.pair_finding(entry, ctx.loc(element),
                                                         "style attribute on <{}>".format(element.name)))
        elif unresolved:
            unresolved["selector"] = selector_for(element)
            unresolved["line"] = element.sourceline
            unknown.append(unresolved)
    for style in ctx.soup.find_all("style"):
        css_text = style.string if style.string is not None else style.get_text()
        base = (style.sourceline or 1)

        def make_location(line, _style=style, _base=base):
            return ctx.loc(_style, line=_base + line - 1)

        results.extend(contrast_checker.css_findings(css_text or "", make_location,
                                                     "embedded <style> line offset {}".format(base)))
    if unknown:
        results.append(contrast_checker.unknown_background_finding(unknown, ctx.loc(line=unknown[0]["line"]),
                                                                   "inline style attributes"))
    return results


def check_statement(ctx):
    results = []
    if not ctx.is_document or ctx.soup.find("html") is None:
        return results
    candidates = []
    for link in ctx.soup.find_all("a", href=True):
        text = name_text(link)
        href = _attr(link, "href") or ""
        if STATEMENT_RE.search(text) or STATEMENT_RE.search(href):
            candidates.append((link, text or href))
    for heading in ctx.soup.find_all(re.compile(r"^h[1-6]$")):
        if re.search(r"הצהרת\s*נגישות|accessibility\s*statement", name_text(heading), re.IGNORECASE):
            candidates.append((heading, name_text(heading)))
    body = ctx.soup.find("body") or ctx.soup.find("html")
    if candidates:
        element, text = candidates[0]
        results.append(finding("statement-candidate-found", STATEMENT, "info", "human-review-required",
                               "human-verification-required", None, ctx.loc(element),
                               "{} candidate(s); first: {} \"{}\"".format(len(candidates), opening_tag(element), text[:80]),
                               "Text or a link mentioning accessibility was found. This is only an indicator: the "
                               "statement content, its location and whether Israeli requirements apply were not "
                               "verified.",
                               "Have the statement reviewed by a qualified professional; do not treat this as compliance."))
    else:
        results.append(finding("statement-candidate-not-found", STATEMENT, "moderate", "human-review-required",
                               "heuristic", None, ctx.loc(body),
                               "No link, heading or text mentioning נגישות / accessibility statement in the scanned HTML",
                               "No accessibility statement indicator was found on this page. Statements may live on "
                               "other pages or be injected by scripts, and applicability depends on the organisation.",
                               "Check whether an accessibility statement is required and linked from every page."))
    return results


def check_runtime(ctx):
    results = []
    scripts = ctx.soup.find_all("script")
    if not scripts:
        return results
    interactive = len(ctx.soup.find_all(["a", "button", "input", "select", "textarea"]))
    headings = len(ctx.soup.find_all(re.compile(r"^h[1-6]$")))
    if ctx.visible_length < 200 and interactive + headings < 3:
        body = ctx.soup.find("body") or ctx.soup.find("html") or scripts[0]
        results.append(finding("spa-shell-not-rendered", STRUCTURE, "serious", "not-tested", "heuristic", None,
                               ctx.loc(body),
                               "{} script element(s); {} characters of visible text; {} static interactive elements "
                               "and {} headings".format(len(scripts), ctx.visible_length, interactive, headings),
                               "The HTML is an application shell whose content is rendered by JavaScript. This tool "
                               "does not execute scripts, so the rendered page, its dynamic content and interactions "
                               "were not tested at all. Nothing here passed.",
                               "Audit the rendered application in a browser (or server-rendered HTML) with "
                               "keyboard, screen reader and browser tooling."))
    return results


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def scan_html(text, location):
    """Scan static HTML (str or bytes) and return findings; no network access."""
    if not isinstance(location, dict) or not ("file" in location or "url" in location):
        raise ValueError("location must be {'file': str} or {'url': str}")
    soup = BeautifulSoup(text, "html.parser")
    ctx = _Context(soup, location)
    results = []
    for check in (check_language, check_title, check_direction, check_images, check_media, check_forms,
                  check_links_buttons, check_headings, check_landmarks, check_keyboard, check_aria,
                  check_tables, check_viewport, check_contrast, check_statement, check_runtime):
        results.extend(check(ctx))
    return results
