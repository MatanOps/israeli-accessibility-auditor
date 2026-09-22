# Israeli Accessibility Auditor
### כלי בדיקת נגישות מבית Next Impact

כלי חינמי וקוד פתוח לזיהוי בעיות נגישות באתרי אינטרנט ובאפליקציות Web, עם דגש על עברית וכתיבה מימין לשמאל. מקבלים דוח מפורט עם ממצאים, הסברים והמלצות לתיקון.

**מיועד לשימוש בעזרת Codex או Claude Code עם גישה לקבצים ולטרמינל.** אין צורך להכיר פקודות Python; סוכן הקוד יכול לבצע עבורכם את ההכנה וההרצה. אם אין לכם סוכן כזה, ההוראות הטכניות מופיעות בהמשך.

> הבדיקות חלקיות: הכלי בודק קוד או HTML של עמוד בודד ואינו מפעיל JavaScript בעצמו. הדוח אינו אישור נגישות או תחליף לבדיקה מקצועית.

## מתחילים בשלושה צעדים

1. פתחו את Codex או Claude Code. לבדיקת פרויקט מקומי, פתחו בו את תיקיית הפרויקט.
2. העתיקו את הבקשה הבאה לשיחה. לבדיקת אתר, הוסיפו בסופה את הכתובת שלו.
3. הסוכן יבצע את ההכנה הזמינה בסביבה שלכם ויריץ את הבדיקה. ייתכן שתתבקשו לאשר התקנה או גישה לקבצים.

```text
השתמש בכלי בדיקת הנגישות של Next Impact:
https://github.com/MatanOps/israeli-accessibility-auditor

אם הכלי עדיין אינו מותקן, קרא את הוראות הריפו והתקן אותו
בסביבה המתאימה לסוכן שבו אני משתמש.
קרא את SKILL.md, טפל בהכנת סביבת Python נפרדת ובהתקנת
requirements.txt, ואז בדוק את הפרויקט הפתוח או את כתובת האתר שצירפתי.
אם חסר רכיב שאינך יכול להתקין, הסבר לי בעברית מה חסר ומה הצעד הבא.

בצע בדיקה בלבד, ללא שינוי בקוד האתר.
בסיום הצג בעברית את הבעיות העיקריות לפי דחיפות, מה מומלץ לתקן,
ומה לא נבדק. תן לי קישורים לדוחות שנוצרו.
```

הבקשה מיועדת לסוכן קוד שמסוגל להתקין כלים ולהריץ פקודות. היא אינה מתקין עצמאי; הצלחת ההכנה תלויה בכלים ובהרשאות שכבר זמינים במחשב.

**כבר התקנתם?** כתבו לסוכן:

> השתמש ב־israeli-accessibility-auditor לבדיקת האתר שלי בכתובת שצירפתי. בצע בדיקה בלבד והסבר את התוצאות בעברית.

## מה מקבלים?

- דוח קריא עם הבעיה, המיקום שלה, הסבר והמלצה לתיקון.
- חלוקה לנושאים: תמונות, טפסים, כותרות, קישורים, מבנה העמוד, ARIA, ניגודיות, עברית ו־RTL ועוד.
- הבחנה בין ממצא שנבדק אוטומטית, חשד שדורש אימות ובדיקה שעדיין צריך לבצע ידנית.
- שני קבצים: `accessibility-report.md` לקריאה ו־`accessibility-report.json` לעיבוד נוסף. סיכום הסוכן יהיה בעברית לפי הבקשה; דוחות ה־CLI עצמם באנגלית.

בדיקה ללא ממצאים אינה מעידה שהאתר נגיש במלואו. ניווט במקלדת, קוראי מסך, תוכן דינמי ומצבי שימוש נוספים מחייבים בדיקה משלימה. [הסבר על מגבלות ואחריות](DISCLAIMER.md).

## התקנה בפקודה אחת למשתמשי סוכני קוד

אם Node.js מתאים כבר מותקן, אפשר להתקין את ה־Skill מתיקיית הפרויקט:

```bash
npx skills add MatanOps/israeli-accessibility-auditor
```

לאחר מכן השתמשו בבקשה למעלה. הפקודה מתקינה את ה־Skill בלבד; סוכן הקוד עדיין צריך להכין את סביבת Python ואת התלויות. פרטי הגרסאות שנבדקו מופיעים בהוראות המורחבות.

<details>
<summary><strong>הוראות טכניות: התקנה ידנית, הרצה, מגבלות ובדיקות (English)</strong></summary>

## Install and run

Requires Python 3.9+; verified on Python 3.9 and 3.12. Use a maintained Python distribution built with OpenSSL 1.1.1+ for HTTPS auditing; the older macOS system Python may emit an unsupported LibreSSL warning. Public Python dependencies are pinned in `requirements.txt`: `requests==2.32.5` and `beautifulsoup4==4.15.0`.

```bash
git clone https://github.com/MatanOps/israeli-accessibility-auditor.git
cd israeli-accessibility-auditor
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python scripts/audit.py --path tests/fixtures/inaccessible-hebrew.html
```

On Windows, activate with `.venv\Scripts\activate` instead. Once the virtual environment is active, use `python` as shown above. `scripts/audit.py` is the sole command-line entry point.

The included fixture deliberately contains defects, so exit code `1` is expected. To audit your own project or a page:

```bash
python scripts/audit.py --path "./my app"
python scripts/audit.py --url https://example.co.il --output ./accessibility-report
```

The repository also includes an agent skill for Codex and Claude Code. Install it from your project directory:

```bash
npx skills add MatanOps/israeli-accessibility-auditor
```

This exact command was verified against the public repository with `skills` 1.7.0 and Node.js 26.5.0; that installer requires Node.js 22.20+. See [SKILL.md](SKILL.md). Skill installation copies the bundled scripts and references; it does not install the Python dependencies. Create a virtual environment and install the `requirements.txt` from the installed skill directory before running its `scripts/audit.py`.

After installing the skill, ask Codex to “Use `$israeli-accessibility-auditor` to audit this project; do not edit application code.” In Claude Code, invoke `/israeli-accessibility-auditor` with the project path or URL. The agent can add browser observations and retain a clear human-testing handoff.

## Reports and exit codes

Both modes write these files in the output directory (`./accessibility-report` by default):

```text
accessibility-report.md
accessibility-report.json
```

`--format markdown` or `--format json` selects console output; both files are still generated. Operational failures are included in reports where possible. Invalid CLI arguments or an unwritable destination can prevent report creation.

| Code | Meaning |
| --- | --- |
| `0` | No `fail` or `warning` findings. Untested and human-review items may remain; this does not mean compliance. |
| `1` | At least one `fail` or `warning` finding. |
| `2` | Operational error, such as an unavailable URL, missing dependency, invalid path, no supported frontend files, or an output failure. |

Findings include ID, category, severity, status, evidence type, WCAG reference, location, snippet, explanation, and remediation. Status is one of `fail`, `pass`, `warning`, `not-tested`, or `human-review-required`. Evidence is `automatically-verified`, `heuristic`, or `human-verification-required`; severity does not establish certainty.

The report organizes results into images/media; semantics/landmarks; headings; keyboard/focus; forms; links/buttons; ARIA; tables; contrast; zoom/reflow/motion; Hebrew/RTL; language; statement indicators; and human verification. Counts describe reported checks, not overall accessibility coverage.

## Scope and limits

- `--path` scans HTML, JSX, TSX, Vue, Svelte, and CSS. Source patterns are approximate; framework expressions and rendered behavior need verification.
- `--url` fetches one HTML page, following HTTP redirects. It does not render JavaScript or crawl the website. SPA content and runtime behavior remain untested.
- Static contrast checks cannot establish the full rendered CSS cascade, image backgrounds, transparency, or every interaction state.
- A statement link is an indicator, not verification of the statement's content or legal sufficiency.
- The scanner does not modify audited files. Follow [manual-checks.md](references/manual-checks.md), including real screen-reader and user testing.

No account, hosted scanner, external API, upstream checkout, or Git submodule is required. URL mode connects to the target website; source mode operates locally. Installation uses public package registries.

## Examples

```bash
python scripts/audit.py --path tests/fixtures/accessible.html --output ./reports/accessible
python scripts/audit.py --path tests/fixtures/inaccessible-hebrew.html --format json --output ./reports/inaccessible
```

Review the generated `accessibility-report.md` and `accessibility-report.json` in each directory. Findings on the deliberately inaccessible fixture are expected.

Run the regression tests after installing the requirements:

```bash
python -m unittest discover -s tests
```

</details>

---

יוזמה קהילתית של **[Next Impact](https://nextimpact.co.il)** — AI, אוטומציה ופתרונות טכנולוגיים לעסקים.

[רישיון MIT](LICENSE) · [רישיונות רכיבי צד שלישי](THIRD_PARTY_NOTICES.md) · [הבהרת אחריות](DISCLAIMER.md)
