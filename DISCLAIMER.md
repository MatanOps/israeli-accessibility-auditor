# Scope and disclaimer / היקף והבהרה

This tool provides a partial technical accessibility review of supplied source files or fetched HTML. It is not an accessibility certificate, legal opinion, proof of compliance with Israeli law, or a substitute for a qualified accessibility professional, screen-reader testing, or testing with real users with disabilities.

The Israeli baseline and the engineering target are distinct:

- **Israeli baseline:** SI 5568 (ת״י 5568), the applicable Israeli regulations, and official Israeli guidance. This tool does not decide which legal obligations, exceptions, or exemptions apply to a particular organization.
- **Recommended engineering target:** WCAG 2.2 Level AA. This recommendation does not assert that SI 5568 is equivalent to WCAG 2.1 or WCAG 2.2, or that this scanner implements all their criteria.

A `pass` describes only the specific observed condition and scope named in a finding. No findings, a successful exit code, or a report containing passes does not establish overall accessibility. Source patterns can produce false positives and false negatives. Fetched HTML excludes JavaScript-rendered states; unobserved behavior remains `not-tested` or `human-review-required`. An accessibility-statement link is only an indicator; its content and applicability require review.

The tool and reports are provided as-is, without a warranty of accuracy, completeness, accessibility, or legal compliance. Laws, regulations, standards, and official guidance may change. Users are responsible for reviewing current applicable sources, arranging the required professional and legal review, and verifying their own implementation. See the [MIT license](LICENSE) for the software's warranty and liability terms.

הכלי מספק בדיקה טכנית חלקית של קובצי מקור או HTML שנשלף מאתר. הוא אינו אישור נגישות, חוות דעת משפטית, הוכחה לעמידה בחוק או תחליף למורשה נגישות, לבדיקת קורא מסך ולבדיקה עם משתמשים אמיתיים עם מוגבלות.

יש להבחין בין הבסיס הישראלי — ת״י 5568, הדין החל והמקורות הרשמיים בישראל — לבין יעד ההנדסה המומלץ, WCAG 2.2 ברמה AA. אין בכך טענה שת״י 5568 זהה ל־WCAG 2.1 או ל־WCAG 2.2. הכלי אינו קובע אילו חובות או פטורים חלים על ארגון מסוים.

תוצאת `pass` חלה רק על התנאי המסוים שנבדק ובהיקף שתועד. היעדר ממצאים או קוד יציאה תקין אינם מעידים על נגישות מלאה. ייתכנו התרעות שגויות וליקויים שלא יאותרו. תוכן והתנהגות שלא נבדקו יישארו מסומנים כ־`not-tested` או `human-review-required`.

הכלי והדוחות ניתנים כפי שהם, ללא אחריות לדיוק, לשלמות, לנגישות או לעמידה בדרישות הדין. חוקים, תקנות, תקנים והנחיות רשמיות עשויים להשתנות. האחריות לעיון במקורות העדכניים החלים, לקבלת הבדיקה המקצועית והמשפטית הנדרשת ולאימות היישום היא של המשתמשים. תנאי האחריות והגבלת החבות של התוכנה מפורטים ב־[רישיון MIT](LICENSE).

Official and technical references, reviewed 2026-09-22:

- [Israeli Commission for Equal Rights of Persons with Disabilities: website accessibility](https://www.gov.il/he/pages/website_accessibility).
- [SI 5568 Part 1, September 2023, official government copy](https://www.gov.il/BlobFolder/legalinfo/israeli_accessibility_standards_pdf/he/sitedocs_si-5568-1-september-2023.pdf).
- [W3C WCAG 2.2](https://www.w3.org/TR/WCAG22/).
