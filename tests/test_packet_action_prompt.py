"""The packet must open with an actionable Hebrew request and auditor repo guidance."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from repair_packet import build_packet, browser_script

REPO_URL = 'https://github.com/MatanOps/israeli-accessibility-auditor'
INSTALL_CMD = 'npx skills add MatanOps/israeli-accessibility-auditor'


def _packet():
    report = {'findings': [
        {'id': 'selected', 'stable_id': 'stable-selected', 'status': 'fail',
         'evidence_type': 'automatically-verified', 'engine': 'axe',
         'evidence': 'SELECTED_EVIDENCE'},
        {'id': 'excluded', 'evidence': 'EXCLUDED_EVIDENCE'},
    ]}
    return report, build_packet(report, ['stable-selected'])


class PacketActionPromptTests(unittest.TestCase):
    def test_packet_opens_with_explicit_action_request(self):
        _, packet = _packet()
        head = packet.index('## בקשת פעולה לסוכן המקבל')
        # The action request must come before the binding-instructions section.
        self.assertLess(head, packet.index('## הוראות מחייבות לסוכן המתקן'))
        # Verify-selected-only, fix in the authorized project, per-finding reporting.
        self.assertIn('רק את הממצאים שנבחרו בחבילה זו', packet)
        self.assertIn('בפרויקט האמיתי שאליו יש גישה מורשית', packet)
        self.assertIn('דווח/י תוצאה לכל ממצא בנפרד: תוקן, לא תוקן, לא אומת או דורש אדם', packet)
        # Missing access: ask for the specific access and one exact next step.
        self.assertIn('בקש/י את הגישה הספציפית החסרה', packet)
        self.assertIn('צעד המשך מדויק אחד', packet)
        self.assertIn('כתובת URL לבדה אינה מאפשרת לערוך אתר', packet)

    def test_packet_references_public_repo_and_baseline_workflow(self):
        _, packet = _packet()
        self.assertIn(REPO_URL, packet)
        self.assertIn('`' + INSTALL_CMD + '`', packet)
        self.assertIn('קרא/י את SKILL.md המותקן', packet)
        self.assertIn('מדידת בסיס (baseline) עדכנית לפני כל תיקון', packet)
        self.assertIn('באותו יעד ובאותו היקף', packet)
        # No deployment promise or automatic verified-repair claim.
        self.assertIn('אין כאן הבטחת פריסה או תיקון מאומת אוטומטי', packet)

    def test_new_guidance_keeps_selected_only_evidence(self):
        _, packet = _packet()
        self.assertIn('SELECTED_EVIDENCE', packet)
        self.assertNotIn('EXCLUDED_EVIDENCE', packet)
        # Empty selection still carries the guidance but authorizes nothing.
        empty = build_packet({'findings': [{'id': 'a', 'evidence': 'HIDDEN'}]}, [])
        self.assertIn(REPO_URL, empty)
        self.assertIn('לא נבחר אף ממצא', empty)
        self.assertNotIn('HIDDEN', empty)

    def test_browser_twin_emits_identical_guidance(self):
        node = shutil.which('node')
        if not node:
            self.skipTest('Node is required for browser packet parity')
        report, expected = _packet()
        script = 'global.window = {};\n' + browser_script()
        script += ('\nprocess.stdout.write(window.RepairPack.build('
                   + json.dumps(report) + ', ["stable-selected"]));')
        actual = subprocess.check_output([node, '-e', script], text=True)
        self.assertEqual(actual, expected)
        self.assertIn(REPO_URL, actual)

    def test_browser_script_remains_safe_to_inline(self):
        script = browser_script()
        self.assertNotIn('</' + 'script', script.lower())
        self.assertNotIn('<!--', script)


if __name__ == '__main__':
    unittest.main()
