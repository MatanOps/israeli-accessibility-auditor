"""Grouped report counts preserve occurrence data and finding identities."""

import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from report import DISCLAIMER, IMAGES, build_report, finding, grouped_counts


def sample(rule_id='image-alt', status='fail', engine='axe', selector='#image'):
    item = finding(rule_id, IMAGES, 'serious', status, 'automatically-verified',
                   '1.1.1', {'url': 'https://example.test/', 'selector': selector},
                   '<img>', 'Test evidence', 'Provide an alternative')
    item.update(engine=engine, rule_id=rule_id)
    return item


def report_for(items, status='completed'):
    return build_report('https://example.test/', 'url', items, 1, [],
                        {'run': {'status': status, 'rendered': True}})


class GroupedSummaryTests(unittest.TestCase):
    def test_repeated_rule_groups_without_changing_occurrences_or_identities(self):
        items = [sample(selector='#first'), sample(selector='#second'),
                 sample('button-name', selector='#button')]
        original = copy.deepcopy(items)
        counts = grouped_counts(items)
        self.assertEqual((counts['problem_types'], counts['problem_occurrences']), (2, 3))
        self.assertEqual(items, original)
        report = report_for(items)
        self.assertEqual(report['summary']['by_status']['fail'], 3)
        self.assertEqual(report['summary']['actionable_findings'], 3)
        self.assertEqual(report['summary']['problem_types'], 2)
        failures = [item for item in report['findings'] if item['status'] == 'fail']
        self.assertEqual(len({item['stable_id'] for item in failures}), 3)
        self.assertEqual({item['id'] for item in failures}, {'image-alt-1', 'image-alt-2', 'button-name'})
        self.assertEqual([item['rule_id'] for item in failures], ['image-alt', 'image-alt', 'button-name'])

    def test_same_rule_from_different_engines_stays_separate(self):
        counts = grouped_counts([sample(engine='axe'), sample(engine='static')])
        self.assertEqual(counts['problem_types'], 2)
        self.assertEqual(counts['problem_occurrences'], 2)

    def test_status_groups_do_not_mix_warnings_manual_tasks_and_passes(self):
        items = [sample(), sample('region', 'warning'), sample('region', 'warning', selector='#other'),
                 sample('contrast', 'human-review-required'),
                 sample('human-keyboard-operation', 'human-review-required', 'static'),
                 sample('image-alt', 'pass'), sample('scope-unknown', 'not-tested')]
        self.assertEqual(grouped_counts(items), {
            'problem_types': 1, 'problem_occurrences': 1,
            'recommendation_types': 1, 'recommendation_occurrences': 2,
            'review_types': 2, 'review_occurrences': 2,
            'pass_types': 1, 'pass_occurrences': 1,
        })

    def test_best_practice_counts_follow_normalized_warning_status(self):
        item = sample('heading-order')
        item['standards_basis'] = 'best-practice'
        summary = report_for([item])['summary']
        self.assertEqual(summary['problem_types'], 0)
        self.assertEqual(summary['problem_occurrences'], 0)
        self.assertEqual(summary['recommendation_types'], 1)
        self.assertEqual(summary['recommendation_occurrences'], 1)

    def test_not_performed_suppresses_all_grouped_counts(self):
        report = report_for([sample(), sample('region', 'warning'),
                             sample('contrast', 'human-review-required'), sample(status='pass')],
                            status='not-performed')
        for field in grouped_counts([]):
            self.assertEqual(report['summary'][field], 0, field)
        self.assertEqual(report['summary']['files_scanned'], 0)

    def test_old_saved_records_and_malformed_records(self):
        old = {'id': 'static-image-alt', 'status': 'fail'}
        invalid = [None, 'finding', {}, {'status': []}, {'status': 'fail', 'rule_id': []},
                   {'status': 'fail', 'rule_id': 'image-alt', 'engine': {}},
                   {'status': 'fail', 'rule_id': ''}]
        counts = grouped_counts([old, dict(old), *invalid])
        self.assertEqual((counts['problem_types'], counts['problem_occurrences']), (1, 2))
        self.assertTrue(all(value == 0 for value in grouped_counts(None).values()))

    def test_disclaimer_is_hebrew_and_preserves_scope(self):
        self.assertIn('בדיקות אוטומטיות בהיקף מוגבל', DISCLAIMER)
        self.assertIn('אינו אישור נגישות', DISCLAIMER)
        self.assertIn('חוות דעת משפטית', DISCLAIMER)
        self.assertIn('תקן ישראלי 5568', DISCLAIMER)
        self.assertIn('טכנולוגיות מסייעות', DISCLAIMER)
        self.assertIn('משתמשים', DISCLAIMER)
        self.assertIn('איש מקצוע מוסמך', DISCLAIMER)
        self.assertIn('WCAG 2.2 AA', DISCLAIMER)
        self.assertNotIn('This report', DISCLAIMER)
        self.assertEqual(report_for([])['disclaimer'], DISCLAIMER)


if __name__ == '__main__':
    unittest.main()
