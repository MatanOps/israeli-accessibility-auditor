"""Focused real-axe Hebrew coverage and conservative finding classification."""

from contextlib import redirect_stderr, redirect_stdout
import http.server
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import tempfile
import unittest
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'scripts'))
from audit import axe_findings, main, rendered_supplement
from html_scanner import scan_html
from report import KEYBOARD


def document(body, language='he'):
    return ('<!doctype html><html lang="' + language + '" dir="rtl"><head><meta charset="utf-8">'
            '<title>בדיקת נגישות</title></head><body>' + body + '</body></html>')


def fixture(fixed=False):
    choices = ['בחר אפשרויות עבור מוצר ' if fixed else 'הצגת אפשרויות עבור מוצר ']
    cart = 'הוספה לסל עבור מוצר ' if fixed else 'הוספה לעגלת הקניות מוצר '
    links = ''.join('<a id="option-{0}" href="/product/{0}" aria-label="{1}{0}">בחר אפשרויות</a>'
                    .format(i, choices[0]) for i in range(3))
    links += ''.join('<a id="cart-{0}" href="/product/{0}" aria-label="{1}{0}">הוספה לסל</a>'
                     .format(i, cart) for i in range(2))
    return document('<a id="skip" href="#content">דלג לתוכן</a><main' + (' id="content"' if fixed else '')
                    + '><h1>חנות מוצרים לבדיקת נגישות בעברית</h1>' + links
                    + '<a id="expanded" href="/product/good" aria-label="בחר אפשרויות עבור חולצה כחולה">'
                    'בחר אפשרויות</a></main>')


class ClassificationTests(unittest.TestCase):
    def test_comparison_error_preserves_scan_status_and_summary(self):
        with tempfile.TemporaryDirectory() as temp:
            baseline = Path(temp) / 'baseline.json'
            baseline.write_text(json.dumps({'findings': []}), encoding='utf-8')
            for completed, expected in ((False, 'not-performed'), (True, 'partial')):
                with self.subTest(completed=completed):
                    scanned = {'ok': completed, 'html': document('<main><h1>בדיקה</h1></main>'),
                               'axe': {'violations': [], 'incomplete': [], 'passes': []},
                               'run': {'status': 'completed' if completed else 'not-performed',
                                       'rendered': completed},
                               'errors': [] if completed else ['Access challenge blocked the requested page']}
                    with mock.patch('audit.rendered_scan', return_value=scanned), \
                            mock.patch('compare.compare_reports', side_effect=ValueError('Invalid baseline')), \
                            mock.patch('audit.write_reports') as writer, \
                            redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                        code = main(['--url', 'http://localhost/', '--baseline', str(baseline),
                                     '--format', 'json', '--output', temp])
                    report = writer.call_args.args[0]
                    self.assertEqual(code, 2)
                    self.assertEqual(report['metadata']['run']['status'], expected)
                    self.assertEqual(report['summary']['run_status'], expected)
                    self.assertEqual(report['summary']['operational_errors'], 1 if completed else 2)

    def test_best_practice_and_incomplete_remain_distinct(self):
        def rule(identifier, tags):
            return {'id': identifier, 'tags': tags, 'impact': 'moderate',
                    'nodes': [{'target': ['#subject'], 'html': '<div id="subject"></div>'}]}
        axe = {'violations': [rule('heading-order', ['best-practice']),
                              rule('landmark-one-main', ['best-practice']),
                              rule('target-size', ['wcag22aa', 'wcag258'])],
               'incomplete': [rule('region', ['best-practice'])], 'passes': []}
        results = {item['rule_id']: item for item in axe_findings(axe, 'http://localhost/')}
        for name in ('heading-order', 'landmark-one-main'):
            item = results['axe-' + name]
            self.assertEqual((item['status'], item['evidence_type'], item['standards_basis']),
                             ('warning', 'heuristic', 'best-practice'))
            self.assertIsNone(item['wcag_criterion'])
        self.assertEqual(results['axe-region']['status'], 'human-review-required')
        self.assertEqual(results['axe-region']['evidence_type'], 'human-verification-required')
        self.assertEqual(results['axe-target-size']['category'], KEYBOARD)
        self.assertEqual(results['axe-target-size']['status'], 'fail')
        self.assertEqual(results['axe-target-size']['axe_tags'], ['wcag22aa', 'wcag258'])

    def test_rendered_language_has_no_duplicate_static_results(self):
        for lang in ('', 'xx-invalid-value', 'he'):
            result = rendered_supplement({'html': document('<main><h1>תוכן בעברית לבדיקת שפה</h1></main>', lang),
                                         'run': {}}, [], 'http://localhost/')
            self.assertFalse({'lang-missing', 'lang-invalid', 'lang-declared'} & {i['id'] for i in result})
        mismatch = rendered_supplement({'html': document('<h1>תוכן בעברית לבדיקת שפה וכיווניות</h1>', 'en'),
                                         'run': {}}, [], 'http://localhost/')
        self.assertIn('lang-hebrew-mismatch', {i['id'] for i in mismatch})

    def test_statement_link_preferred_over_toolbar_candidate(self):
        html = document('<a id="toolbar" href="#" role="button" aria-label="נגישות">נגישות</a>'
                        '<a id="statement" href="/accessibility-statement">הצהרת נגישות</a>')
        statement = next(i for i in scan_html(html, {'url': 'http://localhost/'})
                         if i['id'] == 'statement-candidate-found')
        self.assertIn('statement', statement['location']['selector'])
        self.assertEqual(statement['status'], 'human-review-required')
        self.assertIsNone(statement['wcag_criterion'])

    def test_static_structure_is_best_practice(self):
        results = scan_html(document('<h2>תחילת העמוד</h2><h4>פרטים נוספים</h4>'), {'url': 'http://localhost/'})
        for item in results:
            if item['id'] in ('heading-level-skipped', 'bypass-mechanism-not-found'):
                self.assertEqual(item['standards_basis'], 'best-practice')
                self.assertIsNone(item['wcag_criterion'])
                self.assertEqual(item['status'], 'warning')

    def test_axe_skip_link_does_not_duplicate_supplement(self):
        evidence = {'selector': '#skip', 'href': '#content', 'fragment': 'content',
                    'target_exists': False, 'html': '<a id="skip" href="#content">דלג לתוכן</a>'}
        existing = [{'rule_id': 'axe-skip-link', 'status': 'warning', 'location': {'selector': '#skip'}}]
        result = rendered_supplement({'html': fixture(), 'run': {'skip_links': [evidence]}}, existing,
                                    'http://localhost/')
        self.assertNotIn('skip-link-target-missing', {i['id'] for i in result})


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def do_GET(self):
        html = fixture(self.path == '/fixed')
        if self.path == '/structure':
            html = document('<h2>תוכן בעברית</h2><h4>פרטים נוספים</h4><p>פסקת מידע</p>')
        if self.path == '/fragments':
            html = document('<a id="encoded" href="#%D7%AA%D7%95%D7%9B%D7%9F">דלג לתוכן</a>'
                            '<a id="named" href="#legacy">Skip to content</a>'
                            '<a id="ordinary" href="#missing">Product details</a>'
                            '<a id="jump-product" href="#missing">Jump to product details</a>'
                            '<a id="bare-content" href="#missing">לתוכן</a>'
                            '<a id="hidden" hidden href="#missing">דלג לתוכן</a>'
                            '<a id="external" href="/other#missing">דלג לתוכן</a>'
                            '<main id="תוכן"><h1>עמוד בדיקה</h1><a name="legacy"></a></main>')
        payload = html.encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


class RealRenderedCoverageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.node = shutil.which('node')
        if not cls.node:
            raise unittest.SkipTest('Node unavailable; real rendered coverage not tested')
        cls.env = dict(os.environ)
        check = subprocess.run([cls.node, '-e', "require('axe-core');require('playwright').chromium.launch({headless:true}).then(b=>b.close()).catch(()=>process.exit(1))"],
                               cwd=REPO, env=cls.env, capture_output=True, timeout=25)
        if check.returncode:
            raise unittest.SkipTest('Playwright/axe runtime unavailable; real rendered coverage not tested')
        cls.server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = 'http://127.0.0.1:' + str(cls.server.server_port)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=3)

    def adapter(self, route):
        process = subprocess.run([self.node, str(REPO / 'scripts/browser_scan.cjs')],
                                 input=json.dumps({'url': self.base + route, 'timeout_ms': 12000}),
                                 text=True, capture_output=True, env=self.env, cwd=REPO, timeout=17)
        result = json.loads(process.stdout)
        self.assertTrue(result['ok'], result['errors'])
        return result

    def test_five_hebrew_mismatches_fixed_copy_and_extended_name(self):
        broken = self.adapter('/broken')
        findings = axe_findings(broken['axe'], self.base + '/broken')
        mismatch = [i for i in findings if i['rule_id'] == 'axe-label-content-name-mismatch' and i['status'] == 'fail']
        self.assertEqual(len(mismatch), 5)
        self.assertEqual({i['location']['selector'] for i in mismatch},
                         {'#option-0', '#option-1', '#option-2', '#cart-0', '#cart-1'})
        self.assertTrue(all('experimental' in i['axe_tags'] for i in mismatch))
        fixed = self.adapter('/fixed')
        findings = axe_findings(fixed['axe'], self.base + '/fixed')
        self.assertFalse([i for i in findings if i['rule_id'] == 'axe-label-content-name-mismatch' and i['status'] == 'fail'])
        passed = next(i for i in findings if i['rule_id'] == 'axe-label-content-name-mismatch' and i['status'] == 'pass')
        self.assertIn('#expanded', {loc['selector'] for loc in passed['locations']})
        for scanned, expected in ((broken, 'fail'), (fixed, 'pass')):
            supplement = rendered_supplement(scanned, axe_findings(scanned['axe'], self.base), self.base)
            skip = next(i for i in supplement if i.get('rule_id') == 'skip-link-target-missing')
            self.assertEqual(skip['status'], expected)
            self.assertEqual(skip['engine'], 'rendered-dom')

    def test_real_structure_findings_are_recommendations(self):
        scanned = self.adapter('/structure')
        findings = axe_findings(scanned['axe'], self.base)
        for rule in ('heading-order', 'landmark-one-main', 'page-has-heading-one', 'region'):
            item = next(i for i in findings if i['rule_id'] == 'axe-' + rule)
            self.assertEqual(item['status'], 'warning', rule)
            self.assertEqual(item['standards_basis'], 'best-practice', rule)
            self.assertIsNone(item['wcag_criterion'])

    def test_decoded_fragment_and_named_anchor_without_broad_anchor_scan(self):
        scanned = self.adapter('/fragments')
        links = scanned['run']['skip_links']
        self.assertEqual({link['selector'] for link in links}, {'#encoded', '#named'})
        self.assertTrue(all(link['target_exists'] for link in links))


if __name__ == '__main__':
    unittest.main()
