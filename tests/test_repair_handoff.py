"""The copied browser packet and Python export must carry the same evidence."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from repair_packet import build_packet, browser_script


class RepairHandoffTests(unittest.TestCase):
    def test_browser_and_python_export_the_same_selected_evidence(self):
        node = shutil.which('node')
        if not node:
            self.skipTest('Node is required for browser packet parity')
        for status in ('fail', 'warning', 'human-review-required', 'not-tested'):
            with self.subTest(status=status):
                report = {'findings': [
                    {'id': 'selected', 'stable_id': 'stable-selected', 'status': status,
                     'evidence_type': 'human-verification-required', 'engine': 'axe',
                     'evidence': 'SELECTED <script>example</script> ```'},
                    {'id': 'excluded', 'evidence': 'EXCLUDED_EVIDENCE'},
                ]}
                expected = build_packet(report, ['stable-selected'])
                script = 'global.window = {};\n' + browser_script()
                script += '\nprocess.stdout.write(window.RepairPack.build(' + json.dumps(report) + ', ["stable-selected"]));'
                actual = subprocess.check_output([node, '-e', script], text=True)
                self.assertEqual(actual, expected)
                self.assertNotIn('EXCLUDED_EVIDENCE', actual)
                self.assertIn('ראיית מעבר מפורשת', actual)
                self.assertIn('היעלמות ממצא לבדה אינה תיקון', actual)

    def test_unknown_selection_does_not_authorize_an_unselected_finding(self):
        packet = build_packet({'findings': [{'id': 'known', 'evidence': 'NOT_SELECTED'}]}, ['unknown'])
        self.assertNotIn('NOT_SELECTED', packet)
        self.assertIn('לא נבחר אף ממצא', packet)
