# Israel, Hebrew and bidirectional content

## Two separate baselines

Use SI 5568 (ת״י 5568) and the relevant official Israeli sources when investigating Israeli requirements. Use WCAG 2.2 Level AA as this project's recommended engineering target. A criterion reference is a technical mapping, not a conclusion about legal applicability. Do not describe SI 5568 as identical to WCAG 2.1 or WCAG 2.2.

The scanner cannot establish legal obligations or exemptions. Review current official guidance and obtain qualified advice where a legal determination is required. The Commission's [website-accessibility guide](https://www.gov.il/he/pages/website_accessibility) and the [government copy of SI 5568 Part 1, September 2023](https://www.gov.il/BlobFolder/legalinfo/israeli_accessibility_standards_pdf/he/sitedocs_si-5568-1-september-2023.pdf) are source references, not functionality implemented by this tool.

## Language and direction are different

For a document whose primary language is Hebrew, a usual starting point is:

```html
<html lang="he" dir="rtl">
```

Set the language to match the actual content. Do not require Hebrew merely because a domain ends in `.il`. `lang="he-IL"` can be appropriate; English pages should identify English. Mark passages in other languages where required by WCAG 3.1.2. An attribute's presence does not prove its value describes the page correctly.

Language metadata does not set base direction. Review Hebrew reading order, focus order, punctuation, dates, numbers, telephone numbers, URLs, and mixed-language labels. Isolate dynamic mixed-direction text with appropriate markup such as `<bdi>` or `dir="auto"` where suitable. Prefer logical CSS properties when implementing layouts. Inspect the rendered result and the accessibility tree; a source check cannot verify their agreement. See [W3C guidance on HTML direction](https://www.w3.org/International/questions/qa-html-dir).

## Accessibility-statement indicators

A link whose text or destination suggests an accessibility statement is a discovery hint. It does not verify that the linked page exists, is accessible, describes the service accurately, or meets applicable requirements. An absent matching link in one HTML response does not prove the organization has no statement. Review it against the Commission's [official statement guidance](https://www.gov.il/he/pages/declaration_website_accessibility).

## Human review

Test Hebrew pronunciation and language changes with real assistive technology. Test mixed Hebrew/English content, form instructions and errors, right-to-left navigation, zoom and mobile layouts. Record the exact browser, assistive technology, page state, and observation; do not predict a screen reader's announcements from markup alone. Use [the manual checklist](manual-checks.md).

References reviewed 2026-09-22. Consult the live official sources for subsequent changes. See [the disclaimer](../DISCLAIMER.md).
