# Third-party notices

This repository includes adapted MIT-licensed code. All runtime code is local to this repository or supplied by the public dependencies in `requirements.txt` and `package-lock.json`; the upstream repositories are provenance references, not runtime dependencies. Upstream projects do not endorse this project.

Sources and licenses were inspected before copying. The pinned revisions below identify the inspected source snapshots.

## alirezarezvani/claude-skills — adapted code

- Repository: https://github.com/alirezarezvani/claude-skills
- Commit: `19392f7a08264ed00486a251f5b2098321771f94`
- License: MIT, verified from the root `LICENSE` at that commit.
- Copyright: Copyright (c) 2025 Alireza Rezvani.
- Source `engineering-team/a11y-audit/skills/a11y-audit/scripts/a11y_scanner.py` adapted into `scripts/source_scanner.py`.
- Source `engineering-team/a11y-audit/skills/a11y-audit/scripts/contrast_checker.py` adapted into `scripts/contrast_checker.py`.
- Related rule coverage is consolidated in `scripts/html_scanner.py`; the finding model, category grouping and remediation patterns also inform `scripts/report.py`.
- Source-scanner adaptations: retain file discovery, tag/attribute matching, rule IDs and remediation guidance; handle multiline tags and component syntax conservatively; dispatch HTML to the HTML parser and CSS to contrast helpers; remove document-level assumptions for components, id-as-label shortcuts, redundant live-region requirements, and the upstream command-line/report code. File-reading errors propagate to the common entry point.
- Contrast adaptations: retain color parsing, relative luminance, contrast ratio and thresholds; extend named colors and declaration parsing; reject unresolved/translucent values and report unknown backgrounds; add shared findings for explicit color pairs and focus/motion indicators. Remove upstream demo, suggestion and command-line output modes. Calculated pairs are distinguished from unverified rendered contrast.
- Report adaptations: replace upstream formatters and severity-based results with one status/evidence/severity contract, 14 categories, explicit untested coverage, human-review items, and Markdown/JSON output. No overall compliance score or certificate is produced. `scripts/audit.py` provides the sole command-line interface.

Original license notice:

```text
MIT License

Copyright (c) 2025 Alireza Rezvani

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## skills-il/localization — adapted code

- Repository: https://github.com/skills-il/localization
- Commit: `9ec3b05fd9a551f123c3a5fb7abcc208b85a10e5`
- License: MIT, verified from the root `LICENSE` at that commit.
- Copyright: Copyright (c) 2026 Skills IL (Yootech).
- Source `israeli-accessibility-compliance/scripts/audit_a11y.py` adapted into `scripts/html_scanner.py`.
- Adaptations: retain BeautifulSoup traversal, label associations, heading checks, and skip-link/statement discovery; resolve ARIA name references and consolidate HTML image, form, ARIA, table, media, viewport and contrast checks in this module. Infer Hebrew context from visible content rather than requiring Hebrew/RTL for every page; handle complete documents and fragments separately. Replace boolean/scored results with the shared finding contract, distinguish static facts from heuristics, and add explicit unrendered-content limitations. Remove HTTP fetching, standalone CLI/report handling and compliance verdicts; the common entry point handles fetching and output.

Original license notice:

```text
MIT License

Copyright (c) 2026 Skills IL (Yootech)

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## AccessLint/skills — methodology reference only

- Repository: https://github.com/AccessLint/skills
- Commit: `2e9d7336678302d0bc08848e92542560295b34d3`
- Reviewed file: `plugins/accesslint/skills/shared/methodology.md`.
- The root `README.md` identifies the license as MIT, but the inspected tree has no standalone `LICENSE` file with a copyright and permission notice. No source code or substantial documentation text is copied from this repository.
- Methodological influence: classify evidence separately from user impact; distinguish automated observations, heuristic conclusions, and work requiring a human; preserve untested scope explicitly.
- No AccessLint service, MCP server, `@accesslint/core` package, account, or upstream checkout is required at runtime.

## peleg-jpg/site-legal-kit — reference only

- Repository: https://github.com/peleg-jpg/site-legal-kit
- Commit: `5341b4667a1684d7253edc6f3216a6b39c60430f`
- License: MIT, verified from the root `LICENSE` at that commit.
- Copyright: Copyright (c) 2026 site-legal-kit contributors.
- Reviewed as a reference for keeping accessibility work separate from legal determinations. No code or substantial documentation text is copied into this project.

## Standards and official guidance

W3C and Israeli government materials linked from the documentation are references, not copied implementations. Their respective rights and terms apply. The project's MIT license does not relicense those external materials.

## Public browser runtime dependencies

Playwright 1.63.0 and playwright-core 1.63.0 are distributed under Apache-2.0. Their bundled NOTICE identifies Microsoft Corporation and code derived from Puppeteer under Apache-2.0. Source: https://github.com/microsoft/playwright. Preserve the LICENSE and NOTICE shipped with installed packages.

axe-core 4.13.0 is distributed under MPL-2.0. Its source header states Copyright (c) 2015 - 2026 Deque Systems, Inc. Source: https://github.com/dequelabs/axe-core. The installed package retains its original LICENSE and source copyright notice; this project loads its bundled axe.min.js without modifying it.

These dependencies are pinned in package-lock.json and installed from public packages; they are not relicensed under this project's MIT license. Downloaded Chromium binaries retain their upstream licenses and notices. No engine is loaded from a CDN during an audit.
