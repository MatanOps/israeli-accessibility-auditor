# Israeli Accessibility Auditor

**בדיקות נגישות ותיקונים** — כלי קהילתי לבדיקת נגישות של אתר ציבורי או של קוד מקור, עם דוח בעברית.

## התקנה חד־פעמית

מריצים פעם אחת במחשב (לא בתוך פרויקט). פקודה אחת מתקינה ל־Codex ול־Claude Code יחד:

```bash
npx skills add MatanOps/israeli-accessibility-auditor --global --agent codex claude-code --yes
```

רק Codex:

```bash
npx skills add MatanOps/israeli-accessibility-auditor --global --agent codex --yes
```

רק Claude Code:

```bash
npx skills add MatanOps/israeli-accessibility-auditor --global --agent claude-code --yes
```

נדרשים Node.js 20 ומעלה ו־npm (להתקנה ולסריקת דפדפן) ו־Python 3.9 ומעלה.

## מה אומרים לסוכן

בכל פרויקט או שיחה חדשה ב־Codex או Claude Code:

```text
בדוק את https://example.com עם israeli-accessibility-auditor
```

במקום הכתובת אפשר לתת תיקיית קוד. הסוכן מכין את סביבת הסריקה בעצמו בהרצה הראשונה (הורדות; לוקח יותר זמן) ומשתמש בה שוב בהרצות הבאות. אם חסרים Python או Node.js, הוא יגיד מה להתקין — פעולה אחת — ותריצו שוב.

## מה מקבלים

- לפני ההתחלה: היקף הבדיקה (כתובת, מספר עמודים וזמן מרביים).
- בסיום: כמה עמודים נבדקו, נכשלו ונותרו; שלושת סוגי הבעיות העיקריים לפי סדר עדיפות, בעברית קצרה, עם הצעד הבא לתיקון.
- דוח HTML בעברית (וגם Markdown ו־JSON) שנכתב מחוץ לתיקיית האתר. בכרטיס ממצא יש כפתור **הכנת בקשה לתיקון זה**; הכפתורים מכינים טקסט להעתקה בלבד.

## מגבלות

- "בדוק" = בדיקה בלבד, ללא שינוי באתר או בקוד. לתיקון נדרשים אישור מפורש וגישה בפועל לקוד או לעורך. כתובת של WordPress/Wix ודומיהם מאפשרת בדיקה, לא עריכה אוטומטית.
- נבדקים עמודים ציבוריים באותה כתובת אתר, שמצאנו בקישורים ובמפת האתר: עד 100 עמודים ו־10 דקות כברירת מחדל (ניתן להגדיל עד 5000 עמודים / 7200 שניות). השתמשו בכתובת הסופית המופיעה בדפדפן. אין כניסה לחשבון ואין פעולות משתמש. כשמגיעים לגבול, הדוח חלקי ומציין עמודים שנותרו — הוא אינו "נקי". אין הבטחה שכל העמודים באתר נמצאו.
- זו בדיקה אוטומטית; היא אינה אישור נגישות מלאה. ת״י 5568 והמקורות הרשמיים בישראל הם הבסיס הישראלי; WCAG 2.2 AA הוא יעד ההנדסה המומלץ. אין טענה שהם זהים. נדרשות בדיקות מקלדת, קורא מסך ומשתמשים אמיתיים. [מגבלות ואחריות](DISCLAIMER.md).

---

## English reference

### Install

One-time, per machine, with the `skills` CLI (commands above; verified with skills 1.7.0). Requirements: Python 3.9+; Node.js 20+ and npm for browser scans. The skill never installs system runtimes, never uses `sudo`, and never touches the audited project's dependencies. The browser path was exercised on macOS and Ubuntu (GitHub Actions) with Python 3.12 and Node.js 22; no claim is made for other platforms.

### Run modes

`scripts/audit.py` is the only entry point. Always pass `--prepare`: the first run creates an isolated cache (`.runtime` inside the skill; Python venv, pinned Node packages, local Chromium) and later runs reuse it. For a read-only skill location set `A11Y_AUDITOR_CACHE` to an absolute writable path. Write reports outside the audited project.

```bash
# Public site (default for a website request): same-origin link + sitemap discovery
python3 /abs/skill/scripts/audit.py --prepare --site https://example.com --output /abs/reports/site

# Bigger budget (limits: --max-pages 1-5000, --max-seconds 5-7200, --timeout 2-120 per page)
python3 /abs/skill/scripts/audit.py --prepare --site https://example.com --max-pages 500 --max-seconds 1800 --output /abs/reports/site

# One rendered page only (explicit single-page compatibility)
python3 /abs/skill/scripts/audit.py --prepare --url https://example.com/page --output /abs/reports/page

# Fetched HTML only, no JavaScript (only with --url; partial by design)
python3 /abs/skill/scripts/audit.py --prepare --url https://example.com --static --output /abs/reports/static

# Source only, no browser: HTML, JSX, TSX, Vue, Svelte, CSS
python3 /abs/skill/scripts/audit.py --prepare --path "/abs/my app" --output /abs/reports/source

# Compare with an earlier JSON report (same target and mode)
python3 /abs/skill/scripts/audit.py --prepare --site https://example.com --baseline /abs/before/accessibility-report.json --output /abs/reports/after
```

Site defaults: 100 pages, 600 seconds. The queue is bounded, no authentication or user interaction is performed, and a page that fails, blocks, or times out is recorded as an exception in a partial report rather than stopping the run.

### Reports and exit codes

Each run writes `accessibility-report.html`, `.md` and `.json` to `--output`; `--format markdown|json` only changes the console output. Every finding keeps `id`, `stable_id`, `rule_id`, `engine` (`static`/`axe`), category, severity, status (`fail`, `pass`, `warning`, `not-tested`, `human-review-required`), evidence type, WCAG criterion, location, evidence, explanation and remediation. `metadata.run` records original/final URL, timestamps, pages attempted, failed and pending, states and untested coverage.

| Exit | Meaning |
| --- | --- |
| `0` | No `fail`/`warning` findings. Not a compliance statement; human checks remain. |
| `1` | At least one `fail`/`warning` finding. Not an installation failure. |
| `2` | Operational error, or a site run that did not complete (cap reached, blocked or failed pages). Read `errors` and the pending pages. |

A baseline comparison distinguishes `fixed_verified`, `changed_unverified`, `remaining`, `new` and `not_tested`; a finding that disappears is not proof of a fix.

### Scope and limits

- A waiting or challenge page is not a result. CAPTCHA and site protections are never bypassed; a browser failure never silently becomes a static scan.
- `--site` visits discovered public same-origin pages within the budget; nothing guarantees that every page on the site was found. Same origin means identical scheme, host and port: pass the final URL as the browser shows it (`https`, with or without `www`), because a redirect to `www` or from `http` to `https` is another origin and stops the crawl. `--url` loads one page in its initial state. `--path` and `--static` are partial: dynamic expressions, props, slots and JavaScript-rendered states need human verification.
- Static contrast checks use explicit color pairs; cascade, transparency, images and real text size can change the outcome.
- Follow the [manual checks](references/manual-checks.md) and the [Hebrew/RTL notes](references/israel-hebrew-rtl.md). Full terms: [DISCLAIMER.md](DISCLAIMER.md).
- No account, external scanning service or AI-provider API is used; a browser scan loads the audited site and its resources over the network.

### Development

```bash
python3 scripts/audit.py --prepare --path tests/fixtures/inaccessible-hebrew.html --output ./fixture-report
.runtime/venv/bin/python -m unittest discover -s tests
```

The fixture is intentionally broken, so exit `1` is expected. Browser tests need a prepared cache.

### Credits and license

MIT licensed. Built on axe-core, Playwright and the packages listed with their notices in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
