"""Pure deployment contracts: provenance, privacy, resource caps and immutable images."""
import importlib
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch
import shutil
import pandas as pd

from tests import test_release_bundle as release_fixtures


class CloudConfigTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(Path('scripts/cloud_deploy.py').exists(),'Cloud deployment implementation required')
        self.module=importlib.import_module('scripts.cloud_deploy')

    def test_cloudrun_resource_caps_probes_digest_and_labels(self):
        service=self.module.service_spec('demo-project-123','us-central1',
            'us-central1-docker.pkg.dev/demo-project-123/repeat-order-demo/demo@sha256:'+'a'*64,
            {'release_id':'release_v1_'+'b'*64,'model_version':'logistic_123',
             'data_as_of':'2011-05-01T00:00:00','provenance':{'code_commit':'c'*40}})
        template=service['spec']['template']
        self.assertEqual(template['metadata']['annotations']['autoscaling.knative.dev/minScale'],'0')
        self.assertEqual(template['metadata']['annotations']['autoscaling.knative.dev/maxScale'],'1')
        self.assertEqual(template['metadata']['annotations']['run.googleapis.com/cpu-throttling'],'true')
        self.assertEqual(template['spec']['containerConcurrency'],5)
        container=template['spec']['containers'][0]
        self.assertEqual(container['resources']['limits'],{'cpu':'1','memory':'1Gi'})
        self.assertEqual(container['startupProbe']['httpGet']['path'],'/ready')
        self.assertEqual(container['livenessProbe']['httpGet']['path'],'/health')
        self.assertEqual(service['metadata']['labels']['purpose'],'historical-recommendation-demo')
        with self.assertRaisesRegex(ValueError,'digest'):
            self.module.service_spec('demo-project-123','us-central1','image:latest',{})

    def test_raw_request_log_privacy_guard_covers_every_exporting_sink(self):
        filter_=self.module.request_log_filter()
        self.assertIn('run.googleapis.com%2Frequests',filter_)
        self.assertIn('repeat-order-historical-demo',filter_)
        sinks=[{'name':'_Default','exclusions':[{'name':'historical-demo-request-privacy','filter':filter_}]},
               {'name':'_Required'}, {'name':'custom-export','filter':''}]
        with self.assertRaisesRegex(ValueError,'custom-export'):
            self.module.verify_log_privacy(sinks)
        sinks[-1]['exclusions']=[{'name':'historical-demo-request-privacy','filter':filter_}]
        self.module.verify_log_privacy(sinks)

    def test_clean_logistic_proof_rejects_another_model_or_source(self):
        reference=json.loads(Path('config/logistic_artifact_v1.json').read_text())
        windows=[{'cutoff':t.isoformat(),'end_exclusive':(t+pd.offsets.MonthBegin(1)).isoformat()}
                 for t in pd.date_range('2010-03-01','2011-01-01',freq='MS')]
        manifest={'data_as_of':'2011-05-01T00:00:00','model_name':'logistic',
            'files':{'model.joblib':{'sha256':reference['joblib_sha256']},
                     'policies/ranking_features_v1.json':{'sha256':reference['feature_schema_sha256']}},
            'provenance':{'code_worktree_dirty':False,'code_commit':'c'*40,'deployable_build':True,
                'training_label_windows':windows,'model_labels_end_exclusive':'2011-02-01T00:00:00',
                'validation_labels_end_exclusive':'2011-05-01T00:00:00','data_access':{'test_evaluated':False}}}
        self.module.validate_deployable_manifest(manifest,'c'*40)
        with self.assertRaisesRegex(ValueError,'commit'):
            self.module.validate_deployable_manifest(manifest,'d'*40)
        manifest['files']['model.joblib']['sha256']='e'*64
        with self.assertRaisesRegex(ValueError,'artifact'):
            self.module.validate_deployable_manifest(manifest,'c'*40)

    def test_archive_traversal_is_rejected_before_extraction(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);archive=root/'unsafe.tar'
            with tarfile.open(archive,'w') as tar:
                tar.addfile(tarfile.TarInfo('bundle/../../outside'))
            with self.assertRaisesRegex(ValueError,'Unsafe'):
                self.module.extract_bundle(archive,root/'extracted',self.module.sha256(archive))
            self.assertFalse((root/'outside').exists())

    def test_image_provenance_must_bind_to_the_supplied_manifest(self):
        manifest={'release_id':'release_v1_example','provenance':{'code_commit':'a'*40}}
        labels={'org.opencontainers.image.revision':'a'*40,'io.repeat-order.release':'release_v1_example',
                'io.repeat-order.bundle-manifest-sha256':'b'*64}
        self.module.validate_image_labels(labels,manifest,'b'*64)
        labels['org.opencontainers.image.revision']='c'*40
        with self.assertRaisesRegex(ValueError,'OCI'):
            self.module.validate_image_labels(labels,manifest,'b'*64)

    def test_aggregated_ancestor_exports_require_customer_url_exclusions(self):
        project_sinks=[{'name':'_Default','exclusions':[{'name':self.module.PRIVACY_EXCLUSION,
                                                       'filter':self.module.request_log_filter()}]}]
        def backend(*args,**kwargs):
            if args[:2]==('projects','get-ancestors'):
                return [{'type':'project','id':'demo'},{'type':'organization','id':'123'}]
            if '--organization' in args:
                return [{'name':'aggregated','includeChildren':True}]
            return project_sinks
        with patch.object(self.module,'gcloud',side_effect=backend):
            with self.assertRaisesRegex(ValueError,'aggregated'):
                self.module.privacy_preflight('demo')

    def test_monitoring_preserves_fallback_denominator_and_startup_errors(self):
        bootstrap=importlib.import_module('scripts.bootstrap_cloud')
        metrics,dashboard=bootstrap.definitions()
        recommendation=next(m for m in metrics if m['name']=='repeat_demo_recommendations')
        self.assertIn('jsonPayload.status=200',recommendation['filter'])
        failure=next(m for m in metrics if m['name']=='repeat_demo_startup_failures')
        self.assertIn('bundle_load_failed',failure['filter'])
        ratio=dashboard['gridLayout']['widgets'][-1]['xyChart']['dataSets'][0]['timeSeriesQuery']['timeSeriesFilterRatio']
        self.assertIn('popularity_fallback',ratio['numerator']['filter'])
        self.assertNotIn('popularity_fallback',ratio['denominator']['filter'])
        self.assertNotIn('customer_id',str(recommendation['labelExtractors']))
        # Actual pinned SDK dictionary flags treat any nonempty disabled value as True.
        self.assertNotIn('disabled=',bootstrap.exclusion_argument(False))
        self.assertTrue(bootstrap.exclusion_argument(True).endswith(',disabled='))


class DeployableBundleTests(release_fixtures.ReleaseTests):
    # Reuse the real synthetic bundle fixture, not a pickle or HTTP mock.
    def test_dirty_source_and_training_window_overlap_are_rejected(self):
        self.assertTrue(Path('scripts/cloud_deploy.py').exists(),'Deployable provenance validation required')
        cloud=importlib.import_module('scripts.cloud_deploy')
        manifest=json.loads((self.path/'manifest.json').read_text())
        manifest['provenance']['code_worktree_dirty']=True
        with self.assertRaisesRegex(ValueError,'clean|dirty'):
            cloud.validate_deployable_manifest(manifest,expected_commit='c'*40)
        manifest['provenance']['code_worktree_dirty']=False
        manifest['provenance']['code_commit']='c'*40
        manifest['provenance']['training_label_windows']=[{'cutoff':'2011-01-01','end_exclusive':'2011-06-01'}]
        with self.assertRaisesRegex(ValueError,'window|future|temporal'):
            cloud.validate_deployable_manifest(manifest,expected_commit='c'*40)

    def test_image_extraction_does_not_require_repository_cache(self):
        cloud=importlib.import_module('scripts.cloud_deploy')
        manifest=json.loads((self.path/'manifest.json').read_text())
        labels={'org.opencontainers.image.revision':manifest['provenance']['code_commit'],
                'io.repeat-order.release':manifest['release_id'],
                'io.repeat-order.bundle-manifest-sha256':cloud.sha256(self.path/'manifest.json')}
        docker_config=self.root/'external-docker';docker_config.mkdir()
        def docker(command,**kwargs):
            if command[1:3]==['image','inspect']:
                return json.dumps([{'Id':'sha256:test','Config':{'Labels':labels}}])
            if command[1]=='create':return 'test-container'
            if command[1]=='cp':shutil.copytree(self.path,command[-1])
            return ''
        with patch.object(cloud,'ROOT',self.root/'absent-repository'), \
             patch.dict(cloud.os.environ,{'DOCKER_CONFIG':str(docker_config)}), \
             patch.object(cloud.subprocess,'check_output',side_effect=docker), \
             patch.object(cloud,'clean_commit',return_value='synthetic-test'), \
             patch.object(cloud,'validate_deployable_manifest'):
            result=cloud.verify_image_bundle('registry@sha256:test',self.path)
        self.assertTrue(result['verified_without_execution'])
        self.assertFalse((self.root/'absent-repository/.cache').exists())


if __name__=='__main__':
    unittest.main()
