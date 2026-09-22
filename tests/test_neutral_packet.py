"""The repair packet stays neutral and Hebrew even for legacy saved reports."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from repair_packet import build_packet, browser_script

LEGACY_ENGLISH_DISCLAIMER = (
    'This report is technical assistance produced by scoped automated checks. '
    'It is not an accessibility certificate, a legal opinion, or proof of compliance.'
)

DISCLAIMER_HEADING = '## הצהרה (Disclaimer)'
NEUTRAL_TITLE = '# Israeli Accessibility Auditor — בקשת תיקון נגישות'


def _has_hebrew(text):
    return any(0x0590 <= ord(ch) <= 0x05FF for ch in text)


class NeutralPacketTests(unittest.TestCase):
    def legacy_report(self, evidence='plain evidence'):
        return {
            'disclaimer': LEGACY_ENGLISH_DISCLAIMER,
            'findings': [
                {'id': 'selected', 'stable_id': 'stable-selected', 'status': 'fail',
                 'evidence_type': 'automatically-verified', 'engine': 'axe',
                 'evidence': evidence},
            ],
        }

    def test_legacy_english_disclaimer_is_replaced_with_hebrew(self):
        packet = build_packet(self.legacy_report(), ['stable-selected'])
        self.assertNotIn('not an accessibility certificate', packet)
        disclaimer_section = packet.split(DISCLAIMER_HEADING, 1)[1]
        self.assertTrue(_has_hebrew(disclaimer_section))

    def test_packet_title_is_neutral_and_unbranded(self):
        packet = build_packet(self.legacy_report(), ['stable-selected'])
        self.assertEqual(packet.splitlines()[0], NEUTRAL_TITLE)
        self.assertNotIn('Next Impact', packet)
        self.assertNotIn('nextimpact.co.il', packet)

    def test_target_site_evidence_mentioning_a_brand_is_preserved(self):
        # Brand text inside site-derived evidence is data, not tool branding,
        # and must survive verbatim inside the fenced evidence block.
        packet = build_packet(
            self.legacy_report(evidence='<a href="https://nextimpact.co.il">Next Impact</a>'),
            ['stable-selected'])
        self.assertIn('Next Impact', packet)
        self.assertIn('https://nextimpact.co.il', packet)

    def test_browser_twin_matches_python_for_legacy_disclaimer_input(self):
        node = shutil.which('node')
        if not node:
            self.skipTest('Node is required for browser packet parity')
        report = self.legacy_report()
        expected = build_packet(report, ['stable-selected'])
        script = 'global.window = {};\n' + browser_script()
        script += ('\nprocess.stdout.write(window.RepairPack.build('
                   + json.dumps(report) + ', ["stable-selected"]));')
        actual = subprocess.check_output([node, '-e', script], text=True)
        self.assertEqual(actual, expected)
        self.assertNotIn('not an accessibility certificate', actual)


if __name__ == '__main__':
    unittest.main()
