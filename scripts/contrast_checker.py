#!/usr/bin/env python3
"""Static colour contrast helpers (WCAG relative luminance and contrast ratio).

Adapted from ``contrast_checker.py`` in alirezarezvani/claude-skills.
MIT License, Copyright (c) 2025 Alireza Rezvani. See THIRD_PARTY_NOTICES.md.

Retained from upstream: the named colour table (extended), ``parse_color``,
``relative_luminance``, ``contrast_ratio`` and the AA thresholds.
Removed: the CLI, demo, background-suggestion and batch output code, because
``audit.py`` is the only entry point.
Changed: CSS extraction now keeps the last declaration in a block, parses whole
literal values with an anchored colour parser, and refuses alpha, gradients,
variables, images and keywords instead of truncating them to opaque colours.
It reports numeric pairs only; it never claims rendered contrast.
"""

from __future__ import annotations

import re
from collections import OrderedDict

from report import CONTRAST, KEYBOARD, ZOOM, finding

# ---------------------------------------------------------------------------
# Named CSS colours (upstream table plus the remaining CSS Color Level 4 names)
# ---------------------------------------------------------------------------
NAMED_COLORS = {
    "black": (0, 0, 0), "white": (255, 255, 255), "red": (255, 0, 0), "green": (0, 128, 0),
    "blue": (0, 0, 255), "yellow": (255, 255, 0), "cyan": (0, 255, 255), "magenta": (255, 0, 255),
    "gray": (128, 128, 128), "grey": (128, 128, 128), "orange": (255, 165, 0), "purple": (128, 0, 128),
    "pink": (255, 192, 203), "brown": (165, 42, 42), "navy": (0, 0, 128), "teal": (0, 128, 128),
    "olive": (128, 128, 0), "maroon": (128, 0, 0), "lime": (0, 255, 0), "aqua": (0, 255, 255),
    "silver": (192, 192, 192), "gold": (255, 215, 0), "coral": (255, 127, 80), "salmon": (250, 128, 114),
    "tomato": (255, 99, 71),
    # Additional standard names used in real style sheets.
    "aliceblue": (240, 248, 255), "antiquewhite": (250, 235, 215), "aquamarine": (127, 255, 212),
    "azure": (240, 255, 255), "beige": (245, 245, 220), "bisque": (255, 228, 196),
    "blanchedalmond": (255, 235, 205), "blueviolet": (138, 43, 226), "burlywood": (222, 184, 135),
    "cadetblue": (95, 158, 160), "chartreuse": (127, 255, 0), "chocolate": (210, 105, 30),
    "cornflowerblue": (100, 149, 237), "cornsilk": (255, 248, 220), "crimson": (220, 20, 60),
    "darkblue": (0, 0, 139), "darkcyan": (0, 139, 139), "darkgoldenrod": (184, 134, 11),
    "darkgray": (169, 169, 169), "darkgrey": (169, 169, 169), "darkgreen": (0, 100, 0),
    "darkkhaki": (189, 183, 107), "darkmagenta": (139, 0, 139), "darkolivegreen": (85, 107, 47),
    "darkorange": (255, 140, 0), "darkorchid": (153, 50, 204), "darkred": (139, 0, 0),
    "darksalmon": (233, 150, 122), "darkseagreen": (143, 188, 143), "darkslateblue": (72, 61, 139),
    "darkslategray": (47, 79, 79), "darkslategrey": (47, 79, 79), "darkturquoise": (0, 206, 209),
    "darkviolet": (148, 0, 211), "deeppink": (255, 20, 147), "deepskyblue": (0, 191, 255),
    "dimgray": (105, 105, 105), "dimgrey": (105, 105, 105), "dodgerblue": (30, 144, 255),
    "firebrick": (178, 34, 34), "floralwhite": (255, 250, 240), "forestgreen": (34, 139, 34),
    "fuchsia": (255, 0, 255), "gainsboro": (220, 220, 220), "ghostwhite": (248, 248, 255),
    "goldenrod": (218, 165, 32), "greenyellow": (173, 255, 47), "honeydew": (240, 255, 240),
    "hotpink": (255, 105, 180), "indianred": (205, 92, 92), "indigo": (75, 0, 130), "ivory": (255, 255, 240),
    "khaki": (240, 230, 140), "lavender": (230, 230, 250), "lavenderblush": (255, 240, 245),
    "lawngreen": (124, 252, 0), "lemonchiffon": (255, 250, 205), "lightblue": (173, 216, 230),
    "lightcoral": (240, 128, 128), "lightcyan": (224, 255, 255), "lightgoldenrodyellow": (250, 250, 210),
    "lightgray": (211, 211, 211), "lightgrey": (211, 211, 211), "lightgreen": (144, 238, 144),
    "lightpink": (255, 182, 193), "lightsalmon": (255, 160, 122), "lightseagreen": (32, 178, 170),
    "lightskyblue": (135, 206, 250), "lightslategray": (119, 136, 153), "lightslategrey": (119, 136, 153),
    "lightsteelblue": (176, 196, 222), "lightyellow": (255, 255, 224), "limegreen": (50, 205, 50),
    "linen": (250, 240, 230), "mediumaquamarine": (102, 205, 170), "mediumblue": (0, 0, 205),
    "mediumorchid": (186, 85, 211), "mediumpurple": (147, 112, 219), "mediumseagreen": (60, 179, 113),
    "mediumslateblue": (123, 104, 238), "mediumspringgreen": (0, 250, 154), "mediumturquoise": (72, 209, 204),
    "mediumvioletred": (199, 21, 133), "midnightblue": (25, 25, 112), "mintcream": (245, 255, 250),
    "mistyrose": (255, 228, 225), "moccasin": (255, 228, 181), "navajowhite": (255, 222, 173),
    "oldlace": (253, 245, 230), "olivedrab": (107, 142, 35), "orangered": (255, 69, 0), "orchid": (218, 112, 214),
    "palegoldenrod": (238, 232, 170), "palegreen": (152, 251, 152), "paleturquoise": (175, 238, 238),
    "palevioletred": (219, 112, 147), "papayawhip": (255, 239, 213), "peachpuff": (255, 218, 185),
    "peru": (205, 133, 63), "plum": (221, 160, 221), "powderblue": (176, 224, 230),
    "rebeccapurple": (102, 51, 153), "rosybrown": (188, 143, 143), "royalblue": (65, 105, 225),
    "saddlebrown": (139, 69, 19), "sandybrown": (244, 164, 96), "seagreen": (46, 139, 87),
    "seashell": (255, 245, 238), "sienna": (160, 82, 45), "skyblue": (135, 206, 235), "slateblue": (106, 90, 205),
    "slategray": (112, 128, 144), "slategrey": (112, 128, 144), "snow": (255, 250, 250),
    "springgreen": (0, 255, 127), "steelblue": (70, 130, 180), "tan": (210, 180, 140), "thistle": (216, 191, 216),
    "turquoise": (64, 224, 208), "violet": (238, 130, 238), "wheat": (245, 222, 179), "whitesmoke": (245, 245, 245),
    "yellowgreen": (154, 205, 50),
}

# WCAG 2.x AA thresholds (label, required ratio)
AA_NORMAL_TEXT = 4.5
AA_LARGE_TEXT = 3.0
WCAG_THRESHOLDS = [
    ("AA Normal Text", AA_NORMAL_TEXT),
    ("AA Large Text", AA_LARGE_TEXT),
    ("AA UI Components", 3.0),
]

_HEX_RE = re.compile(r"^#([0-9a-f]{3}|[0-9a-f]{6})$")
_HEX_ALPHA_RE = re.compile(r"^#([0-9a-f]{4}|[0-9a-f]{8})$")
_RGB_RE = re.compile(r"^rgba?\(\s*(\d{1,3})\s*[, ]\s*(\d{1,3})\s*[, ]\s*(\d{1,3})\s*(?:[,/]\s*([0-9.]+%?)\s*)?\)$")


# ---------------------------------------------------------------------------
# Colour parsing (strict: the whole literal must be one opaque colour)
# ---------------------------------------------------------------------------
def parse_color(color_str):
    """Parse an opaque colour literal into an (R, G, B) tuple.

    Accepts #RGB, #RRGGBB, rgb(r, g, b), rgba(...) with alpha exactly 1, and
    CSS named colours. Anything else, including translucent colours,
    gradients, var(), url(), currentColor, transparent and inherit, raises
    ValueError, because a static ratio for those would be fabricated.
    """
    s = color_str.strip().lower()
    if s in NAMED_COLORS:
        return NAMED_COLORS[s]
    hex_match = _HEX_RE.match(s)
    if hex_match:
        h = hex_match.group(1)
        if len(h) == 3:
            return (int(h[0] * 2, 16), int(h[1] * 2, 16), int(h[2] * 2, 16))
        return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
    if _HEX_ALPHA_RE.match(s):
        raise ValueError("Colour '{}' carries an alpha channel; the composited result is not known statically"
                         .format(color_str))
    rgb_match = _RGB_RE.match(s)
    if rgb_match:
        r, g, b = (int(rgb_match.group(i)) for i in (1, 2, 3))
        if not all(0 <= c <= 255 for c in (r, g, b)):
            raise ValueError("RGB values must be 0-255, got rgb({},{},{})".format(r, g, b))
        alpha = rgb_match.group(4)
        if alpha is not None:
            value = float(alpha.rstrip("%")) / (100.0 if alpha.endswith("%") else 1.0)
            if value < 1.0:
                raise ValueError("Colour '{}' is translucent; the composited result is not known statically"
                                 .format(color_str))
        return (r, g, b)
    raise ValueError("Colour '{}' is not a plain opaque literal (#RRGGBB, #RGB, rgb(r,g,b) or a named colour)"
                     .format(color_str))


def color_to_hex(rgb):
    return "#{:02x}{:02x}{:02x}".format(rgb[0], rgb[1], rgb[2])


# ---------------------------------------------------------------------------
# WCAG luminance and contrast (unchanged upstream math)
# ---------------------------------------------------------------------------
def relative_luminance(rgb):
    """Relative luminance per WCAG (sRGB). https://www.w3.org/TR/WCAG22/#dfn-relative-luminance"""
    channels = []
    for c in rgb:
        s = c / 255.0
        channels.append(s / 12.92 if s <= 0.04045 else ((s + 0.055) / 1.055) ** 2.4)
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]


def contrast_ratio(rgb1, rgb2):
    """WCAG contrast ratio between two colours (>= 1.0)."""
    l1 = relative_luminance(rgb1)
    l2 = relative_luminance(rgb2)
    lighter = max(l1, l2)
    darker = min(l1, l2)
    return (lighter + 0.05) / (darker + 0.05)


# ---------------------------------------------------------------------------
# Declaration parsing (small, deliberately not a full CSS parser)
# ---------------------------------------------------------------------------
_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
_BLOCK_RE = re.compile(r"([^{}]+)\{([^{}]*)\}", re.DOTALL)
_UNEVALUABLE = ("var(", "url(", "gradient(", "image(", "calc(", "color-mix(", "light-dark(",
                "currentcolor", "transparent", "inherit", "initial", "unset", "revert", "none")


def blank_comments(text):
    """Replace CSS comments with spaces while preserving line numbers."""
    return _COMMENT_RE.sub(lambda m: re.sub(r"[^\n]", " ", m.group(0)), text)


def _split_declarations(body):
    """Split a declaration block on semicolons outside quotes and parentheses."""
    parts, depth, quote, current = [], 0, None, []
    for char in body:
        if quote:
            current.append(char)
            if char == quote:
                quote = None
            continue
        if char in "\"'":
            quote = char
        elif char == "(":
            depth += 1
        elif char == ")":
            depth = max(0, depth - 1)
        elif char == ";" and depth == 0:
            parts.append("".join(current))
            current = []
            continue
        current.append(char)
    parts.append("".join(current))  # final declaration may lack a semicolon
    return parts


def parse_declarations(body):
    """Track declaration order; flag priority syntax for conservative review."""
    declarations = OrderedDict()
    for part in _split_declarations(body):
        if ":" not in part:
            continue
        name, value = part.split(":", 1)
        name = name.strip().lower()
        value = value.strip()
        if re.search(r"!\s*important\s*$", value, re.IGNORECASE):
            declarations["__priority_unresolved__"] = "true"
        value = re.sub(r"!\s*important\s*$", "", value, flags=re.IGNORECASE).strip()
        if name and value:
            declarations[name] = value
            declarations.move_to_end(name)
    return declarations


def solid_color(value):
    """Return (rgb, None) for an opaque literal, else (None, reason)."""
    lowered = value.strip().lower()
    if any(token in lowered for token in _UNEVALUABLE):
        return None, "value '{}' depends on variables, images, gradients or keywords".format(value.strip())
    try:
        return parse_color(lowered), None
    except ValueError as exc:
        return None, str(exc)


def background_value(declarations):
    """Pick the effective background colour declaration, if any."""
    if "background-color" in declarations:
        # A later shorthand overrides an earlier longhand; respect order.
        keys = list(declarations)
        if "background" in declarations and keys.index("background") > keys.index("background-color"):
            return declarations["background"]
        return declarations["background-color"]
    return declarations.get("background")


_FONT_SIZE_RE = re.compile(r"^([0-9]*\.?[0-9]+)(px|pt)$")


def font_size_px(value):
    """Convert absolute px/pt sizes; inherited and relative sizes stay unknown."""
    if not value:
        return None
    match = _FONT_SIZE_RE.match(value.strip().lower())
    if not match:
        return None
    number, unit = float(match.group(1)), match.group(2)
    factor = {"px": 1.0, "pt": 4.0 / 3.0}[unit]
    return number * factor


def is_bold(value):
    if not value:
        return None
    value = value.strip().lower()
    if value == "bold":
        return True
    if value == "normal":
        return False
    return int(value) >= 700 if value.isdigit() else None


def is_large_text(size_px, bold):
    """WCAG large text: >= 24 px, or >= 18.66 px (14 pt) when bold. None if size unknown."""
    if size_px is None:
        return None
    if size_px >= 24.0:
        return True
    if size_px < 14.0 * 4.0 / 3.0:
        return False
    return bold


def evaluate_pair(fg_rgb, bg_rgb, size_px=None, bold=False):
    """Compare a ratio against AA thresholds without assuming the text size."""
    ratio = contrast_ratio(fg_rgb, bg_rgb)
    large = is_large_text(size_px, bold)
    result = {"ratio": round(ratio, 2), "passes_normal": ratio >= AA_NORMAL_TEXT,
              "passes_large": ratio >= AA_LARGE_TEXT, "large_text": large}
    if ratio >= AA_NORMAL_TEXT:
        result["verdict"] = "pass"
    elif ratio < AA_LARGE_TEXT:
        result["verdict"] = "fail"
    elif large is True:
        result["verdict"] = "pass"
    elif large is False:
        result["verdict"] = "fail"
    else:
        result["verdict"] = "warning"  # 3.0-4.5 with unknown text size
    return result


# ---------------------------------------------------------------------------
# CSS analysis
# ---------------------------------------------------------------------------
def _line_at(text, offset):
    return text.count("\n", 0, offset) + 1


def analyze_declarations(declarations):
    """Classify one declaration block. Returns a dict describing the colour pair."""
    fg_value = declarations.get("color")
    bg_value = background_value(declarations)
    info = {"foreground": fg_value, "background": bg_value, "pair": None, "reason": None,
            "size_px": font_size_px(declarations.get("font-size")),
            "bold": is_bold(declarations.get("font-weight"))}
    if not fg_value:
        return info
    if "__priority_unresolved__" in declarations:
        info["reason"] = "!important precedence is not resolved by this static extractor"
        return info
    if declarations.get("background-image", "none").strip().lower() != "none":
        info["reason"] = "a background image or gradient requires rendered review"
        return info
    if not bg_value:
        info["reason"] = "no background declared in the same block; the effective background is unknown"
        return info
    fg_rgb, fg_reason = solid_color(fg_value)
    bg_rgb, bg_reason = solid_color(bg_value)
    if fg_rgb is None or bg_rgb is None:
        info["reason"] = fg_reason or bg_reason
        return info
    if "opacity" in declarations and declarations["opacity"].strip() not in ("1", "1.0", "100%"):
        info["reason"] = "opacity {} changes the rendered colours".format(declarations["opacity"])
        return info
    info["pair"] = (fg_rgb, bg_rgb)
    return info


def analyze_css(css_text):
    """Extract colour pairs and focus/motion clues from a style sheet string.

    Returns {"pairs": [...], "unknown_background": [...], "focus_outline_removed": [...],
             "animated_lines": [...], "reduced_motion_query": bool}.
    """
    text = blank_comments(css_text)
    result = {"pairs": [], "unknown_background": [], "focus_outline_removed": [],
              "animated_lines": [], "reduced_motion_query": "prefers-reduced-motion" in text.lower()}
    for match in _BLOCK_RE.finditer(text):
        selector = " ".join(match.group(1).split())
        if not selector or selector.startswith("@"):
            continue
        body = match.group(2)
        line = _line_at(text, match.start(2))
        declarations = parse_declarations(body)
        info = analyze_declarations(declarations)
        entry = {"selector": selector, "line": line, "foreground": info["foreground"],
                 "background": info["background"], "size_px": info["size_px"], "bold": info["bold"]}
        if info["pair"]:
            entry["pair"] = info["pair"]
            result["pairs"].append(entry)
        elif info["foreground"]:
            entry["reason"] = info["reason"]
            result["unknown_background"].append(entry)
        if ":focus" in selector.lower():
            outline = declarations.get("outline", declarations.get("outline-style", ""))
            if outline.strip().lower() in ("none", "0", "0px") and not any(
                    key in declarations for key in ("box-shadow", "border", "border-color", "background-color",
                                                    "background", "text-decoration", "outline-color")):
                result["focus_outline_removed"].append({"selector": selector, "line": line, "value": outline})
        if any(key in declarations for key in ("animation", "animation-name", "transition")):
            result["animated_lines"].append({"selector": selector, "line": line})
    return result


# ---------------------------------------------------------------------------
# Finding builders shared by the HTML and source scanners
# ---------------------------------------------------------------------------
def pair_finding(entry, location, context):
    """Turn an evaluable colour pair into a contrast finding."""
    fg_rgb, bg_rgb = entry["pair"]
    result = evaluate_pair(fg_rgb, bg_rgb, entry.get("size_px"), entry.get("bold", False))
    ratio = result["ratio"]
    evidence = "{}: color {} on background {} -> computed ratio {}:1".format(
        context, entry["foreground"], entry["background"], ratio)
    if result["large_text"] is None:
        size_note = ("Text size is unknown, so both AA thresholds are shown: normal text needs 4.5:1, "
                     "large text (24 px, or 18.66 px bold) needs 3:1.")
    elif result["large_text"]:
        size_note = "Declared size {} px qualifies as large text (3:1 threshold).".format(entry["size_px"])
    else:
        size_note = "Declared size {} px is normal text (4.5:1 threshold).".format(entry["size_px"])
    rendered = ("The ratio is an exact calculation for these two literals only; whether this pair is what "
                "users actually see depends on cascade, inheritance and images, which were not rendered.")
    if result["verdict"] == "pass":
        return finding("contrast-pair-ratio", CONTRAST, "info", "pass", "automatically-verified",
                       "1.4.3 Contrast (Minimum)", location, evidence,
                       "The declared pair meets the applicable AA ratio. " + size_note + " " + rendered,
                       "No change needed for this pair; confirm rendered contrast on the page.")
    if result["verdict"] == "warning":
        return finding("contrast-pair-size-unknown", CONTRAST, "moderate", "warning", "heuristic",
                       "1.4.3 Contrast (Minimum)", location, evidence,
                       "The ratio is between 3:1 and 4.5:1. It passes only if the text is large. " + size_note
                       + " " + rendered,
                       "Increase contrast to at least 4.5:1 unless the affected text is verified as large.")
    return finding("contrast-pair-insufficient", CONTRAST, "serious", "fail", "automatically-verified",
                   "1.4.3 Contrast (Minimum)", location, evidence,
                   "The declared pair is below the AA ratio. " + size_note + " " + rendered,
                   "Adjust the foreground or background colour so text reaches 4.5:1 (3:1 for large text).")


def unknown_background_finding(entries, location, context):
    """One not-tested finding summarising colour declarations without a known background."""
    listed = "; ".join("{} (line {}): color {} - {}".format(e["selector"], e["line"], e["foreground"],
                                                            e.get("reason", "unknown background"))
                       for e in entries[:6])
    if len(entries) > 6:
        listed += "; and {} more".format(len(entries) - 6)
    return finding("contrast-background-unknown", CONTRAST, "info", "not-tested", "heuristic",
                   "1.4.3 Contrast (Minimum)", location, "{}: {}".format(context, listed),
                   "These colour declarations have no opaque background in the same rule, or use transparency, "
                   "gradients, images or variables, so no ratio can be computed statically.",
                   "Measure the rendered contrast in the browser for these elements.")


def focus_outline_finding(entry, location):
    return finding("focus-outline-removed", KEYBOARD, "serious", "warning", "heuristic",
                   "2.4.7 Focus Visible", location,
                   "{} (line {}): outline: {}".format(entry["selector"], entry["line"], entry["value"]),
                   "A focus rule removes the outline without declaring an alternative indicator in the same "
                   "rule. Other rules or scripts may still provide one; confirm on the rendered page.",
                   "Keep a visible focus indicator (outline or an equivalent box-shadow/border change) for "
                   "every focusable element.")


def motion_finding(entries, location, context):
    listed = ", ".join("{} (line {})".format(e["selector"], e["line"]) for e in entries[:6])
    return finding("motion-no-reduced-motion-query", ZOOM, "minor", "warning", "heuristic",
                   "2.3.3 Animation from Interactions (AAA, advisory)", location,
                   "{}: animation/transition declared in {} without any prefers-reduced-motion query".format(
                       context, listed),
                   "Animations are declared but the style sheet never checks the reduced-motion preference. "
                   "Whether the motion is essential or disorienting requires rendered review. This AAA "
                   "recommendation is not an automatic failure of the AA engineering target.",
                   "Wrap non-essential animation in @media (prefers-reduced-motion: no-preference) or disable it "
                   "under (prefers-reduced-motion: reduce).")


def css_findings(css_text, make_location, context):
    """Full set of findings for one style sheet; ``make_location(line)`` builds the location."""
    analysis = analyze_css(css_text)
    results = []
    for entry in analysis["pairs"]:
        results.append(pair_finding(entry, make_location(entry["line"]), "{} {}".format(context, entry["selector"])))
    if analysis["unknown_background"]:
        first = analysis["unknown_background"][0]["line"]
        results.append(unknown_background_finding(analysis["unknown_background"], make_location(first), context))
    for entry in analysis["focus_outline_removed"]:
        results.append(focus_outline_finding(entry, make_location(entry["line"])))
    if analysis["animated_lines"] and not analysis["reduced_motion_query"]:
        results.append(motion_finding(analysis["animated_lines"],
                                      make_location(analysis["animated_lines"][0]["line"]), context))
    return results


def inline_style_pair(style_value):
    """Analyse one style attribute value; returns (entry or None, unknown_entry or None)."""
    declarations = parse_declarations(style_value)
    info = analyze_declarations(declarations)
    entry = {"selector": "style attribute", "line": None, "foreground": info["foreground"],
             "background": info["background"], "size_px": info["size_px"], "bold": info["bold"]}
    if info["pair"]:
        entry["pair"] = info["pair"]
        return entry, None
    if info["foreground"]:
        entry["reason"] = info["reason"]
        return None, entry
    return None, None
