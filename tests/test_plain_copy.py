"""Plain Hebrew label-in-name copy respects evidence and scan status."""

import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from hebrew import finding_copy


class LabelNameCopyTests(unittest.TestCase):
    def sample(self, status='fail'):
        return {'rule_id': 'axe-label-content-name-mismatch', 'status': status,
                'category': 'Forms, labels and validation',
                'evidence': '<a aria-label="הצגת אפשרויות עבור חולצה">בחר אפשרויות</a>'}

    def test_failure_explains_voice_control_and_preserves_visible_copy(self):
        item = self.sample()
        original = copy.deepcopy(item)
        text = finding_copy(item)
        self.assertIn('הכיתוב הגלוי אינו כלול בשם הנגיש', text['title'])
        self.assertIn('פקודות קוליות', text['impact'])
        self.assertIn('קורא מסך', text['impact'])
        self.assertIn('השוו', text['action'])
        self.assertIn('שמרו על הכיתוב הגלוי', text['action'])
        self.assertIn('לעדכן את השם הנגיש כך שיכלול אותו', text['action'])
        self.assertIn('אפשר להוסיף מידע מועיל', text['action'])
        self.assertNotIn('טפסים', text['title'])
        self.assertEqual(item, original)

    def test_incomplete_does_not_assert_a_mismatch(self):
        text = finding_copy(self.sample('human-review-required'))
        self.assertTrue(text['title'].startswith('נדרשת בדיקה אנושית:'))
        self.assertIn('לא הצליחה להכריע', text['impact'])
        self.assertIn('אם אכן יש בעיה', text['action'])
        self.assertNotIn('אינו כלול', text['title'])

    def test_pass_uses_neutral_subject_and_keeps_scoped_qualification(self):
        text = finding_copy(self.sample('pass'))
        self.assertTrue(text['title'].startswith('נבדק ונמצא תקין:'))
        self.assertIn('התאמה בין הכיתוב הגלוי לשם הנגיש', text['title'])
        self.assertNotIn('אינו כלול', text['title'])
        self.assertIn('לבדיקה הזו בלבד', text['impact'])

    def test_legacy_occurrence_id_uses_the_same_rule_copy(self):
        item = self.sample()
        item['id'] = item.pop('rule_id') + '-3'
        self.assertEqual(finding_copy(item), finding_copy(self.sample()))


if __name__ == '__main__':
    unittest.main()
