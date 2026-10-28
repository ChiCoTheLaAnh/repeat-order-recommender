import importlib
from pathlib import Path
import unittest

import pandas as pd

from tests.test_temporal_snapshots import paid_fixture


class BuildTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(Path('scripts/build_release.py').exists(),'Past-only serving builder required')
        self.module = importlib.import_module('scripts.build_release')

    def test_display_uses_only_past_eligible_nonempty_descriptions(self):
        paid = paid_fixture([
            ('2011-03-01','12345','i1','10001'),('2011-04-01','12345','i2','10001'),
            ('2011-05-01','12345','i3','10001'),('2011-06-01','12345','i4','FUTURE')])
        paid['description'] = ['old description','', 'future description','future product']
        items = self.module.item_metadata(paid,pd.Timestamp('2011-05-01'))
        self.assertEqual(list(items.sku),['10001'])
        self.assertEqual(items.iloc[0].description,'old description')
        self.assertEqual(items.iloc[0].last_observed_at,pd.Timestamp('2011-04-01'))

    def test_model_selection_prefers_simple_only_with_small_quality_difference(self):
        selector = importlib.import_module('scripts.model_selection')
        report = {'paired_ndcg_difference_xgb_minus_logistic':{'mean_difference':.001,'lower':-.003,'upper':.006},
            'quality':{'logistic':{'recall_at_10':.21},'xgb_depth5':{'recall_at_10':.212}},
            'operations':{'logistic':{'artifact_bytes':100},'xgb_depth5':{'artifact_bytes':10000}}}
        self.assertEqual(selector.select_model(report),'logistic')
        report['paired_ndcg_difference_xgb_minus_logistic']['mean_difference'] = .02
        self.assertEqual(selector.select_model(report),'xgb_depth5')


if __name__ == '__main__':
    unittest.main()
