"""A partial crawl cannot become a successful empty accessibility result."""
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import audit


class SiteCliTests(unittest.TestCase):
    def run_audit(self, args):
        with tempfile.TemporaryDirectory() as directory:
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                status = audit.main([*args, '--output', directory])
            return status, json.loads((Path(directory) / 'accessibility-report.json').read_text())

    def test_incomplete_site_without_error_message_still_exits_two(self):
        module = types.ModuleType('site_scan')
        module.scan_site = lambda *args, **kwargs: {
            'results': [], 'errors': [],
            'run': {'status': 'partial', 'site': {'complete': False}},
        }
        with patch.dict(sys.modules, {'site_scan': module}):
            status, report = self.run_audit(['--site', 'https://example.test'])
        self.assertEqual(status, 2)
        self.assertTrue(report['errors'])
        self.assertEqual(report['metadata']['run']['status'], 'not-performed')

    def test_invalid_limits_stop_before_any_browser_or_network_work(self):
        for args in (['--max-pages', '0'], ['--max-seconds', 'nan'], ['--max-seconds', '4']):
            with self.subTest(args=args), patch.object(audit, 'rendered_scan') as browser:
                status, report = self.run_audit(['--site', 'https://example.test', *args])
                self.assertEqual(status, 2)
                self.assertEqual(report['metadata']['run']['status'], 'not-performed')
                browser.assert_not_called()

    def test_static_site_combination_is_rejected_instead_of_silently_downgraded(self):
        status, report = self.run_audit(['--site', 'https://example.test', '--static'])
        self.assertEqual(status, 2)
        self.assertEqual(report['summary']['files_scanned'], 0)

    def test_existing_static_flag_on_source_scan_remains_compatible(self):
        fixture = Path(__file__).parent / 'fixtures' / 'accessible.html'
        status, report = self.run_audit(['--path', str(fixture), '--static'])
        self.assertIn(status, (0, 1))
        self.assertEqual(report['summary']['files_scanned'], 1)
        self.assertEqual(report['errors'], [])


if __name__ == '__main__':
    unittest.main()
