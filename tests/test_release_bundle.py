"""Portable synthetic fixtures exercise integrity, persistence and HTTP semantics."""
import importlib
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np
import pandas as pd
from fastapi.testclient import TestClient

from scripts.ranking_features import load_schema
from scripts.ranking_models import fit_model, load_model_config, save_artifact, score_model


class ReleaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schema = load_schema()
        examples = pd.DataFrame(0.0, index=range(8), columns=cls.schema['features'])
        examples['ci_invoice_count'] = [0, 2] * 4
        examples['ci_previously_purchased'] = [0, 1] * 4
        examples['ci_days_since_last_purchase'] = [np.nan, 10] * 4
        examples['query_id'] = [f'q{i//2}' for i in range(8)]
        examples['label'], examples['row_weight'] = [0, 1] * 4, .5
        cls.artifact = fit_model('logistic', examples, cls.schema, load_model_config())
        cls.features = examples.iloc[:3][cls.schema['features']].copy()
        cls.features['customer_id'] = 'known-private-id'
        cls.features['sku'] = ['B', 'A', 'C']
        cls.features['query_id'] = '2011-05-01|known-private-id'
        cls.features['cutoff'] = pd.Timestamp('2011-05-01')
        cls.features['selected_source'] = 'history'
        cls.features['candidate_rank'] = [1.,2.,3.]
        # The fitted rank coefficient is zero: B and C tie and sort lexically.
        cls.items = pd.DataFrame({'sku':['A','B','C'], 'description':['alpha','beta','gamma'],
            'last_observed_at': pd.to_datetime(['2011-04-01']*3)})
        cls.fallback = pd.DataFrame({'sku':['C','A','B'], 'ranking_score':[5.,3.,1.]})

    def setUp(self):
        self.assertTrue(Path('scripts/release_bundle.py').exists(), 'Release bundle implementation is required')
        self.module = importlib.import_module('scripts.release_bundle')
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        save_artifact(self.artifact, self.root/'model.joblib')
        self.path = self.module.create_bundle(self.root/'releases', self.root/'model.joblib',
            self.features, self.items, self.fallback,
            {'data_as_of':'2011-05-01T00:00:00', 'model_last_training_feature_cutoff':'2011-01-01T00:00:00',
             'model_labels_end_exclusive':'2011-02-01T00:00:00',
             'validation_labels_end_exclusive':'2011-05-01T00:00:00', 'code_commit':'synthetic-test'},
            policy_paths=[Path('config/ranking_features_v1.json'), Path('config/retrieval_policy_v1.json'),
                          Path('config/ranking_models_v1.json'), Path('config/stock_code_policy.json'), Path('config/release_v1.json')])

    def test_save_load_offline_equivalence_and_immutable_rebuild(self):
        bundle = self.module.ReleaseBundle.load(self.path)
        expected = score_model(self.artifact, self.features)
        np.testing.assert_allclose(bundle.score_customer('known-private-id'), expected, atol=1e-12)
        order = [row['item_id'] for row in bundle.recommend('known-private-id',3)['items']]
        self.assertEqual(order, ['A','B','C'])
        before = (self.path/'manifest.json').read_bytes()
        again = self.module.create_bundle(self.root/'releases', self.root/'model.joblib', self.features,
            self.items,self.fallback,bundle.manifest['provenance'], policy_paths=bundle.original_policy_paths)
        self.assertEqual(again,self.path)
        self.assertEqual(before,(self.path/'manifest.json').read_bytes())

    def test_corruption_detected_before_loading_model(self):
        (self.path/'model.joblib').chmod(0o644)
        with (self.path/'model.joblib').open('ab') as out:
            out.write(b'corrupt')
        with self.assertRaisesRegex(self.module.BundleValidationError,'hash|integrity'):
            self.module.ReleaseBundle.load(self.path)

    def test_version_and_schema_mismatch(self):
        (self.path/'manifest.json').chmod(0o644)
        manifest = json.loads((self.path/'manifest.json').read_text())
        manifest['bundle_version'] = 'future-incompatible-version'
        (self.path/'manifest.json').write_text(json.dumps(manifest))
        with self.assertRaisesRegex(self.module.BundleValidationError,'version'):
            self.module.ReleaseBundle.load(self.path)
        manifest['bundle_version'] = 'release_bundle_v1'
        manifest['feature_names'] = list(reversed(manifest['feature_names']))
        (self.path/'manifest.json').write_text(json.dumps(manifest))
        with self.assertRaisesRegex(self.module.BundleValidationError,'schema|feature'):
            self.module.ReleaseBundle.load(self.path)

    def test_api_known_unknown_k_and_offline_equivalence(self):
        api = importlib.import_module('scripts.local_api')
        bundle = self.module.ReleaseBundle.load(self.path)
        with TestClient(api.create_app(self.path)) as client:
            self.assertEqual(client.get('/ready').status_code,200)
            self.assertEqual(client.get('/health').status_code,200)
            with self.assertLogs('recommendation_requests',level='INFO') as logs:
                result = client.get('/recommendations/known-private-id?k=3')
            self.assertNotIn('known-private-id',' '.join(logs.output))
            self.assertEqual(result.json(),bundle.recommend('known-private-id',3))
            self.assertEqual(result.json()['recommendation_mode'],'personalized')
            unknown = client.get('/recommendations/never-seen?k=2').json()
            self.assertEqual(unknown['recommendation_mode'],'popularity_fallback')
            self.assertEqual([i['item_id'] for i in unknown['items']],['C','A'])
            for k in ['0','21','-1','invalid']:
                self.assertEqual(client.get('/recommendations/known-private-id',params={'k':k}).status_code,422)
            self.assertEqual(client.get('/recommendations/known-private-id?as_of=2011-01-01').status_code,422)
            self.assertEqual(client.get('/model').json()['data_as_of'],'2011-05-01T00:00:00')
            self.assertEqual(client.post('/events').status_code,404)

    def test_operational_logs_report_versions_memory_and_startup_without_ids(self):
        api=importlib.import_module('scripts.local_api')
        with self.assertLogs('recommendation_requests',level='INFO') as logs:
            with TestClient(api.create_app(self.path)) as client:
                response=client.get('/recommendations/known-private-id')
                ready=client.get('/ready').json()
        self.assertIn('bundle_loaded',' '.join(logs.output))
        self.assertNotIn('known-private-id',' '.join(logs.output))
        self.assertIn('model_version',' '.join(logs.output))
        self.assertIn('process_peak_rss_mib',ready)
        self.assertIn('X-Instance-Start-Id',response.headers)

    def test_invalid_bundle_remains_live_but_not_ready(self):
        api = importlib.import_module('scripts.local_api')
        (self.path/'manifest.json').chmod(0o644)
        (self.path/'manifest.json').write_text('{}')
        with TestClient(api.create_app(self.path)) as client:
            self.assertEqual(client.get('/health').status_code,200)
            self.assertEqual(client.get('/ready').status_code,503)
            self.assertEqual(client.get('/model').status_code,503)
            self.assertEqual(client.get('/recommendations/known-private-id').status_code,503)

    def test_release_readable_by_container_user_and_published_readonly(self):
        self.assertEqual(self.path.stat().st_mode & 0o777,0o555)
        for path in self.path.rglob('*'):
            self.assertEqual(path.stat().st_mode & 0o777,0o555 if path.is_dir() else 0o444)

    def test_manifest_policy_versions_match_frozen_configuration(self):
        manifest = json.loads((self.path/'manifest.json').read_text())
        self.assertEqual(manifest['policy_versions']['retrieval'],
            json.loads(Path('config/retrieval_policy_v1.json').read_text())['policy_version'])
        self.assertEqual(manifest['policy_versions']['model_configuration'],
            json.loads(Path('config/ranking_models_v1.json').read_text())['version'])


if __name__ == '__main__':
    unittest.main()
