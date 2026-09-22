"""A selector observed on another page must not prove a site-wide repair."""
import copy
import unittest
from test_compare import finding, report, URL
from compare import compare_reports


class SiteComparisonTests(unittest.TestCase):
    def setUp(self):
        self.before = report([finding()])
        self.after = report([finding('pass-button-name', 'pass')])
        for item in (self.before, self.after):
            item['metadata']['run']['site'] = {'complete': True}
            item['metadata']['run']['pages'].append(URL + '/second')
        self.after['metadata']['run']['observed_selectors_by_page'] = {URL: {'#submit': True}}

    def test_matching_page_and_selector_with_pass_proves_fix(self):
        result = compare_reports(self.before, self.after)
        self.assertEqual([item['identity'] for item in result['fixed_verified']], ['old'])

    def test_same_selector_on_other_page_is_not_positive_evidence(self):
        self.after['metadata']['run']['observed_selectors_by_page'] = {URL + '/second': {'#submit': True}}
        result = compare_reports(self.before, self.after)
        self.assertEqual(result['fixed_verified'], [])
        self.assertEqual(len(result['not_tested']), 1)

    def test_missing_page_receipts_do_not_fall_back_to_flat_selector_map(self):
        self.after['metadata']['run'].pop('observed_selectors_by_page')
        self.assertEqual(compare_reports(self.before, self.after)['fixed_verified'], [])

    def test_partial_site_cannot_prove_fix_even_when_selector_passes(self):
        self.after['metadata']['run']['status'] = 'partial'
        self.after['metadata']['run']['site']['complete'] = False
        self.assertEqual(compare_reports(self.before, self.after)['fixed_verified'], [])

    def test_contradictory_site_receipt_cannot_prove_fix(self):
        self.after['metadata']['run']['site']['complete'] = False
        self.assertEqual(compare_reports(self.before, self.after)['fixed_verified'], [])
