#!/usr/bin/env python3
"""Standalone Hebrew RTL HTML report renderer.

``render_html(report)`` returns one self-contained HTML document (no external
assets) for the report dictionary produced by ``report.build_report``. The page
groups repeating findings by ``rule_id``, keeps every location, and offers
native controls to select findings and export a repair packet through
``window.RepairPack.build(report, selectedIds)`` supplied by
``repair_packet.browser_script()``.

This module uses only the Python standard library.
"""

from __future__ import annotations

import html
import json
from collections import OrderedDict

try:
    from repair_packet import browser_script
except ImportError:  # The packet module may not exist yet; the page degrades.
    browser_script = None

__all__ = ["render_html"]

TITLE = "Next Impact — בדיקת נגישות ותיקונים"

# Display groups. Every finding lands in exactly one of the three action
# groups, except "pass" which is shown separately and quietly.
GROUP_VERIFIED = "verified"
GROUP_HEURISTIC = "heuristic"
GROUP_HUMAN = "human"
GROUP_PASS = "pass"
GROUP_INCOMPLETE = "incomplete"
GROUP_UNTESTED = "untested"
GROUP_BEST_PRACTICE = "best-practice"
GROUP_REVIEW = "review"

_STATUS_HE = {
    "fail": "נכשל",
    "warning": "אזהרה",
    "not-tested": "לא נבדק",
    "human-review-required": "נדרשת בדיקה אנושית",
    "pass": "עבר",
}
_SEVERITY_HE = {
    "critical": "קריטי",
    "serious": "חמור",
    "moderate": "בינוני",
    "minor": "קל",
    "info": "מידע",
}
_EVIDENCE_TYPE_HE = {
    "automatically-verified": "אומת אוטומטית",
    "heuristic": "היוריסטי",
    "human-verification-required": "נדרש אימות אנושי",
}
_RUN_STATUS_HE = {
    "completed": "הושלמה",
    "partial": "חלקית",
    "not-performed": "לא בוצעה",
}


def _esc(value):
    """Escape untrusted text for HTML content and attribute values."""
    return html.escape(str(value if value is not None else ""), quote=True)


def _json_embed(value):
    """Serialize ``value`` so it is safe inside a script element.

    ``<``, ``>`` and ``&`` are replaced with JSON ``\\uXXXX`` escapes so the
    markup can never contain ``</script`` or ``<!--``; U+2028/U+2029 are
    escaped because they are line terminators in JavaScript source.
    """
    text = json.dumps(value, ensure_ascii=False)
    for raw, escaped in (("&", "\\u0026"), ("<", "\\u003c"), (">", "\\u003e"),
                         ("\u2028", "\\u2028"), ("\u2029", "\\u2029")):
        text = text.replace(raw, escaped)
    return text


def _identity(item):
    """Selection identity: stable_id when the root added one, else id."""
    return str(item.get("stable_id") or item.get("id") or "")


def _rule_key(item):
    """Grouping key: original rule_id when present, else the finding id."""
    return str(item.get("rule_id") or item.get("id") or "unknown-rule")


def _classify(item):
    """Map one finding onto its display group."""
    status = item.get("status")
    if status == "pass":
        return GROUP_PASS
    if status == "not-tested":
        return GROUP_UNTESTED
    if item.get("engine") == "axe" and status == "human-review-required":
        return GROUP_INCOMPLETE
    if item.get("standards_basis") == "best-practice" and status == "warning":
        return GROUP_BEST_PRACTICE
    if status == "human-review-required" and not _rule_key(item).startswith("human-"):
        return GROUP_REVIEW
    if status == "fail" and item.get("evidence_type") == "automatically-verified":
        return GROUP_VERIFIED
    if status in ("fail", "warning"):
        return GROUP_HEURISTIC
    # not-tested, human-review-required and anything unexpected stay visible.
    return GROUP_HUMAN


def _locations(item):
    """Every location of one finding: ``location`` plus the optional list."""
    seen = []
    result = []
    candidates = []
    if isinstance(item.get("location"), dict):
        candidates.append(item["location"])
    for entry in item.get("locations") or []:
        if isinstance(entry, dict):
            candidates.append(entry)
    for entry in candidates:
        key = json.dumps(entry, sort_keys=True, ensure_ascii=False, default=str)
        if key not in seen:
            seen.append(key)
            result.append(entry)
    return result


def _location_html(location):
    """One location as inline HTML; technical values stay LTR and isolated."""
    parts = []
    if location.get("url"):
        parts.append('כתובת <code dir="ltr">{}</code>'.format(_esc(location["url"])))
    if location.get("file"):
        parts.append('קובץ <code dir="ltr">{}</code>'.format(_esc(location["file"])))
    if location.get("line"):
        parts.append("שורה {}".format(_esc(location["line"])))
    if location.get("selector"):
        parts.append('בורר <code dir="ltr">{}</code>'.format(_esc(location["selector"])))
    for key in sorted(location):
        if key not in ("url", "file", "line", "selector") and location[key] not in (None, ""):
            parts.append('<code dir="ltr">{}</code>: <code dir="ltr">{}</code>'.format(
                _esc(key), _esc(location[key])))
    return ", ".join(parts) if parts else "מיקום לא ידוע"


def _badge(css, text):
    return '<span class="badge {}">{}</span>'.format(css, _esc(text))


def _status_badges(item):
    """Status, evidence-type, severity and engine badges for one finding."""
    status = str(item.get("status") or "")
    badges = [_badge("badge-" + _classify(item),
                     _STATUS_HE.get(status, status or "לא ידוע"))]
    evidence_type = str(item.get("evidence_type") or "")
    if evidence_type:
        badges.append(_badge("badge-plain", _EVIDENCE_TYPE_HE.get(evidence_type, evidence_type)))
    severity = str(item.get("severity") or "")
    if severity:
        badges.append(_badge("badge-plain", "חומרה: " + _SEVERITY_HE.get(severity, severity)))
    engine = str(item.get("engine") or "")
    if engine:
        badges.append(_badge("badge-plain", "מנוע: " + engine))
    return " ".join(badges)


def _occurrence_html(item, group, group_explanation, group_remediation):
    """One finding inside a rule card: checkbox, badges, locations, evidence."""
    identity = _identity(item)
    lines = ['<li class="occurrence">']
    if group not in (GROUP_PASS, GROUP_UNTESTED) and identity:
        lines.append(
            '<label class="pick"><input type="checkbox" class="finding-select" '
            'value="{}" data-group="{}"> כללו בבקשה לסוכן: <code dir="ltr">{}</code></label>'.format(
                _esc(identity), _esc(group), _esc(item.get("id") or identity)))
    else:
        lines.append('<p class="pick"><code dir="ltr">{}</code></p>'.format(
            _esc(item.get("id") or identity or "ללא מזהה")))
    lines.append('<p class="badges">{}</p>'.format(_status_badges(item)))
    if item.get("wcag_criterion"):
        lines.append('<p class="meta">WCAG: <span lang="en" dir="ltr">{}</span></p>'.format(
            _esc(item["wcag_criterion"])))
    if item.get("standards_basis") == "best-practice":
        lines.append('<p class="meta">שיטת עבודה מומלצת — אינה כשל WCAG מאומת.</p>')
    locations = _locations(item)
    if len(locations) == 1:
        lines.append('<p class="meta">מיקום: {}</p>'.format(_location_html(locations[0])))
    elif locations:
        lines.append('<p class="meta">מיקומים ({}):</p><ul class="locations">{}</ul>'.format(
            len(locations),
            "".join("<li>{}</li>".format(_location_html(entry)) for entry in locations)))
    # Repeat prose only when it differs from the rule-level text.
    explanation = str(item.get("explanation") or "")
    if explanation and explanation != group_explanation:
        lines.append('<p class="prose" lang="en" dir="auto">{}</p>'.format(_esc(explanation)))
    remediation = str(item.get("remediation") or "")
    if remediation and remediation != group_remediation:
        lines.append('<p class="prose" lang="en" dir="auto">תיקון: <span lang="en">{}</span></p>'.format(
            _esc(remediation)))
    if item.get("evidence"):
        lines.append('<details><summary>ראיה טכנית</summary><pre dir="ltr" class="evidence"><code>{}</code></pre></details>'.format(
            _esc(item["evidence"])))
    lines.append("</li>")
    return "\n".join(lines)


def _rule_card_html(rule_key, items, group):
    """One card per rule_id holding every occurrence of that rule."""
    first = items[0]
    explanation = str(first.get("explanation") or "")
    remediation = str(first.get("remediation") or "")
    count_note = ' <span class="count">({} מופעים)</span>'.format(len(items)) if len(items) > 1 else ""
    lines = ['<article class="rule rule-{}">'.format(_esc(group))]
    from hebrew import finding_copy
    copy = finding_copy(first)
    lines.append('<h3>{}{}</h3>'.format(_esc(copy['title']), count_note))
    lines.append('<p class="prose"><strong>השפעה על המשתמש:</strong> {}</p>'.format(_esc(copy['impact'])))
    lines.append('<p class="prose"><strong>מה מוצע לעשות:</strong> {}</p>'.format(_esc(copy['action'])))
    lines.append('<details><summary>הסבר טכני של הכלל</summary>')
    lines.append('<p><code dir="ltr">{}</code></p>'.format(_esc(rule_key)))
    if first.get("category"):
        lines.append('<p class="meta">קטגוריה: <span lang="en" dir="ltr">{}</span></p>'.format(_esc(first["category"])))
    if explanation:
        lines.append('<p class="prose" lang="en" dir="ltr">{}</p>'.format(_esc(explanation)))
    if remediation:
        lines.append('<p class="prose" lang="en" dir="ltr">{}</p>'.format(_esc(remediation)))
    lines.append('</details><details class="occurrence-details"><summary>בחירת מופעים ומיקומים ({})</summary><ol class="occurrences">'.format(len(items)))
    for item in items:
        lines.append(_occurrence_html(item, group, explanation, remediation))
    lines.append("</ol></details></article>")
    return "\n".join(lines)


def _group_section_html(section_id, heading, description, grouped, group, empty_text):
    total = sum(len(items) for items in grouped.values())
    lines = ['<section id="group-{}" aria-labelledby="{}-h">'.format(_esc(group), section_id)]
    lines.append('<h2 id="{}-h">{} ({})</h2>'.format(section_id, _esc(heading), total))
    lines.append('<p class="section-note">{}</p>'.format(_esc(description)))
    if not grouped:
        lines.append('<p class="empty">{}</p>'.format(_esc(empty_text)))
    for rule_key, items in grouped.items():
        lines.append(_rule_card_html(rule_key, items, group))
    lines.append("</section>")
    return "\n".join(lines)


def _pass_section_html(grouped):
    total = sum(len(items) for items in grouped.values())
    lines = ['<section id="group-pass" aria-labelledby="pass-h">']
    lines.append('<h2 id="pass-h">בדיקות שעברו ({})</h2>'.format(total))
    lines.append('<p class="section-note">כל מעבר מעיד רק על העובדה הצרה שנבדקה; אין בכך עמידה בתקן.</p>')
    if not grouped:
        lines.append('<p class="empty">לא נרשמו בדיקות שעברו.</p></section>')
        return "\n".join(lines)
    lines.append("<details><summary>הצגת הבדיקות שעברו</summary>")
    for rule_key, items in grouped.items():
        lines.append(_rule_card_html(rule_key, items, GROUP_PASS))
    lines.append("</details></section>")
    return "\n".join(lines)


def _run_metadata_html(report):
    scope = report.get("scope") or {}
    run = (report.get("metadata") or {}).get("run") or {}
    rows = []

    def row(term, value_html):
        rows.append("<div><dt>{}</dt><dd>{}</dd></div>".format(term, value_html))

    target = scope.get("target")
    if target:
        row("יעד הבדיקה", '<code dir="ltr">{}</code>'.format(_esc(target)))
    status = run.get("status")
    if status:
        row("מצב ההרצה", _esc(_RUN_STATUS_HE.get(status, status)))
    elif scope.get("mode") == "path" or not scope.get("rendered"):
        row("מצב ההרצה", "חלקית (ניתוח סטטי בלבד)")
    original = run.get("original_url")
    final = run.get("final_url")
    if original and final and original != final:
        row("כתובת סופית", '<code dir="ltr">{}</code>'.format(_esc(final)))
    rendered = run.get("rendered", scope.get("rendered"))
    row("עיבוד בדפדפן (הרצת JavaScript)", "כן" if rendered else "לא")
    engines = run.get("engines") or {}
    if engines:
        row("מנועי בדיקה", ", ".join(
            '<code dir="ltr">{}</code>'.format(_esc(name)) for name in (str(key) + " " + str(value) for key, value in engines.items())))
    if run.get("started_at"):
        row("זמן הבדיקה", '<time dir="ltr">{}</time>'.format(_esc(run["started_at"])))
    if run.get("states"):
        row("מצבים שנבדקו", _esc(", ".join(run["states"])))
    if run.get("pages"):
        row("עמודים שנבדקו", _esc(len(run["pages"])))
    if run.get("untested"):
        row("תחומים שלא נכללו בסריקה", _esc(", ".join(str(item) for item in run["untested"])))
    return '<dl class="run-meta">{}</dl>'.format("".join(rows))


def _summary_html(report):
    summary = report.get("summary") or {}
    findings = report.get("findings") or []
    totals = {group: sum(_classify(item) == group for item in findings) for group in
              (GROUP_VERIFIED, GROUP_INCOMPLETE, GROUP_HUMAN, GROUP_BEST_PRACTICE, GROUP_HEURISTIC, GROUP_REVIEW)}
    chips = []
    for group, label in ((GROUP_VERIFIED, "מופעי כשל אוטומטי"),
                         (GROUP_INCOMPLETE, "מופעים שהמנוע לא הכריע בהם"),
                         (GROUP_HUMAN, "משימות בדיקה כלליות"),
                         (GROUP_REVIEW, "פריטים נקודתיים לבדיקה אנושית"),
                         (GROUP_BEST_PRACTICE, "המלצות לשיפור"),
                         (GROUP_HEURISTIC, "חשדות לאימות")):
        chips.append(_badge("badge-plain", "{}: {}".format(label, totals[group])))
    chips.append(_badge("badge-plain", "שגיאות תפעוליות: {}".format(
        summary.get("operational_errors", 0))))
    run = report.get("metadata", {}).get("run", {})
    state = run.get("status", "partial")
    next_step = ("הבדיקה האוטומטית הושלמה לעמוד ולמצב המתועדים. בחרו ממצאים לאימות והמשיכו לבדיקות האנושיות."
                 if state == "completed" else "זו בדיקה חלקית. אמתו את החשדות בדפדפן לפני שינוי קוד."
                 if state == "partial" else "העמוד המבוקש לא נבדק. פתרו את סיבת העצירה והריצו שוב; אין כאן תוצאת נגישות של האתר.")
    untested = run.get("untested") or []
    scope_text = ("תחומים שלא נכללו בסריקה: " + "; ".join(str(item) for item in untested)
                  if untested else "תחומים שלא נכללו בסריקה: לא תועדו; אין להסיק מכך שהכול נבדק.")
    if state == "not-performed":
        readiness = run.get("readiness") or {}
        reason = readiness.get("reason") if isinstance(readiness, dict) else None
        reason = reason or "; ".join(str(error) for error in report.get("errors") or []) or "העמוד לא היה זמין לבדיקה תקפה."
        next_step += " סיבה: " + str(reason)
        if isinstance(readiness, dict) and readiness.get("next_step"):
            next_step += " הצעד הבא: " + str(readiness["next_step"])
        chips = []
    return ('<section aria-labelledby="summary-h"><h2 id="summary-h">תמצית</h2>'
            '<p>{}</p><p class="badges">{}</p><p>{}</p>'
            '<p>הספירות מתארות סוגי תוצאות שונים; הן אינן ציון נגישות.</p>'
            '<p class="section-note">היעדר ממצאים אוטומטיים לעולם אינו מהווה '
            'עמידה בתקן או בדין; רמת ההתאמה אינה נקבעת על ידי הכלי.</p>'
            "</section>").format(_esc(next_step), " ".join(chips), _esc(scope_text))


def _limitations_html(report):
    lines = ['<section aria-labelledby="limits-h"><h2 id="limits-h">מגבלות הבדיקה</h2>']
    limitations = report.get("limitations") or []
    if limitations:
        lines.append('<ul class="prose-list">')
        for item in limitations:
            lines.append('<li lang="en" dir="ltr">{}</li>'.format(_esc(item)))
        lines.append("</ul>")
    errors = report.get("errors") or []
    lines.append("<h2>שגיאות תפעוליות ({})</h2>".format(len(errors)))
    if errors:
        lines.append('<ul class="prose-list">')
        for item in errors:
            lines.append('<li dir="auto">{}</li>'.format(_esc(item)))
        lines.append("</ul>")
    else:
        lines.append("<p>לא נרשמו שגיאות תפעוליות.</p>")
    lines.append("</section>")
    return "\n".join(lines)


def _comparison_html(report):
    comparison = report.get('comparison')
    if not comparison:
        return ''
    labels = {'fixed_verified':'תוקן ואומת בבדיקה החוזרת', 'changed_unverified':'נעשה שינוי אך האימות לא הושלם',
              'remaining':'הממצא נותר', 'new':'ממצא חדש', 'not_tested':'לא ניתן לאמת'}
    return '<section><h2>השוואה לבדיקה הקודמת</h2><ul>' + ''.join(
        '<li>{}: {}</li>'.format(label, len(comparison.get(key, []))) for key,label in labels.items()) + '</ul><p>היעלמות רכיב או ירידה בכיסוי אינן הוכחה לתיקון. פרטי האימות נשמרים גם ב־JSON.</p></section>'


def _toolbar_html(not_performed=False):
    if not_performed:
        return '<section><h2>מה עושים עכשיו?</h2><p>העמוד המבוקש לא נבדק. אין ממצאי אתר תקפים לבחירה ואין חבילת תיקון. הסדירו גישה מורשית לעמוד או בדקו עותק מקומי, ואז הריצו שוב.</p></section>'
    return """<section aria-labelledby="actions-h">
<h2 id="actions-h">מה עושים עכשיו?</h2>
<ol class="next-steps">
<li><strong>בוחרים נושא לבדיקה או לתיקון</strong><span>פתחו את המופעים בכרטיס המתאים וסמנו מה לכלול. חשד נשלח לאימות לפני שינוי.</span></li>
<li><strong>מעבירים לסוכן שלכם</strong><span>העתיקו את הבקשה לשיחה ב־Codex או Claude Code, או צרפו את הקובץ שהורדתם.</span></li>
<li><strong>מקבלים תיקון ובדיקה חוזרת</strong><span>פתחו את פרויקט האתר אצל הסוכן ובקשו: ״טפל רק בממצאים שבחבילה, אמת לפני שינוי ובדוק שוב אחריו. אל תפרוס את האתר.״</span></li>
</ol>
<p class="handoff-note"><strong>הלחיצה מכינה הוראות בלבד.</strong> היא לא מפעילה סוכן, לא משנה קוד ולא שולחת מידע. תיקון בפועל מתחיל אצל הסוכן עם גישה לפרויקט ובקשה שלכם.</p>
<p>באתר WordPress או Elementor דרושה גישה למערכת שבה מנוהלים התוכן והתבניות. כתובת URL לבדה אינה מאפשרת תיקון.</p>
<div class="toolbar" role="group" aria-label="פעולות על הממצאים שנבחרו">
<button type="button" id="select-verified">בחירת כל הליקויים המאומתים</button>
<button type="button" id="copy-selected">העתקת בקשה ל־Codex / Claude</button>
<button type="button" id="download-selected">הורדת קובץ לצירוף לסוכן</button>
<button type="button" id="clear-selected">ניקוי הבחירה</button>
<span id="selected-count">נבחרו 0 ממצאים</span>
</div>
<p id="packet-status" role="status"></p>
<p id="packet-next" class="handoff-note" hidden>השלב הבא: פתחו את פרויקט האתר אצל סוכן הקוד, הדביקו את הבקשה או צרפו את repair-packet.md ובקשו לבצע את ההוראות. אפשר להעביר את הקובץ גם למי שמתחזק את האתר. כתובת אתר לבדה אינה מאפשרת לערוך אותו.</p>
<div id="packet-fallback" hidden>
<label for="packet-text">חבילת התיקון (Markdown) — אם ההעתקה האוטומטית נכשלה, העתיקו מכאן ידנית:</label>
<textarea id="packet-text" rows="12" readonly spellcheck="false" dir="auto"></textarea>
</div>
</section>"""


_CSS = """
:root { color-scheme: light; }
* { box-sizing: border-box; }
body {
  margin: 0; background: #f6f6f4; color: #1a1a1a;
  font-family: system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
  line-height: 1.6;
}
header.page, main, footer.page { max-width: 62rem; margin-inline: auto; padding: 1rem; }
header.page { border-block-end: 4px solid #1d4ed8; background: #ffffff; }
header.page h1 { margin: 0 0 0.5rem; font-size: 1.6rem; }
main > section { background: #ffffff; border: 1px solid #d9d9d4; border-radius: 0.5rem;
  padding: 1rem; margin-block: 1rem; }
h2 { font-size: 1.25rem; margin-block: 0 0.5rem; }
h3 { font-size: 1.05rem; margin-block: 0 0.25rem; overflow-wrap: anywhere; }
.section-note, .meta { color: #444444; margin-block: 0.25rem; }
.empty { color: #444444; font-style: normal; }
.rule { border: 1px solid #d9d9d4; border-inline-start: 6px solid #6b7280;
  border-radius: 0.375rem; padding: 0.75rem 1rem; margin-block: 0.75rem; }
.rule-verified { border-inline-start-color: #b91c1c; }
.rule-heuristic { border-inline-start-color: #c2620a; }
.rule-human { border-inline-start-color: #1d4ed8; }
.rule-pass { border-inline-start-color: #15803d; }
.occurrences { margin: 0.5rem 0 0; padding-inline-start: 1.25rem; }
.occurrence { margin-block: 0.75rem; border-block-start: 1px solid #ececea; padding-block-start: 0.5rem; }
.occurrence:first-child { border-block-start: none; padding-block-start: 0; }
.pick { display: inline-flex; align-items: center; gap: 0.5rem; font-weight: 600;
  padding: 0.25rem; margin: 0; }
.pick input[type="checkbox"] { inline-size: 1.25rem; block-size: 1.25rem; accent-color: #1d4ed8; }
.badges { margin-block: 0.25rem; }
.badge { display: inline-block; border-radius: 0.25rem; padding: 0.1rem 0.5rem;
  font-size: 0.85rem; margin-inline-end: 0.25rem; background: #e4e4e7; color: #27272a; }
.badge-verified { background: #fde8e8; color: #7f1d1d; }
.badge-heuristic { background: #ffedd5; color: #7c2d12; }
.badge-human { background: #dbeafe; color: #1e3a8a; }
.badge-pass { background: #dcfce7; color: #14532d; }
.evidence { background: #f4f4f2; border: 1px solid #d9d9d4; border-radius: 0.25rem;
  padding: 0.5rem; overflow-x: auto; max-inline-size: 100%; }
code { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 0.9em; overflow-wrap: anywhere; }
.prose, .prose-list li { overflow-wrap: anywhere; }
.locations { margin-block: 0.25rem; }
.run-meta { margin: 0; }
.run-meta div { display: flex; flex-wrap: wrap; gap: 0.5rem; padding-block: 0.15rem; }
.run-meta dt { font-weight: 600; }
.run-meta dt::after { content: ":"; }
.run-meta dd { margin: 0; overflow-wrap: anywhere; }
.toolbar { display: flex; flex-wrap: wrap; align-items: center; gap: 0.75rem; }
button {
  font: inherit; background: #1d4ed8; color: #ffffff; border: 2px solid #1d4ed8;
  border-radius: 0.375rem; padding: 0.5rem 1rem; min-block-size: 2.75rem; cursor: pointer;
}
button:hover { background: #1e40af; border-color: #1e40af; }
#selected-count { font-weight: 600; }
#packet-status { min-block-size: 1.5rem; font-weight: 600; color: #14532d; margin-block: 0.5rem; }
#packet-status.error { color: #7f1d1d; }
#packet-fallback label { display: block; font-weight: 600; margin-block-end: 0.25rem; }
#packet-text { inline-size: 100%; font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }
details > summary { cursor: pointer; font-weight: 600; padding: 0.25rem; }
:focus { outline: 3px solid #1d4ed8; outline-offset: 2px; }
footer.page { color: #444444; }
.next-steps { display: grid; grid-template-columns: repeat(3, 1fr); gap: 1.25rem; padding-inline-start: 1.5rem; }
.next-steps li { padding-inline: .3rem; }
.next-steps span { display: block; color: #475569; font-size: .95rem; margin-block-start: .5rem; }
.handoff-note { background: #eff6ff; border-inline-start: 3px solid #1d4ed8; border-radius: .4rem; padding: .8rem 1rem; }
header.page { background: #101f38; color: #fff; border: none; border-radius: 0 0 1.5rem 1.5rem; padding: 2rem; }
header.page .meta, header.page .section-note { color: #e2e8f0; }
header.page .eyebrow { color: #c7d2fe; font-size: .8rem; letter-spacing: .15em; margin: 0 0 .8rem; }
header.page details { margin-block-start: 1rem; }
header.page h1 { font-size: clamp(1.45rem, 4vw, 2.2rem); }
header.page p { overflow-wrap: anywhere; }
main > section { border-radius: 1rem; padding: 1.5rem; }
.rule { border-radius: .7rem; }
.occurrence-details { margin-block-start: .75rem; }
#select-verified, #download-selected, #clear-selected { background: #fff; color: #1d4ed8; }
#select-verified:hover, #download-selected:hover, #clear-selected:hover { background: #eff6ff; }
#packet-next[hidden] { display: none; }
@media print { .toolbar, #packet-fallback, #packet-status { display: none; } }
@media (max-width: 40rem) {
  .next-steps { grid-template-columns: 1fr; }
  .toolbar { flex-direction: column; align-items: stretch; }
  header.page h1 { font-size: 1.3rem; }
}
"""


# The user-interface script. It parses the embedded JSON into
# window.auditReport, tracks the selection, and hands ONLY the selected
# identities to window.RepairPack.build. All dynamic text is written with
# textContent / value, never innerHTML.
_UI_SCRIPT = """
(function () {
  'use strict';
  var statusEl = document.getElementById('packet-status');
  var countEl = document.getElementById('selected-count');
  var fallbackEl = document.getElementById('packet-fallback');
  var textareaEl = document.getElementById('packet-text');
  var selectionRevision = 0;

  try {
    window.auditReport = JSON.parse(document.getElementById('audit-report-data').textContent);
  } catch (error) {
    window.auditReport = null;
  }

  function announce(message, isError) {
    statusEl.textContent = message;
    statusEl.className = isError ? 'error' : '';
  }

  function selectedIds() {
    var boxes = document.querySelectorAll('input.finding-select');
    var ids = [];
    for (var i = 0; i < boxes.length; i++) {
      if (boxes[i].checked) { ids.push(boxes[i].value); }
    }
    return ids;
  }

  function updateCount() {
    selectionRevision += 1;
    countEl.textContent = 'נבחרו ' + selectedIds().length + ' ממצאים';
    // A changed selection invalidates the previously displayed packet.
    fallbackEl.hidden = true;
    textareaEl.value = '';
    document.getElementById('packet-next').hidden = true;
    announce('', false);
  }

  function buildPacket() {
    if (!window.auditReport) {
      announce('נתוני הדוח המוטמעים אינם תקינים, לא ניתן לבנות חבילת תיקון.', true);
      return null;
    }
    var ids = selectedIds();
    if (ids.length === 0) {
      announce('לא נבחרו ממצאים. סמנו לפחות ממצא אחד ונסו שוב.', true);
      return null;
    }
    if (!window.RepairPack || typeof window.RepairPack.build !== 'function') {
      announce('בונה חבילת התיקון אינו זמין בקובץ דוח זה.', true);
      return null;
    }
    try {
      // Only the identities the user selected are handed to the builder.
      return { ids: ids, text: String(window.RepairPack.build(window.auditReport, ids)) };
    } catch (error) {
      announce('בניית חבילת התיקון נכשלה: ' + error.message, true);
      return null;
    }
  }

  function showFallback(text, message) {
    fallbackEl.hidden = false;
    textareaEl.value = text;
    announce(message, true);
    textareaEl.focus();
    textareaEl.select();
    try { document.execCommand('copy'); } catch (error) { /* manual copy remains */ }
  }

  document.getElementById('select-verified').addEventListener('click', function () {
    var boxes = document.querySelectorAll('input.finding-select[data-group="verified"]');
    for (var i = 0; i < boxes.length; i++) { boxes[i].checked = true; }
    updateCount();
    announce(boxes.length === 0
      ? 'אין ליקויים מאומתים לבחירה בדוח זה.'
      : 'נבחרו כל ' + boxes.length + ' הליקויים המאומתים.', boxes.length === 0);
  });

  document.getElementById('copy-selected').addEventListener('click', function () {
    var packet = buildPacket();
    if (!packet) { return; }
    var copyRevision = selectionRevision;
    var done = function () {
      if (copyRevision !== selectionRevision) { return; }
      announce('הבקשה עבור ' + packet.ids.length + ' ממצאים הועתקה. כעת הדביקו אותה בשיחה עם הסוכן.', false);
      document.getElementById('packet-next').hidden = false;
    };
    var failed = function () {
      if (copyRevision !== selectionRevision) { return; }
      showFallback(packet.text, 'ההעתקה האוטומטית נכשלה. העתיקו ידנית מתיבת הטקסט שלמטה.');
      document.getElementById('packet-next').hidden = false;
    };
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(packet.text).then(done, failed);
    } else {
      failed();
    }
  });

  document.getElementById('download-selected').addEventListener('click', function () {
    var packet = buildPacket();
    if (!packet) { return; }
    var blob = new Blob([packet.text], { type: 'text/markdown;charset=utf-8' });
    var url = URL.createObjectURL(blob);
    var link = document.createElement('a');
    link.href = url;
    link.download = 'repair-packet.md';
    document.body.appendChild(link);
    link.click();
    link.remove();
    setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
    announce('נשלחה בקשת הורדה של repair-packet.md עם ' + packet.ids.length + ' ממצאים. צרפו אותו לשיחה עם הסוכן.', false);
    document.getElementById('packet-next').hidden = false;
  });

  document.getElementById('clear-selected').addEventListener('click', function () {
    document.querySelectorAll('input.finding-select').forEach(function (box) { box.checked = false; });
    updateCount();
    announce('הבחירה נוקתה. לא בוצע שינוי באתר.', false);
  });

  document.addEventListener('change', function (event) {
    var target = event.target;
    if (target && target.classList && target.classList.contains('finding-select')) {
      updateCount();
    }
  });

  updateCount();
})();
"""


def _repair_pack_js():
    """The raw JS from repair_packet.browser_script(), or a safe placeholder."""
    if browser_script is None:
        return ("// repair_packet.browser_script() was unavailable when this report was\n"
                "// rendered; the page reports the missing packet builder when used.")
    script = str(browser_script())
    # Defensive: a literal close-tag sequence would end the script element.
    # Inside JS string literals "<\\/" and "</" are the same text.
    lowered = script.lower()
    out = []
    index = 0
    while True:
        found = lowered.find("</script", index)
        if found == -1:
            out.append(script[index:])
            break
        out.append(script[index:found + 1])
        out.append("\\/")
        out.append(script[found + 2:found + 8])
        index = found + 8
    return "".join(out)


def render_html(report):
    """Render the full standalone Hebrew RTL HTML report as one string."""
    not_performed = (report.get("metadata") or {}).get("run", {}).get("status") == "not-performed"
    groups = OrderedDict((
        (GROUP_VERIFIED, OrderedDict()),
        (GROUP_HEURISTIC, OrderedDict()),
        (GROUP_HUMAN, OrderedDict()),
        (GROUP_PASS, OrderedDict()),
        (GROUP_INCOMPLETE, OrderedDict()),
        (GROUP_UNTESTED, OrderedDict()),
        (GROUP_BEST_PRACTICE, OrderedDict()),
        (GROUP_REVIEW, OrderedDict()),
    ))
    for item in ([] if not_performed else report.get("findings") or []):
        if not isinstance(item, dict):
            continue
        grouped = groups[_classify(item)]
        grouped.setdefault(_rule_key(item), []).append(item)

    sections = [
        _toolbar_html(not_performed),
        _group_section_html(
            "verified", "ליקויים מאומתים אוטומטית",
            "ממצאים במצב fail שאומתו אוטומטית; אלו המועמדים הראשונים לתיקון.",
            groups[GROUP_VERIFIED], GROUP_VERIFIED,
            "לא נמצאו ליקויים מאומתים. היעדר ממצאים אינו מעיד על עמידה בתקן."),
        _group_section_html(
            "heuristic", "חשדות שצריך לאמת",
            "חשדות שדורשים אימות בדף המעובד לפני תיקון; אל תתקנו בעיניים עצומות.",
            groups[GROUP_HEURISTIC], GROUP_HEURISTIC,
            "לא נרשמו אזהרות היוריסטיות."),
        _group_section_html(
            "best-practice", "המלצות לשיפור לפי שיטות עבודה מומלצות",
            "אזהרות היוריסטיות נפרדות מכשלי WCAG. יש לאמת את הצורך בשינוי לפני תיקון.",
            groups[GROUP_BEST_PRACTICE], GROUP_BEST_PRACTICE,
            "לא נרשמו המלצות מסוג זה."),
        _group_section_html(
            "incomplete", "מופעים שהמנוע לא הכריע בהם",
            "רכיבים מסוימים שנבדקו אך axe-core לא הכריע לגביהם. אלה אינם כשלים מאומתים; נדרש אימות ממוקד.",
            groups[GROUP_INCOMPLETE], GROUP_INCOMPLETE,
            "לא נרשמו תוצאות לא מוכרעות של המנוע; אין בכך כיסוי מלא."),
        _group_section_html(
            "review", "פריטים נקודתיים לבדיקה אנושית",
            "מחוונים שנמצאו בעמוד ודורשים שיקול דעת, כגון משמעות טקסט חלופי או תוכן הצהרת נגישות. אינם כשל מאומת ואינם רשימת הבדיקות הכללית.",
            groups[GROUP_REVIEW], GROUP_REVIEW,
            "לא נרשמו פריטים נקודתיים מסוג זה."),
        _group_section_html(
            "human", "משימות בדיקה כלליות",
            "רשימת בדיקות להשלמה, שאינה ראיה לכך שנמצא ליקוי באתר.",
            groups[GROUP_HUMAN], GROUP_HUMAN,
            "לא נרשמו משימות כלליות."),
        _group_section_html(
            "untested", "רשומות בדיקה שלא בוצעה",
            "תקלות או בדיקות נקודתיות שלא בוצעו. הרשימה אינה כוללת את כל תחומי הנגישות שלא נכללו בסריקה; ראו את הכיסוי בתמצית.",
            groups[GROUP_UNTESTED], GROUP_UNTESTED,
            "לא נרשמו רשומות נוספות; אין להסיק מכך שהכול נבדק."),
        _pass_section_html(groups[GROUP_PASS]),
        _comparison_html(report),
        _limitations_html(report),
    ]
    if not_performed:
        sections = [_toolbar_html(True), _limitations_html(report)]

    disclaimer = report.get("disclaimer") or ""
    version = report.get("version") or ""

    return """<!DOCTYPE html>
<html lang="he" dir="rtl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>{css}</style>
</head>
<body>
<header class="page">
<p class="eyebrow" dir="ltr">NEXT IMPACT / ACCESSIBILITY</p>
<h1>{title}</h1>
<p>{scope_intro}</p>
{summary}
<details><summary>פרטי הסריקה והכיסוי</summary>{run_meta}</details>
</header>
<main>
{sections}
</main>
<footer class="page">
<h2>הבהרה והגבלת אחריות</h2>
<p lang="en" dir="ltr">{disclaimer}</p>
<p>israeli-accessibility-auditor <span dir="ltr">{version}</span></p>
</footer>
<script type="application/json" id="audit-report-data">{report_json}</script>
<script>
{repair_pack}
</script>
<script>
{ui_script}
</script>
</body>
</html>
""".format(
        title=_esc(TITLE),
        css=_CSS,
        scope_intro=_esc("היקף הבדיקה: " + str((report.get("scope") or {}).get("target") or "לא צוין") +
                        (" — העמוד המבוקש לא נבדק" if not_performed else
                         " — העמוד והמצב המתועדים בלבד" if (report.get("scope") or {}).get("rendered") else
                         " — ניתוח סטטי של המקור בלבד")),
        summary=_summary_html(report),
        run_meta=_run_metadata_html(report),
        sections="\n".join(sections),
        disclaimer=_esc(disclaimer),
        version=_esc(version),
        report_json=_json_embed(report),
        repair_pack=_repair_pack_js(),
        ui_script="" if not_performed else _UI_SCRIPT,
    )
