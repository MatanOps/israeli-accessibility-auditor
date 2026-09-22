---
name: israeli-accessibility-auditor
description: Audit website and web-app accessibility using bundled Python scanners, with Hebrew, RTL and Israeli context. Use for source or URL accessibility reviews and evidence-based reports; fixes require an explicit request.
license: MIT
---

# Israeli Accessibility Auditor

Produce a scoped technical accessibility report. Default to audit-only. Do not change application code, install overlays, publish statements, or claim compliance, certification or legal approval.

## Workflow

1. Identify the stack, pinned runtime, run commands, main routes and critical user journeys from available source. Choose a representative sample: entry page, form, navigation, and relevant dialogs, menus, validation and SPA states. Record the sample and what is excluded; the CLI does not discover or crawl routes.
2. Locate this installed skill's directory. Use Python 3.9+ and an isolated virtual environment with its `requirements.txt`. Run the bundled entry point using absolute paths when outside the skill directory:

   ```bash
   python scripts/audit.py --path ./my-app --output ./accessibility-report
   python scripts/audit.py --url https://example.co.il --format json --output ./accessibility-report
   ```

   These paths are examples relative to the skill directory. Replace the target with the user's actual path/URL. Keep reports outside the application unless requested. Each run writes `accessibility-report.md` and `accessibility-report.json`; use distinct output directories for different samples. Exit 1 means findings, 2 means incomplete execution. Exit 0 is never a whole-site pass.
3. Review the evidence, especially heuristic source findings. Source regexes cannot resolve component props, spread attributes, slots or runtime handlers. URL mode inspects the returned HTML without executing JavaScript. A rendered SPA, authenticated state or unvisited route remains `not-tested`; do not turn absent static findings into `pass`.
4. Follow [manual-checks.md](references/manual-checks.md) with available browser tooling. Exercise the actual rendered state and capture keyboard/focus, zoom/reflow, contrast and dynamic-state evidence. Browser interaction alone does not substitute for human or assistive-technology testing. If unavailable, give exact steps and retain `human-review-required` or `not-tested`.
5. Apply [israel-hebrew-rtl.md](references/israel-hebrew-rtl.md). Check actual language, base direction, mixed Hebrew/English, phone/email/URL order and Hebrew validation. Treat Israeli legal baseline (SI 5568 and official sources) separately from the engineering target, WCAG 2.2 AA. Check source dates; do not determine legal applicability or exemptions.
6. Produce consolidated Markdown and JSON reports using the CLI finding fields. Preserve original automated evidence and add browser/human observations with their method, route/state and verification status. Keep severity independent from evidence type: `automatically-verified`, `heuristic`, `human-verification-required`. Never invent selectors, source lines, screen-reader results, criterion mappings, or successful checks. Preserve all untested coverage and required human checks.

## When fixes are explicitly requested

Prioritize by user impact and severity. Present the proposed diff before applying it, make focused changes within the requested scope, then show the resulting diff and rerun the same audit plus affected browser checks. Reassess heuristics; do not claim a rendered problem fixed from a static scan alone. Keep unresolved and human checks visible. Do not add an accessibility overlay as a structural fix.

## Limits

Technical assistance only; no legal advice, accessibility certification or warranty of compliance. Automated tools detect only some barriers. Human, assistive-technology and real-user testing remain necessary. Laws and standards may change; the user remains responsible for professional and legal review. Full terms: [DISCLAIMER.md](DISCLAIMER.md). Attribution: [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
