# Israeli Accessibility Auditor

A free, MIT-licensed command-line accessibility scanner for frontend source and fetched HTML, with Hebrew and RTL checks. It produces evidence-based Markdown and JSON reports and identifies work requiring human review.

**Partial technical checks, not accessibility certification or legal advice.** The Israeli baseline is SI 5568 and the applicable official Israeli sources; the recommended engineering target is WCAG 2.2 AA. These are separate references. Read [DISCLAIMER.md](DISCLAIMER.md).

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

The repository also includes an agent skill for Codex and Claude Code. See [SKILL.md](SKILL.md). Skill installation does not replace installing the Python dependencies.

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

## התחלה מהירה

לאחר ההתקנה, הריצו `python scripts/audit.py --path "./my app"`. שני הדוחות יישמרו בתיקיית `accessibility-report`. לבדיקת אתר השתמשו ב־`--url`; נבדק ה־HTML שהשרת מחזיר בלבד, ללא הרצת JavaScript. ברירת המחדל היא בדיקה בלבד. הדוח אינו אישור נגישות או הוכחה לעמידה בחוק.

## Credits and license

MIT. Scanner code is adapted from `alirezarezvani/claude-skills` and `skills-il/localization`. AccessLint methodology and `peleg-jpg/site-legal-kit` informed the review boundaries. Source URLs, pinned revisions, original copyright notices, and adaptation details are recorded in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
