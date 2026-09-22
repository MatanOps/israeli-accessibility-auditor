"""Live, two-origin regressions for the optional browser navigation boundary."""

from collections import Counter
import http.server
import json
import os
from pathlib import Path
import shutil
import subprocess
import threading
import unittest


REPO = Path(__file__).resolve().parents[1]
NODE = shutil.which('node')
HTML = ('<!doctype html><html lang="en"><head><title>Navigation fixture</title></head>'
        '<body><main><h1>Navigation fixture</h1><p>Visible local content for auditing.</p>'
        '{content}</main></body></html>')


class NavigationHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def do_GET(self):
        with self.server.count_lock:
            self.server.counts[self.path] += 1
        redirects = {'/same': '/ready', '/outside': self.server.other + '/forbidden',
                     '/chain': '/outside', '/unguarded': self.server.other + '/legacy',
                     '/frame-redirect': self.server.other + '/frame-destination'}
        if self.path in redirects:
            self.send_response(302)
            self.send_header('Location', redirects[self.path])
            self.send_header('Content-Length', '0')
            self.end_headers()
            return
        content = ''
        if self.path == '/script':
            content = '<script>location.href=' + json.dumps(self.server.other + '/script-forbidden') + '</script>'
        elif self.path == '/late':
            content = ('<script>setTimeout(()=>{location.href='
                       + json.dumps(self.server.other + '/late-forbidden') + '},100);</script>')
        elif self.path == '/meta':
            content = '<meta http-equiv="refresh" content="0;url=' + self.server.other + '/meta-forbidden">'
        elif self.path == '/popup':
            content = '<script>window.open(' + json.dumps(self.server.other + '/popup-forbidden') + ');</script>'
        elif self.path == '/resources':
            content = ('<script src="' + self.server.other + '/asset.js"></script>'
                       '<iframe title="External fixture" src="' + self.server.other + '/frame"></iframe>'
                       '<iframe title="Redirected fixture" src="/frame-redirect"></iframe>')
        body = HTML.format(content=content).encode('utf-8')
        mime = 'text/html; charset=utf-8'
        if self.path == '/asset.js':
            mime, body = 'application/javascript', b'window.fixtureAssetLoaded = true;'
        self.send_response(200)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass


@unittest.skipUnless(NODE, 'Node unavailable; navigation adapter not tested')
class NavigationValidationTests(unittest.TestCase):
    def test_invalid_origins_fail_before_navigation(self):
        for origin in (None, 0, False, [], {}, '', 'null', 'file:///tmp',
                       'ftp://example.org', 'https://user:pass@example.org',
                       'https://example.org/path', 'https://example.org/?query',
                       'https://example.org/#fragment', 'https://example.org?',
                       ' https://example.org', 'https:example.org'):
            with self.subTest(origin=origin):
                result = subprocess.run(
                    [NODE, str(REPO / 'scripts/browser_scan.cjs')],
                    input=json.dumps({'url': 'http://127.0.0.1:1/', 'allowed_origin': origin}),
                    text=True, capture_output=True, timeout=5, cwd=REPO,
                )
                data = json.loads(result.stdout)
                self.assertFalse(data['ok'])
                self.assertEqual(data['run']['status'], 'not-performed')
                self.assertEqual(data['run']['attempts'], [])
                self.assertTrue(any('invalid allowed_origin' in error for error in data['errors']))


class SiteNavigationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.env = dict(os.environ)
        if not NODE:
            raise unittest.SkipTest('Node unavailable; live navigation boundary not tested')
        check = subprocess.run(
            [NODE, '-e', "require('axe-core');require('playwright').chromium.launch({headless:true})"
             ".then(b=>b.close()).catch(e=>{console.error(e.message);process.exit(1)})"],
            cwd=REPO, env=cls.env, capture_output=True, text=True, timeout=25,
        )
        if check.returncode:
            raise unittest.SkipTest('Browser runtime unavailable: ' + check.stderr[-300:])
        cls.servers, cls.threads = [], []
        for _ in range(2):
            server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), NavigationHandler)
            server.counts, server.count_lock = Counter(), threading.Lock()
            cls.servers.append(server)
        cls.base, cls.other = ['http://127.0.0.1:' + str(server.server_port) for server in cls.servers]
        cls.servers[0].other, cls.servers[1].other = cls.other, cls.base
        for server in cls.servers:
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            cls.threads.append(thread)

    @classmethod
    def tearDownClass(cls):
        for server, thread in zip(cls.servers, cls.threads):
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)

    def adapter(self, path, guarded=True, origin=None):
        request = {'url': self.base + path, 'timeout_ms': 12000}
        if guarded:
            request['allowed_origin'] = origin or self.base
        result = subprocess.run(
            [NODE, str(REPO / 'scripts/browser_scan.cjs')], input=json.dumps(request),
            cwd=REPO, env=self.env, text=True, capture_output=True, timeout=17,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def assert_blocked(self, result):
        self.assertFalse(result['ok'], result)
        self.assertEqual(result['run']['status'], 'not-performed')
        self.assertFalse(result['run']['rendered'])
        self.assertEqual(result['run']['readiness']['verdict'], 'navigation-blocked')
        self.assertEqual(result['html'], '')
        for bucket in ('violations', 'incomplete', 'passes', 'inapplicable'):
            self.assertEqual(result['axe'][bucket], [])
        self.assertEqual(result['axe']['testEngine'], {})
        self.assertTrue(result['run']['blocked_navigation'])
        self.assertTrue(any('allowed_origin' in error for error in result['errors']))

    def test_same_origin_redirect_is_audited(self):
        result = self.adapter('/same', origin=self.base + '/')
        self.assertTrue(result['ok'], result['errors'])
        self.assertEqual(result['run']['status'], 'completed')
        self.assertEqual(result['run']['final_url'], self.base + '/ready')
        self.assertEqual(result['run']['allowed_origin'], self.base)
        self.assertTrue(result['axe']['testEngine'])

    def test_other_origin_redirect_is_never_requested_even_after_same_origin_hop(self):
        for path in ('/outside', '/chain'):
            with self.subTest(path=path):
                self.assert_blocked(self.adapter(path))
                self.assertEqual(self.servers[1].counts['/forbidden'], 0)

    def test_script_and_meta_navigations_are_blocked(self):
        for path in ('/script', '/late', '/meta'):
            with self.subTest(path=path):
                self.assert_blocked(self.adapter(path))
                self.assertEqual(self.servers[1].counts[path + '-forbidden'], 0)

    def test_cross_origin_assets_and_iframes_remain_allowed(self):
        result = self.adapter('/resources')
        self.assertTrue(result['ok'], result['errors'])
        self.assertEqual(result['run']['status'], 'completed')
        for path in ('/asset.js', '/frame', '/frame-destination'):
            self.assertGreater(self.servers[1].counts[path], 0, path)

    def test_omitting_guard_preserves_cross_origin_redirect_behavior(self):
        result = self.adapter('/unguarded', guarded=False)
        self.assertTrue(result['ok'], result['errors'])
        self.assertEqual(result['run']['status'], 'completed')
        self.assertEqual(result['run']['final_url'], self.other + '/legacy')
        self.assertGreater(self.servers[1].counts['/legacy'], 0)
        self.assertNotIn('allowed_origin', result['run'])

    def test_page_script_cannot_open_an_unguarded_popup(self):
        result = self.adapter('/popup')
        self.assertTrue(result['ok'], result['errors'])
        self.assertEqual(self.servers[1].counts['/popup-forbidden'], 0)

    def test_mismatched_initial_origin_is_rejected_without_any_request(self):
        result = self.adapter('/initial-never-requested', origin=self.other)
        self.assert_blocked(result)
        self.assertEqual(result['run']['attempts'], [])
        self.assertEqual(self.servers[0].counts['/initial-never-requested'], 0)


if __name__ == '__main__':
    unittest.main(verbosity=2)
