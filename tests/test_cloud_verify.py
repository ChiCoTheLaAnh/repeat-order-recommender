import importlib
import asyncio
from pathlib import Path
import unittest
from unittest.mock import patch
import httpx
from fastapi.testclient import TestClient

from tests import test_release_bundle as fixture


class RemoteSmokeTests(fixture.ReleaseTests):
    def test_rollback_metrics_follow_traffic_not_latest_created_revision(self):
        cloud=importlib.import_module('scripts.cloud_verify')
        state={'status':{'latestReadyRevisionName':'new','traffic':[{'revisionName':'old','percent':100}]}}
        self.assertEqual(cloud.serving_revision(state),'old')
        state['status']['traffic']=[{'revisionName':'old','percent':50},{'revisionName':'new','percent':50}]
        with self.assertRaisesRegex(ValueError,'100%'):
            cloud.serving_revision(state)

    def test_smoke_checks_real_routes_without_reporting_customer_ids(self):
        self.assertTrue(Path('scripts/cloud_verify.py').exists(),'Cloud smoke/load verification required')
        cloud=importlib.import_module('scripts.cloud_verify')
        api=importlib.import_module('scripts.local_api')
        bundle=self.module.ReleaseBundle.load(self.path)
        with TestClient(api.create_app(self.path)) as client:
            result=cloud.smoke(client,bundle)
        self.assertTrue(result['passed'])
        self.assertNotIn('known-private-id',str(result))
        self.assertEqual(result['unknown_mode'],'popularity_fallback')

    def test_failed_known_customer_smoke_never_prints_customer_url(self):
        cloud=importlib.import_module('scripts.cloud_verify')
        api=importlib.import_module('scripts.local_api')
        bundle=self.module.ReleaseBundle.load(self.path)
        with TestClient(api.create_app(self.path)) as local:
            def reply(request):
                if request.url.path.startswith('/recommendations/'):
                    return httpx.Response(503,request=request)
                return httpx.Response(200,json=local.get(request.url.path).json(),request=request)
            with httpx.Client(base_url='https://demo.run.app',transport=httpx.MockTransport(reply)) as client:
                with self.assertRaises(RuntimeError) as caught:
                    cloud.smoke(client,bundle)
        self.assertIn('503',str(caught.exception))
        self.assertNotIn('known-private-id',str(caught.exception))
        self.assertNotIn('https://',str(caught.exception))

    def test_bounded_warm_probe_reports_real_http_results_and_target(self):
        cloud=importlib.import_module('scripts.cloud_verify')
        original=httpx.AsyncClient
        def reply(request):
            mode='popularity_fallback' if 'unknown-cloud-load' in request.url.path else 'personalized'
            return httpx.Response(200,json={'recommendation_mode':mode,'items':[{}, {}, {}]},
                                  headers={'X-Instance-Start-Id':'synthetic'},request=request)
        def client(**kwargs):
            return original(**kwargs,transport=httpx.MockTransport(reply))
        with patch.object(cloud.httpx,'AsyncClient',side_effect=client):
            result=asyncio.run(cloud.warm_level('https://demo.run.app',{},['known-private-id'],5,
                {'warmup_requests':2,'seconds_per_level':1,'requests_per_level':10,'k':3}))
        self.assertEqual(result['completed_requests'],10)
        self.assertEqual(result['error_rate'],0)
        self.assertEqual(result['attempted_mode_counts']['popularity_fallback'],1)
        self.assertTrue(result['warm_p95_target_met'])


if __name__=='__main__':
    unittest.main()
