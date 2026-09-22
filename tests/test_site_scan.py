"""Contract tests for the bounded site-wide crawl in scripts/site_scan.py.

Deterministic and offline: pages come from an in-memory fake site, every
sitemap/robots fetch goes through a patched ``site_scan.fetch_bytes`` and the
clock is patched through ``site_scan.monotonic``. No network is ever used.
"""

from pathlib import Path
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import site_scan  # noqa: E402
from site_scan import canonicalize, classify_url, discover_sitemaps, extract_links, parse_sitemap, same_origin, scan_site  # noqa: E402


ORIGIN = 'https://example.test'
START = ORIGIN + '/'
SITE_KEYS = {'discovered', 'scanned', 'failed', 'pending', 'excluded', 'complete', 'stop_reason',
             'limits', 'page_results', 'excluded_urls', 'pending_urls'}
RUN_KEYS = {'status', 'started_at', 'finished_at', 'original_url', 'final_url', 'rendered', 'engines',
            'pages', 'states', 'untested', 'observed_selectors', 'site'}
STOP_REASONS = {'completed', 'page-limit', 'time-limit', 'discovery-limit', 'page-failures', 'no-pages'}


def page(*links, extra=''):
    """Minimal HTML document linking to ``links``."""
    anchors = ''.join('<a href="{}">link</a>'.format(href) for href in links)
    return ('<!doctype html><html lang="en"><head><title>Fixture</title>{}</head>'
            '<body><main><h1>Fixture</h1>{}</main></body></html>').format(extra, anchors)


def urlset(*paths):
    body = ''.join('<url><loc>{}</loc></url>'.format(path if '://' in path else ORIGIN + path) for path in paths)
    return ('<?xml version="1.0" encoding="UTF-8"?>'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{}</urlset>'.format(body)).encode('utf-8')


def sitemapindex(*paths):
    body = ''.join('<sitemap><loc>{}</loc></sitemap>'.format(path if '://' in path else ORIGIN + path)
                   for path in paths)
    return ('<?xml version="1.0" encoding="UTF-8"?>'
            '<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{}</sitemapindex>'
            .format(body)).encode('utf-8')


class FakeSite:
    """In-memory site: {canonical_url: html}. Records every scan_page call."""

    def __init__(self, pages, failing=(), raising=(), aliases=None, clock=None, tick=0.0):
        self.pages = dict(pages)
        self.failing = set(failing)
        self.raising = set(raising)
        self.aliases = dict(aliases or {})
        self.calls = []
        self.returned = []
        self.clock = clock
        self.tick = tick

    def lookup(self, url):
        for candidate in (url, url.rstrip('/'), url + '/'):
            if candidate in self.pages:
                return candidate
        return None

    def scan_page(self, url, timeout_seconds, allowed_origin):
        self.calls.append((url, timeout_seconds, allowed_origin))
        if self.clock is not None:
            self.clock[0] += self.tick
        if url in self.raising:
            raise RuntimeError('browser crashed on ' + url)
        key = self.lookup(url)
        if key is None or url in self.failing:
            result = {'ok': False, 'html': '', 'axe': None,
                      'run': {'original_url': url, 'final_url': None, 'rendered': False},
                      'errors': ['navigation failed for ' + url]}
        else:
            final = self.aliases.get(url, url)
            result = {'ok': True, 'html': self.pages[key], 'axe': {'violations': [], 'page': url},
                      'run': {'original_url': url, 'final_url': final, 'rendered': True,
                              'observed_selectors': {'#main': True}, 'marker': 'keep-me:' + url},
                      'errors': []}
        self.returned.append(result)
        return result

    def scanned_urls(self):
        return [url for url, _budget, _origin in self.calls]


def fetch_404(url, *args, **kwargs):
    return 404, {}, b''


def make_fetch(routes, calls=None):
    """fetch_bytes fake: routes maps URL -> (status, headers, body) or a callable."""
    def fetch_bytes(url, *args, **kwargs):
        if calls is not None:
            calls.append(url)
        route = routes.get(url)
        if route is None:
            return 404, {}, b''
        if callable(route):
            return route(url, *args, **kwargs)
        return route
    return fetch_bytes


def scan(site, start=START, fetch=fetch_404, **kwargs):
    with mock.patch.object(site_scan, 'fetch_bytes', fetch):
        return scan_site(start, site.scan_page, **kwargs)


class ContractCase(unittest.TestCase):
    def assertInvariant(self, outcome):
        site = outcome['run']['site']
        self.assertEqual(site['discovered'], site['scanned'] + site['failed'] + site['pending'],
                         'discovered == scanned + failed + pending violated: {}'.format(
                             {k: site[k] for k in ('discovered', 'scanned', 'failed', 'pending')}))
        for key in ('discovered', 'scanned', 'failed', 'pending', 'excluded'):
            self.assertIsInstance(site[key], int, key)
            self.assertGreaterEqual(site[key], 0, key)
        self.assertIsInstance(site['complete'], bool)
        self.assertIn(site['stop_reason'], STOP_REASONS)
        self.assertEqual(set(site.keys()), SITE_KEYS)
        self.assertLessEqual(len(outcome['results']), site['scanned'])
        for item in site['page_results']:
            self.assertEqual(set(item.keys()), {'url', 'final_url', 'status', 'reason'})
            self.assertIn(item['status'], ('completed', 'failed'))
            self.assertIsInstance(item['url'], str)
            self.assertTrue(item['final_url'] is None or isinstance(item['final_url'], str))
        for item in site['excluded_urls']:
            self.assertEqual(set(item.keys()), {'url', 'reason'})
            self.assertIsInstance(item['reason'], str)
            self.assertTrue(item['reason'])
        for item in site['pending_urls']:
            self.assertIsInstance(item, str)
        self.assertLessEqual(len(site['pending_urls']), site['pending'])
        self.assertGreaterEqual(len([p for p in site['page_results'] if p['status'] == 'completed']), site['scanned'])
        self.assertEqual(len([p for p in site['page_results'] if p['status'] == 'failed']), site['failed'])
        for error in outcome['errors']:
            self.assertIsInstance(error, str)
            self.assertTrue(error)
        return site


class NormalizationTests(unittest.TestCase):
    def test_fragment_is_stripped(self):
        self.assertEqual(canonicalize(ORIGIN + '/about#team'), ORIGIN + '/about')

    def test_default_port_dropped_and_scheme_host_lowercased(self):
        self.assertEqual(canonicalize('HTTPS://Example.TEST:443/About'), ORIGIN + '/About')
        self.assertEqual(canonicalize('http://EXAMPLE.test:80/x'), 'http://example.test/x')
        self.assertEqual(canonicalize(ORIGIN + ':8443/x'), ORIGIN + ':8443/x')

    def test_tracking_params_removed(self):
        self.assertEqual(canonicalize(ORIGIN + '/about?utm_source=x&utm_medium=y&fbclid=1&gclid=2&page=2'),
                         ORIGIN + '/about?page=2')
        only_tracking = canonicalize(ORIGIN + '/about?utm_campaign=spring')
        self.assertEqual(only_tracking, ORIGIN + '/about')
        self.assertFalse(only_tracking.endswith('?'))

    def test_query_params_sorted(self):
        self.assertEqual(canonicalize(ORIGIN + '/s?b=2&a=1'), ORIGIN + '/s?a=1&b=2')
        self.assertEqual(canonicalize(ORIGIN + '/s?b=2&a=1'), canonicalize(ORIGIN + '/s?a=1&b=2'))

    def test_duplicates_collapse(self):
        variants = [ORIGIN + '/about', ORIGIN + '/about#x', 'HTTPS://EXAMPLE.TEST/about',
                    ORIGIN + ':443/about', ORIGIN + '/about?utm_source=a', ORIGIN + '/about?gclid=1#y']
        self.assertEqual(len({canonicalize(url) for url in variants}), 1)

    def test_returns_string_or_none(self):
        self.assertIsInstance(canonicalize(ORIGIN + '/about'), str)
        self.assertIn(type(canonicalize('not a url at all')), (str, type(None)))


class ScopeTests(unittest.TestCase):
    def assertExcluded(self, url, *tokens):
        eligible, reason = classify_url(url, ORIGIN)
        self.assertFalse(eligible, url)
        self.assertIsInstance(reason, str)
        self.assertTrue(reason, 'reason must be recorded for ' + url)
        if tokens:
            self.assertTrue(any(token in reason.lower() for token in tokens),
                            'reason {!r} for {} mentions none of {}'.format(reason, url, tokens))

    def test_same_origin_helper(self):
        self.assertTrue(same_origin(ORIGIN + '/about', ORIGIN))
        self.assertTrue(same_origin('https://EXAMPLE.test/about', ORIGIN))
        self.assertTrue(same_origin(ORIGIN + ':443/about', ORIGIN))
        self.assertFalse(same_origin('https://other.test/about', ORIGIN))
        self.assertFalse(same_origin('http://example.test/about', ORIGIN))
        self.assertFalse(same_origin(ORIGIN + ':8443/about', ORIGIN))
        self.assertFalse(same_origin('https://sub.example.test/about', ORIGIN))

    def test_eligible_same_origin_page(self):
        eligible, _reason = classify_url(ORIGIN + '/about', ORIGIN)
        self.assertTrue(eligible)
        eligible, _reason = classify_url(ORIGIN + '/blog?page=2', ORIGIN)
        self.assertTrue(eligible)

    def test_external_origin_excluded_with_reason(self):
        self.assertExcluded('https://other.test/page')
        self.assertExcluded('http://example.test/page')

    def test_non_http_schemes_excluded(self):
        self.assertExcluded('mailto:hello@example.test')
        self.assertExcluded('tel:+97231234567')
        self.assertExcluded('javascript:void(0)')

    def test_credentials_excluded(self):
        self.assertExcluded('https://user:secret@example.test/page')
        self.assertExcluded('https://user@example.test/page')

    def test_non_html_suffixes_excluded(self):
        for suffix in ('.pdf', '.zip', '.jpg', '.png', '.css', '.js'):
            self.assertExcluded(ORIGIN + '/files/asset' + suffix)

    def test_sensitive_paths_excluded(self):
        for path in ('/login', '/logout', '/admin/dashboard', '/account', '/checkout', '/cart', '/wp-login.php'):
            self.assertExcluded(ORIGIN + path)

    def test_actionful_queries_excluded(self):
        for query in ('?add-to-cart=12', '?logout=1', '?delete=3', '?action=edit'):
            self.assertExcluded(ORIGIN + '/shop' + query)


class LinkExtractionTests(unittest.TestCase):
    def canonical(self, links):
        return {canonicalize(link) for link in links} - {None}

    def test_relative_and_absolute_links_resolved(self):
        html = page('/about', 'team', ORIGIN + '/contact#top')
        links = self.canonical(extract_links(html, ORIGIN + '/company/'))
        self.assertIn(ORIGIN + '/about', links)
        self.assertIn(ORIGIN + '/company/team', links)
        self.assertIn(ORIGIN + '/contact', links)
        self.assertIsInstance(extract_links(html, ORIGIN + '/company/'), list)

    def test_base_href_same_origin_honored(self):
        html = page('intro', extra='<base href="{}/docs/">'.format(ORIGIN))
        links = self.canonical(extract_links(html, ORIGIN + '/company/'))
        self.assertIn(ORIGIN + '/docs/intro', links)
        self.assertNotIn(ORIGIN + '/company/intro', links)

    def test_base_href_external_keeps_browser_semantics(self):
        # An external <base href> is honoured the way a browser would resolve it;
        # the resulting URL is external and must then be excluded, never rewritten
        # into an invented same-origin URL.
        html = page('intro', extra='<base href="https://evil.test/">')
        links = self.canonical(extract_links(html, ORIGIN + '/company/'))
        self.assertIn('https://evil.test/intro', links)
        self.assertNotIn(ORIGIN + '/company/intro', links)
        eligible, reason = classify_url('https://evil.test/intro', ORIGIN)
        self.assertFalse(eligible)
        self.assertIn('external', reason)

    def test_no_links(self):
        self.assertEqual(extract_links(page(), START), [])


class CrawlTests(ContractCase):
    def test_small_site_completes(self):
        site = FakeSite({START: page('/about', '/contact'), ORIGIN + '/about': page('/', '/contact'),
                         ORIGIN + '/contact': page('/')})
        outcome = scan(site, timeout=30, max_pages=100, max_seconds=600)
        run = outcome['run']
        record = self.assertInvariant(outcome)
        self.assertEqual(run['status'], 'completed')
        self.assertTrue(record['complete'])
        self.assertEqual(record['stop_reason'], 'completed')
        self.assertEqual(record['scanned'], 3)
        self.assertEqual(record['failed'], 0)
        self.assertEqual(record['pending'], 0)
        self.assertEqual(record['pending_urls'], [])
        self.assertEqual(outcome['errors'], [])
        self.assertEqual(len(outcome['results']), 3)
        self.assertEqual(sorted(site.scanned_urls()), sorted([START, ORIGIN + '/about', ORIGIN + '/contact']))
        self.assertEqual(record['limits'], {'max_pages': 100, 'max_seconds': 600})
        for url, budget, allowed_origin in site.calls:
            self.assertGreater(budget, 0)
            self.assertLessEqual(budget, 30)
            self.assertIn('example.test', allowed_origin)

    def test_aggregate_run_fields(self):
        site = FakeSite({START: page('/about'), ORIGIN + '/about': page('/')})
        outcome = scan(site)
        run = outcome['run']
        self.assertTrue(RUN_KEYS.issubset(run.keys()), RUN_KEYS - set(run.keys()))
        self.assertEqual(run['observed_selectors'], {})
        self.assertTrue(run['started_at'])
        self.assertTrue(run['finished_at'])
        self.assertEqual(run['original_url'], START)
        self.assertEqual(run['final_url'], site.returned[0]['run']['final_url'])
        self.assertIs(run['rendered'], True)
        self.assertIsInstance(run['engines'], dict)
        self.assertIsInstance(run['states'], list)
        self.assertIsInstance(run['pages'], list)
        self.assertEqual(sorted(run['pages']), sorted(site.scanned_urls()))
        self.assertEqual(sorted(run['pages']), sorted([START, ORIGIN + '/about']))
        self.assertIn('untested', run)
        self.assertEqual(set(run['site'].keys()), SITE_KEYS)
        self.assertEqual(set(run['site']['limits'].keys()), {'max_pages', 'max_seconds'})

    def test_results_preserve_page_run_untouched(self):
        site = FakeSite({START: page('/about'), ORIGIN + '/about': page()})
        outcome = scan(site)
        self.assertEqual(len(outcome['results']), 2)
        for result in outcome['results']:
            self.assertIn(result, site.returned)
            self.assertTrue(result['run']['marker'].startswith('keep-me:'))
            self.assertEqual(result['run']['observed_selectors'], {'#main': True})
            self.assertIs(result['ok'], True)
        self.assertEqual(sorted(r['run']['marker'] for r in outcome['results']),
                         sorted(r['run']['marker'] for r in site.returned if r['ok']))

    def test_discovery_excludes_out_of_scope_links(self):
        external = 'https://other.test/page'
        home = page('/about', external, 'mailto:a@example.test', 'tel:123', 'javascript:void(0)',
                    'https://u:p@example.test/secret', '/brochure.pdf', '/login', '/cart', '/admin',
                    '/shop?add-to-cart=5', '/item?action=delete')
        site = FakeSite({START: home, ORIGIN + '/about': page('/')})
        outcome = scan(site)
        record = self.assertInvariant(outcome)
        self.assertEqual(outcome['run']['status'], 'completed')
        self.assertTrue(record['complete'])
        self.assertEqual(record['scanned'], 2)
        self.assertEqual(record['discovered'], 2)
        self.assertGreaterEqual(record['excluded'], 8)
        self.assertGreaterEqual(len(record['excluded_urls']), 1)
        excluded_urls = [item['url'] for item in record['excluded_urls']]
        self.assertTrue(any('other.test' in url for url in excluded_urls))
        for url in site.scanned_urls():
            self.assertTrue(url.startswith(ORIGIN + '/'))
            self.assertNotIn('other.test', url)
            for banned in ('/login', '/cart', '/admin', '.pdf', 'add-to-cart', 'action=', 'mailto', 'tel:', '@'):
                self.assertNotIn(banned, url)

    def test_duplicate_link_variants_scanned_once(self):
        home = page('/about', '/about#team', '/about?utm_source=news&fbclid=1', 'HTTPS://EXAMPLE.TEST/about',
                    ORIGIN + ':443/about', '/about?gclid=9')
        site = FakeSite({START: home, ORIGIN + '/about': page('/')})
        outcome = scan(site)
        record = self.assertInvariant(outcome)
        self.assertEqual(site.scanned_urls().count(ORIGIN + '/about'), 1)
        self.assertEqual(record['scanned'], 2)
        self.assertEqual(record['discovered'], 2)
        self.assertTrue(record['complete'])

    def test_page_limit(self):
        pages = {START: page('/p1', '/p2', '/p3', '/p4')}
        for index in range(1, 5):
            pages[ORIGIN + '/p{}'.format(index)] = page('/')
        site = FakeSite(pages)
        outcome = scan(site, max_pages=2)
        record = self.assertInvariant(outcome)
        self.assertEqual(record['stop_reason'], 'page-limit')
        self.assertFalse(record['complete'])
        self.assertEqual(record['scanned'], 2)
        self.assertGreaterEqual(record['pending'], 1)
        self.assertGreaterEqual(len(record['pending_urls']), 1)
        self.assertGreaterEqual(len(outcome['errors']), 1)
        self.assertEqual(outcome['run']['status'], 'partial')
        self.assertEqual(record['limits']['max_pages'], 2)
        self.assertEqual(len(site.calls), 2)

    def test_exact_page_limit_with_empty_queue_is_completed(self):
        site = FakeSite({START: page('/about'), ORIGIN + '/about': page('/')})
        outcome = scan(site, max_pages=2)
        record = self.assertInvariant(outcome)
        self.assertEqual(record['stop_reason'], 'completed')
        self.assertTrue(record['complete'])
        self.assertEqual(outcome['run']['status'], 'completed')
        self.assertEqual(outcome['errors'], [])
        self.assertEqual(record['scanned'], 2)
        self.assertEqual(record['pending'], 0)

    def test_time_limit_via_patched_monotonic(self):
        clock = [1000.0]
        pages = {START: page('/p1', '/p2', '/p3')}
        for index in range(1, 4):
            pages[ORIGIN + '/p{}'.format(index)] = page('/')
        site = FakeSite(pages, clock=clock, tick=100.0)
        with mock.patch.object(site_scan, 'monotonic', lambda: clock[0]):
            outcome = scan(site, max_seconds=5)
        record = self.assertInvariant(outcome)
        self.assertEqual(record['stop_reason'], 'time-limit')
        self.assertFalse(record['complete'])
        self.assertGreaterEqual(record['scanned'], 1)
        self.assertLess(record['scanned'], 4)
        self.assertGreaterEqual(record['pending'], 1)
        self.assertGreaterEqual(len(record['pending_urls']), 1)
        self.assertGreaterEqual(len(outcome['errors']), 1)
        self.assertEqual(outcome['run']['status'], 'partial')
        self.assertEqual(record['limits']['max_seconds'], 5)

    def test_redirect_alias_not_scanned_twice(self):
        about = ORIGIN + '/about'
        old = ORIGIN + '/old-about'
        site = FakeSite({START: page('/about'), about: page('/', '/old-about'), old: page('/')},
                        aliases={old: about})
        outcome = scan(site)
        record = self.assertInvariant(outcome)
        self.assertEqual(site.scanned_urls().count(about), 1)
        final_urls = [result['run']['final_url'] for result in outcome['results']]
        self.assertEqual(len(final_urls), len(set(final_urls)), 'audited URLs duplicated: {}'.format(final_urls))
        self.assertEqual(final_urls.count(about), 1)
        recorded = [item['url'] for item in record['excluded_urls']] + [item['url'] for item in record['page_results']]
        self.assertIn(old, recorded)
        self.assertEqual(record['failed'], 0)
        self.assertEqual(record['pending'], 0)

    def test_page_failure_is_partial(self):
        missing = ORIGIN + '/missing'
        site = FakeSite({START: page('/about', '/missing'), ORIGIN + '/about': page('/')})
        outcome = scan(site)
        record = self.assertInvariant(outcome)
        self.assertEqual(record['failed'], 1)
        self.assertEqual(record['scanned'], 2)
        self.assertEqual(record['pending'], 0)
        self.assertFalse(record['complete'])
        self.assertEqual(record['stop_reason'], 'page-failures')
        self.assertEqual(outcome['run']['status'], 'partial')
        self.assertGreaterEqual(len(outcome['errors']), 1)
        self.assertEqual(len(outcome['results']), 2)
        self.assertTrue(all(result['ok'] for result in outcome['results']))
        failed = [item for item in record['page_results'] if item['status'] == 'failed']
        self.assertEqual([item['url'] for item in failed], [missing])
        self.assertTrue(failed[0]['reason'])

    def test_scan_page_exception_counts_as_failure_and_crawl_continues(self):
        broken = ORIGIN + '/broken'
        site = FakeSite({START: page('/broken', '/about'), broken: page('/'), ORIGIN + '/about': page('/', '/contact'),
                         ORIGIN + '/contact': page('/')}, raising={broken})
        outcome = scan(site)
        record = self.assertInvariant(outcome)
        failed = [item for item in record['page_results'] if item['status'] == 'failed']
        self.assertEqual([item['url'] for item in failed], [broken])
        self.assertTrue(failed[0]['reason'])
        self.assertEqual(record['failed'], 1)
        self.assertEqual(record['scanned'], 3)
        self.assertEqual(sorted(url for url in site.scanned_urls() if url != broken),
                         sorted([START, ORIGIN + '/about', ORIGIN + '/contact']))
        self.assertGreaterEqual(len(outcome['errors']), 1)
        self.assertEqual(outcome['run']['status'], 'partial')
        self.assertFalse(record['complete'])
        self.assertEqual(len(outcome['results']), 3)
        self.assertEqual(sorted(outcome['run']['pages']), sorted([START, ORIGIN + '/about', ORIGIN + '/contact']))

    def test_no_successful_page(self):
        site = FakeSite({}, failing={START})
        outcome = scan(site)
        record = self.assertInvariant(outcome)
        self.assertEqual(outcome['run']['status'], 'not-performed')
        self.assertEqual(record['stop_reason'], 'no-pages')
        self.assertEqual(outcome['results'], [])
        self.assertEqual(record['scanned'], 0)
        self.assertEqual(record['failed'], 1)
        self.assertFalse(record['complete'])
        self.assertGreaterEqual(len(outcome['errors']), 1)
        self.assertIs(outcome['run']['rendered'], False)
        self.assertEqual(outcome['run']['observed_selectors'], {})
        self.assertEqual(outcome['run']['pages'], [])

    def test_discovery_queue_cap_truncates(self):
        count = 11000
        home = page(*['/p{}'.format(index) for index in range(count)])
        pages = {START: home}
        pages.update({ORIGIN + '/p{}'.format(index): '<html><body></body></html>' for index in range(count)})
        site = FakeSite(pages)
        outcome = scan(site, max_pages=count + 10, max_seconds=10_000)
        record = self.assertInvariant(outcome)
        self.assertFalse(record['complete'])
        self.assertEqual(record['stop_reason'], 'discovery-limit')
        self.assertGreaterEqual(len(outcome['errors']), 1)
        self.assertLess(record['discovered'], count)
        self.assertGreater(record['scanned'], 100)


class SitemapParsingTests(unittest.TestCase):
    def test_urlset_namespaced_and_plain(self):
        parsed = parse_sitemap(urlset('/about', '/contact'))
        self.assertFalse(parsed['error'])
        self.assertEqual(sorted(parsed['urls']), [ORIGIN + '/about', ORIGIN + '/contact'])
        self.assertEqual(parsed['sitemaps'], [])
        plain = b'<urlset><url><loc>' + ORIGIN.encode() + b'/plain</loc></url></urlset>'
        parsed = parse_sitemap(plain)
        self.assertFalse(parsed['error'])
        self.assertEqual(parsed['urls'], [ORIGIN + '/plain'])

    def test_sitemapindex(self):
        parsed = parse_sitemap(sitemapindex('/a.xml', '/b.xml'))
        self.assertFalse(parsed['error'])
        self.assertEqual(sorted(parsed['sitemaps']), [ORIGIN + '/a.xml', ORIGIN + '/b.xml'])
        self.assertEqual(parsed['urls'], [])

    def test_doctype_and_entities_rejected(self):
        evil = (b'<?xml version="1.0"?><!DOCTYPE urlset [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>'
                b'<urlset><url><loc>' + ORIGIN.encode() + b'/&xxe;</loc></url></urlset>')
        parsed = parse_sitemap(evil)
        self.assertTrue(parsed['error'])
        self.assertEqual(parsed['urls'], [])
        self.assertEqual(parsed['sitemaps'], [])
        doctype_only = b'<!DOCTYPE urlset SYSTEM "http://evil.test/x.dtd"><urlset></urlset>'
        self.assertTrue(parse_sitemap(doctype_only)['error'])

    def test_malformed_xml(self):
        parsed = parse_sitemap(b'<urlset><url><loc>' + ORIGIN.encode() + b'/x</loc>')
        self.assertTrue(parsed['error'])
        self.assertEqual(parsed['urls'], [])
        self.assertEqual(set(parsed.keys()), {'urls', 'sitemaps', 'error'})


def file_count(result):
    files = result['files']
    return files if isinstance(files, int) else len(files)


class SitemapDiscoveryTests(unittest.TestCase):
    def discover(self, routes):
        calls = []
        with mock.patch.object(site_scan, 'fetch_bytes', make_fetch(routes, calls)):
            result = discover_sitemaps(ORIGIN, 10)
        self.assertTrue({'urls', 'warnings', 'truncated', 'files'}.issubset(result.keys()), result.keys())
        self.assertIsInstance(result['urls'], list)
        self.assertIsInstance(result['warnings'], list)
        self.assertIsInstance(result['truncated'], bool)
        return result, calls

    def test_missing_sitemap_is_not_a_failure(self):
        result, calls = self.discover({})
        self.assertEqual(result['urls'], [])
        self.assertFalse(result['truncated'])
        self.assertTrue(all(url.startswith(ORIGIN) for url in calls))

    def test_root_sitemap_urls(self):
        result, _calls = self.discover({ORIGIN + '/sitemap.xml': (200, {'content-type': 'application/xml'},
                                                                  urlset('/about', '/contact'))})
        self.assertEqual(sorted(result['urls']), [ORIGIN + '/about', ORIGIN + '/contact'])
        self.assertFalse(result['truncated'])
        self.assertGreaterEqual(file_count(result), 1)

    def test_robots_sitemap_lines(self):
        robots = ('User-agent: *\nDisallow: /admin\nSitemap: {0}/sitemap-pages.xml\n'
                  'Sitemap: {0}/sitemap-posts.xml\nSitemap: https://other.test/sitemap.xml\n').format(ORIGIN)
        result, calls = self.discover({
            ORIGIN + '/robots.txt': (200, {'content-type': 'text/plain'}, robots.encode('utf-8')),
            ORIGIN + '/sitemap-pages.xml': (200, {'content-type': 'application/xml'}, urlset('/about')),
            ORIGIN + '/sitemap-posts.xml': (200, {'content-type': 'application/xml'}, urlset('/post-1')),
        })
        self.assertIn(ORIGIN + '/about', result['urls'])
        self.assertIn(ORIGIN + '/post-1', result['urls'])
        self.assertFalse(any(url.startswith('https://other.test') for url in calls))

    def test_forbidden_root_sitemap_warns(self):
        result, _calls = self.discover({ORIGIN + '/sitemap.xml': (403, {}, b'forbidden')})
        self.assertGreaterEqual(len(result['warnings']), 1)
        self.assertEqual(result['urls'], [])

    def test_sitemapindex_recursion(self):
        result, _calls = self.discover({
            ORIGIN + '/sitemap.xml': (200, {}, sitemapindex('/sitemap-a.xml', '/sitemap-b.xml')),
            ORIGIN + '/sitemap-a.xml': (200, {}, urlset('/a1', '/a2')),
            ORIGIN + '/sitemap-b.xml': (200, {}, sitemapindex('/sitemap-c.xml')),
            ORIGIN + '/sitemap-c.xml': (200, {}, urlset('/c1')),
        })
        self.assertEqual(sorted(result['urls']), [ORIGIN + '/a1', ORIGIN + '/a2', ORIGIN + '/c1'])
        self.assertGreaterEqual(file_count(result), 4)

    def test_sitemap_loop_is_safe(self):
        result, calls = self.discover({
            ORIGIN + '/sitemap.xml': (200, {}, sitemapindex('/sitemap.xml', '/sitemap-a.xml')),
            ORIGIN + '/sitemap-a.xml': (200, {}, sitemapindex('/sitemap.xml', '/sitemap-a.xml', '/leaf.xml')),
            ORIGIN + '/leaf.xml': (200, {}, urlset('/leaf')),
        })
        self.assertIn(ORIGIN + '/leaf', result['urls'])
        self.assertLessEqual(len(calls), 25)

    def test_depth_limit_truncates(self):
        routes = {ORIGIN + '/sitemap.xml': (200, {}, sitemapindex('/level1.xml'))}
        for level in range(1, 8):
            routes[ORIGIN + '/level{}.xml'.format(level)] = (200, {}, sitemapindex('/level{}.xml'.format(level + 1)))
        routes[ORIGIN + '/level8.xml'] = (200, {}, urlset('/deep'))
        result, _calls = self.discover(routes)
        self.assertTrue(result['truncated'])
        self.assertNotIn(ORIGIN + '/deep', result['urls'])

    def test_file_limit_truncates(self):
        children = ['/child{}.xml'.format(index) for index in range(40)]
        routes = {ORIGIN + '/sitemap.xml': (200, {}, sitemapindex(*children))}
        for index, child in enumerate(children):
            routes[ORIGIN + child] = (200, {}, urlset('/page{}'.format(index)))
        result, calls = self.discover(routes)
        self.assertTrue(result['truncated'])
        xml_calls = [url for url in calls if url.endswith('.xml')]
        self.assertLessEqual(len(xml_calls), 22)
        self.assertLess(len(result['urls']), 40)

    def test_oversized_sitemap_rejected(self):
        def big(url, *args, **kwargs):
            max_bytes = kwargs.get('max_bytes', args[1] if len(args) > 1 else 2_000_000)
            body = urlset(*['/big{}'.format(index) for index in range(60_000)])
            self.assertGreater(len(body), max_bytes)
            return 200, {'content-type': 'application/xml'}, body[:max_bytes]
        result, _calls = self.discover({ORIGIN + '/sitemap.xml': big})
        self.assertTrue(result['warnings'] or result['truncated'])
        self.assertLess(len(result['urls']), 60_000)

    def test_same_origin_redirect_followed_hop_by_hop(self):
        result, calls = self.discover({
            ORIGIN + '/sitemap.xml': (301, {'location': ORIGIN + '/sitemap-real.xml'}, b''),
            ORIGIN + '/sitemap-real.xml': (200, {}, urlset('/real')),
        })
        self.assertEqual(result['urls'], [ORIGIN + '/real'])
        self.assertIn(ORIGIN + '/sitemap-real.xml', calls)

    def test_redirect_to_external_origin_not_followed(self):
        result, calls = self.discover({
            ORIGIN + '/sitemap.xml': (302, {'location': 'https://other.test/sitemap.xml'}, b''),
            'https://other.test/sitemap.xml': (200, {}, urlset('https://other.test/leak')),
        })
        self.assertFalse(any(url.startswith('https://other.test') for url in calls))
        self.assertEqual(result['urls'], [])

    def test_redirect_loop_bounded(self):
        _result, calls = self.discover({ORIGIN + '/sitemap.xml': (301, {'location': ORIGIN + '/sitemap.xml'}, b'')})
        self.assertLessEqual(len([url for url in calls if url.endswith('sitemap.xml')]), 6)

    def test_external_sitemap_urls_never_included(self):
        result, calls = self.discover({ORIGIN + '/sitemap.xml': (200, {}, sitemapindex('https://other.test/s.xml')),
                                       'https://other.test/s.xml': (200, {}, urlset('https://other.test/leak'))})
        self.assertFalse(any(url.startswith('https://other.test') for url in calls))
        self.assertEqual(result['urls'], [])

    def test_fetch_exception_is_a_warning_not_a_crash(self):
        def boom(url, *args, **kwargs):
            raise OSError('connection refused')
        result, _calls = self.discover({ORIGIN + '/sitemap.xml': boom, ORIGIN + '/robots.txt': boom})
        self.assertEqual(result['urls'], [])
        self.assertIsInstance(result['warnings'], list)


class SitemapCrawlIntegrationTests(ContractCase):
    def test_sitemap_urls_join_the_crawl(self):
        site = FakeSite({START: page(), ORIGIN + '/contact': page('/')})
        fetch = make_fetch({ORIGIN + '/sitemap.xml': (200, {}, urlset('/contact', 'https://other.test/x'))})
        outcome = scan(site, fetch=fetch)
        record = self.assertInvariant(outcome)
        self.assertIn(ORIGIN + '/contact', site.scanned_urls())
        self.assertFalse(any('other.test' in url for url in site.scanned_urls()))
        self.assertEqual(record['scanned'], 2)
        self.assertTrue(record['complete'])
        self.assertEqual(outcome['run']['status'], 'completed')
        self.assertEqual(outcome['errors'], [])

    def test_missing_sitemap_keeps_crawl_complete(self):
        site = FakeSite({START: page('/about'), ORIGIN + '/about': page('/')})
        outcome = scan(site, fetch=fetch_404)
        record = self.assertInvariant(outcome)
        self.assertTrue(record['complete'])
        self.assertEqual(outcome['errors'], [])
        self.assertEqual(outcome['run']['status'], 'completed')

    def test_forbidden_root_sitemap_makes_crawl_partial(self):
        site = FakeSite({START: page('/about'), ORIGIN + '/about': page('/')})
        outcome = scan(site, fetch=make_fetch({ORIGIN + '/sitemap.xml': (403, {}, b'')}))
        record = self.assertInvariant(outcome)
        self.assertFalse(record['complete'])
        self.assertGreaterEqual(len(outcome['errors']), 1)
        self.assertEqual(outcome['run']['status'], 'partial')
        self.assertEqual(record['scanned'], 2)

    def test_doctype_sitemap_makes_crawl_partial(self):
        evil = (b'<?xml version="1.0"?><!DOCTYPE urlset [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>'
                b'<urlset><url><loc>' + ORIGIN.encode() + b'/injected</loc></url></urlset>')
        site = FakeSite({START: page('/about'), ORIGIN + '/about': page('/'), ORIGIN + '/injected': page()})
        outcome = scan(site, fetch=make_fetch({ORIGIN + '/sitemap.xml': (200, {}, evil)}))
        record = self.assertInvariant(outcome)
        self.assertFalse(record['complete'])
        self.assertGreaterEqual(len(outcome['errors']), 1)
        self.assertEqual(outcome['run']['status'], 'partial')
        self.assertNotIn(ORIGIN + '/injected', site.scanned_urls())



def advancing_clock(step=5.0):
    """Fake ``monotonic``: every call moves the clock forward by ``step`` seconds."""
    state = [0.0]

    def fake_monotonic():
        state[0] += step
        return state[0]
    return fake_monotonic


def many_sitemaps(count=40):
    children = ['/child{}.xml'.format(index) for index in range(count)]
    routes = {ORIGIN + '/sitemap.xml': (200, {'content-type': 'application/xml'}, sitemapindex(*children))}
    for index, child in enumerate(children):
        routes[ORIGIN + child] = (200, {'content-type': 'application/xml'}, urlset('/page{}'.format(index)))
    return routes


class SitemapTimeBudgetTests(ContractCase):
    def test_scan_site_sitemap_discovery_honours_time_budget(self):
        calls = []
        pages = {START: page()}
        pages.update({ORIGIN + '/page{}'.format(index): page() for index in range(40)})
        site = FakeSite(pages)
        with mock.patch.object(site_scan, 'monotonic', advancing_clock(5.0)):
            outcome = scan(site, fetch=make_fetch(many_sitemaps(40), calls), max_seconds=12)
        record = self.assertInvariant(outcome)
        self.assertLess(len(calls), 10, 'sitemap fetches ignored the time budget: {}'.format(len(calls)))
        self.assertFalse(record['complete'])
        self.assertGreaterEqual(len(outcome['errors']), 1)
        self.assertTrue(any(any(token in error.lower() for token in ('time', 'budget', 'truncat', 'discover'))
                            for error in outcome['errors']), outcome['errors'])

    def test_discover_sitemaps_honours_time_budget(self):
        calls = []
        with mock.patch.object(site_scan, 'monotonic', advancing_clock(5.0)):
            with mock.patch.object(site_scan, 'fetch_bytes', make_fetch(many_sitemaps(40), calls)):
                result = discover_sitemaps(ORIGIN, timeout=1)
        self.assertIs(result['truncated'], True)
        self.assertGreaterEqual(len(result['warnings']), 1)
        self.assertLess(len(calls), 20)


    def test_discovery_makes_progress_before_budget_stops_it(self):
        calls = []
        pages = {START: page()}
        pages.update({ORIGIN + '/page{}'.format(index): page() for index in range(40)})
        site = FakeSite(pages)
        with mock.patch.object(site_scan, 'monotonic', advancing_clock(0.5)):
            outcome = scan(site, fetch=make_fetch(many_sitemaps(40), calls), max_seconds=12)
        record = self.assertInvariant(outcome)
        self.assertGreaterEqual(len(calls), 1, 'a finer clock must allow at least one sitemap fetch')
        self.assertLess(len(calls), 20)
        self.assertFalse(record['complete'])
        self.assertGreaterEqual(len(outcome['errors']), 1)


class SitemapEncodingBoundaryTests(unittest.TestCase):
    def test_utf16_doctype_rejected(self):
        text = ('<?xml version="1.0" encoding="utf-16"?>'
                '<!DOCTYPE urlset [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>'
                '<urlset><url><loc>{}/&xxe;</loc></url><url><loc>{}/utf16</loc></url></urlset>'.format(ORIGIN, ORIGIN))
        parsed = parse_sitemap(text.encode('utf-16'))
        self.assertTrue(parsed['error'])
        self.assertEqual(parsed['urls'], [])
        self.assertEqual(parsed['sitemaps'], [])

    def test_nul_bytes_rejected(self):
        data = b'<urlset><url><loc>' + ORIGIN.encode() + b'/nul\x00page</loc></url></urlset>'
        parsed = parse_sitemap(data)
        self.assertTrue(parsed['error'])
        self.assertEqual(parsed['urls'], [])
        data = b'<url\x00set><url><loc>' + ORIGIN.encode() + b'/x</loc></url></urlset>'
        parsed = parse_sitemap(data)
        self.assertTrue(parsed['error'])
        self.assertEqual(parsed['urls'], [])


class PercentEncodedPathTests(unittest.TestCase):
    def test_percent_encoded_actionful_paths_excluded(self):
        for url in (ORIGIN + '/%6cogout', ORIGIN + '/wp-%61dmin/'):
            eligible, reason = classify_url(url, ORIGIN)
            self.assertFalse(eligible, url)
            self.assertIn('actionful', reason.lower(), (url, reason))

    def test_plain_actionful_paths_still_excluded(self):
        eligible, reason = classify_url(ORIGIN + '/logout', ORIGIN)
        self.assertFalse(eligible)
        self.assertIn('actionful', reason.lower(), reason)


if __name__ == '__main__':
    unittest.main()
