"""Release approval contracts use synthetic manifests, never the held-out split."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd
from scripts import cloud_deploy as cloud


def manifest():
    anchor=json.loads(Path('config/logistic_artifact_v1.json').read_text())
    return {'data_as_of':'2011-05-01T00:00:00','model_name':'logistic',
        'model_version':'logistic_frozen','release_id':'release_v1_frozen',
        'files':{'model.joblib':{'sha256':anchor['joblib_sha256']},
            'policies/ranking_features_v1.json':{'sha256':anchor['feature_schema_sha256']}},
        'provenance':{'code_worktree_dirty':False,'code_commit':'c'*40,
            'deployable_build':True,'model_labels_end_exclusive':'2011-02-01T00:00:00',
            'validation_labels_end_exclusive':'2011-05-01T00:00:00',
            'data_access':{'test_evaluated':False},
            'training_label_windows':[{'cutoff':t.isoformat(),
                'end_exclusive':(t+pd.offsets.MonthBegin(1)).isoformat()}
                for t in pd.date_range('2010-03-01','2011-01-01',freq='MS')]}}


class ApprovedReleaseTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.bundle=self.root/'bundle';self.bundle.mkdir()
        self.m=manifest();self.write_manifest()
        self.image='us-central1-docker.pkg.dev/project-c0e2b13a-3cea-47e9-aad/repeat-order-demo/service@sha256:'+'a'*64
        self.record={'version':'approved_cloud_release_v1','artifact_source_commit':'c'*40,
            'image_digest':self.image,'manifest_sha256':cloud.sha256(self.bundle/'manifest.json'),
            'model_version':'logistic_frozen','release_id':'release_v1_frozen'}
        self.record_path=self.root/'approved.json';self.write_record()
        self.patch=patch.object(cloud,'APPROVED_RELEASE_PATH',self.record_path,create=True)
        self.patch.start();self.addCleanup(self.patch.stop)

    def write_manifest(self):
        (self.bundle/'manifest.json').write_text(json.dumps(self.m))

    def write_record(self):
        self.record_path.write_text(json.dumps(self.record))

    def test_approved_older_artifact_is_independent_of_operator_checkout(self):
        with patch.object(cloud,'clean_commit',return_value='d'*40):
            proof=cloud.approved_release(self.bundle,self.image)
        self.assertEqual(proof['artifact_source_commit'],'c'*40)
        self.assertEqual(proof['approval_sha256'],cloud.sha256(self.record_path))
        # The original build-time source-equality guard is still enforced.
        with self.assertRaisesRegex(ValueError,'commit'):
            cloud.validate_deployable_manifest(self.m,'d'*40)

    def test_changed_image_manifest_source_or_model_is_rejected(self):
        for field,value in [('image_digest',self.image[:-1]+'b'),
            ('manifest_sha256','b'*64),('artifact_source_commit','d'*40),
            ('model_version','other'),('release_id','other'),('version','unknown')]:
            with self.subTest(field=field):
                record=copy.deepcopy(self.record);record[field]=value
                self.record_path.write_text(json.dumps(record))
                with self.assertRaises(ValueError):cloud.approved_release(self.bundle,self.image)
        self.write_record()
        self.m['files']['model.joblib']['sha256']='e'*64;self.write_manifest()
        self.record['manifest_sha256']=cloud.sha256(self.bundle/'manifest.json');self.write_record()
        with self.assertRaisesRegex(ValueError,'artifact'):
            cloud.approved_release(self.bundle,self.image)


class RollbackTests(unittest.TestCase):
    def test_mixed_previous_traffic_is_rejected_before_cloud_mutation(self):
        previous={'status':{'traffic':[{'revisionName':'one','percent':50},
                                      {'revisionName':'two','percent':50}]}}
        with tempfile.TemporaryDirectory() as temp:
            receipt=Path(temp)/'receipt.json'
            receipt.write_text(json.dumps({'previous_service':previous}))
            with patch.object(cloud,'gcloud',side_effect=AssertionError('Cloud mutation before validation')):
                with self.assertRaisesRegex(ValueError,'100%'):
                    cloud.rollback(receipt,Path(temp)/'rollback')

    def test_rollback_checks_revision_digest_then_restores_recorded_traffic(self):
        approved=json.loads(Path('config/approved_cloud_release_v1.json').read_text())
        c=cloud.config();image=approved['image_digest']
        previous={'status':{'traffic':[{'revisionName':'old','percent':100}]}}
        record={'previous_service':previous,'project':c['project'],'region':c['region'],'service':c['service']}
        calls=[]
        def backend(*args,**kwargs):
            calls.append(args)
            if args[:3]==('run','revisions','describe'):
                return {'metadata':{'labels':{'source-commit':approved['artifact_source_commit']}},
                        'spec':{'containers':[{'image':image}]}}
            if args[:3]==('run','services','describe'):
                return {'status':{'traffic':[{'revisionName':'old','percent':100}]}}
            return {}
        with tempfile.TemporaryDirectory() as temp:
            receipt=Path(temp)/'receipt.json';receipt.write_text(json.dumps(record))
            with patch.object(cloud,'gcloud',side_effect=backend), \
                 patch.object(cloud,'privacy_preflight',return_value={'checked':True}), \
                 patch.object(cloud,'clean_commit',return_value='d'*40):
                result=cloud.rollback(receipt,Path(temp)/'rollback')
            self.assertEqual(result['restored_revision'],'old')
            self.assertEqual(result['operator_code_commit'],'d'*40)
            self.assertTrue(any('--to-revisions' in call and 'old=100' in call for call in calls))
            self.assertEqual(json.loads((Path(temp)/'rollback/rollback.json').read_text()),result)
        image='unreviewed-image'
        with tempfile.TemporaryDirectory() as temp:
            receipt=Path(temp)/'receipt.json';receipt.write_text(json.dumps(record));calls.clear()
            with patch.object(cloud,'gcloud',side_effect=backend), \
                 patch.object(cloud,'privacy_preflight',return_value={}), \
                 patch.object(cloud,'clean_commit',return_value='d'*40):
                with self.assertRaisesRegex(ValueError,'approved'):
                    cloud.rollback(receipt,Path(temp)/'rollback')
            self.assertFalse(any('--to-revisions' in call for call in calls))


class ApprovedOperatorLoaderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from tests.test_release_bundle import ReleaseTests
        cls.fixture_class=ReleaseTests
        ReleaseTests.setUpClass()

    def test_operator_can_score_using_exact_artifact_source_without_weakening_runtime(self):
        fixture=self.fixture_class(methodName='test_save_load_offline_equivalence_and_immutable_rebuild')
        fixture.setUp()
        try:
            with patch.object(cloud,'approved_release',return_value={
                    'artifact_source_commit':'b4accd82988a47798df4460f0c205cb95c2c7b74'}):
                loaded=cloud.load_approved_bundle(fixture.path,'synthetic-image')
            self.assertEqual([i['item_id'] for i in loaded.recommend('known-private-id',3)['items']],['A','B','C'])
            self.assertNotEqual(type(loaded).__module__,'scripts.release_bundle')
        finally:fixture.doCleanups()

    def test_changed_bundled_source_is_rejected_before_artifact_import(self):
        fixture=self.fixture_class(methodName='test_save_load_offline_equivalence_and_immutable_rebuild')
        fixture.setUp()
        try:
            loaded=fixture.module.ReleaseBundle.load(fixture.path)
            altered=fixture.module.create_bundle(fixture.root/'different-source',fixture.root/'model.joblib',
                fixture.features,fixture.items,fixture.fallback,loaded.manifest['provenance'],
                code_paths=[Path('scripts/cloud_verify.py').resolve()])
            with patch.object(cloud,'approved_release',return_value={
                    'artifact_source_commit':'b4accd82988a47798df4460f0c205cb95c2c7b74'}), \
                 patch.object(cloud.importlib,'import_module',side_effect=AssertionError('Source was imported before validation')):
                with self.assertRaisesRegex(ValueError,'committed source'):
                    cloud.load_approved_bundle(altered,'synthetic-image')
        finally:fixture.doCleanups()
