import importlib
import json
import asyncio
from pathlib import Path
import unittest
from unittest.mock import patch
import httpx
from fastapi.testclient import TestClient

from tests import test_release_bundle as fixture


def valid_response(mode,k):
    return {'recommendation_mode':mode,
        'items':[{'item_id':str(i),'ranking_score':1.,'description':None} for i in range(k)],
        'model_version':'logistic_fixed','release_version':'release_fixed',
        'data_as_of':'2011-05-01T00:00:00','score_kind':
            'distinct_identified_purchasers_30d' if mode=='popularity_fallback' else 'raw_model_margin',
        'scores_are_calibrated_probabilities':False}


class NativeMetricsTests(unittest.TestCase):
    def test_monitoring_descriptor_names_keep_literal_slashes(self):
        cloud=importlib.import_module('scripts.cloud_verify')
        units={
            'run.googleapis.com/container/startup_latencies':'ms',
            'run.googleapis.com/container/memory/utilizations':'1',
            'run.googleapis.com/request_latencies':'ms',
            'run.googleapis.com/request_count':'1',
        }
        queried=[]
        original=httpx.Client

        def reply(request):
            self.assertEqual(request.headers['Authorization'],'Bearer synthetic-access-token')
            if '/metricDescriptors/' in request.url.path:
                # Monitoring rejects percent-encoded slashes in metric names.
                self.assertNotIn(b'%2f',request.url.raw_path.lower())
                name=request.url.path.split('/metricDescriptors/',1)[1]
                self.assertIn(name,units)
                return httpx.Response(200,json={'name':'projects/demo/metricDescriptors/'+name,
                                               'type':name,'unit':units[name]})
            self.assertEqual(request.url.path,'/v3/projects/demo/timeSeries')
            self.assertEqual(request.url.params['interval.startTime'],'2026-10-27T03:00:00Z')
            self.assertEqual(request.url.params['interval.endTime'],'2026-10-27T03:20:00Z')
            self.assertIn('resource.labels.revision_name="revision-1"',request.url.params['filter'])
            queried.append(request.url.params['filter'])
            return httpx.Response(200,json={})

        def client(**kwargs):
            return original(**kwargs,transport=httpx.MockTransport(reply))

        with patch.object(cloud,'gcloud',return_value='synthetic-access-token'), \
                patch.object(cloud.httpx,'Client',side_effect=client):
            result=cloud.native_metrics('demo','us-central1','revision-1',
                                       '2026-10-27T03:00:00Z','2026-10-27T03:20:00Z')
        self.assertEqual(set(result),set(units))
        self.assertEqual(len(queried),4)
        for name,unit in units.items():
            self.assertEqual(result[name]['unit'],unit)
            self.assertEqual(result[name]['time_series'],[])
            self.assertFalse(result[name]['more_pages'])


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
            return httpx.Response(200,json=valid_response(mode,3),
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


class WarmDiagnosticsTests(unittest.TestCase):
    def test_failure_categories_omit_sensitive_details_and_keep_gate(self):
        cloud=importlib.import_module('scripts.cloud_verify')
        original=httpx.AsyncClient
        calls=iter(['timeout','json','key','mismatch','server','ok'])
        def reply(request):
            outcome=next(calls)
            if outcome=='timeout':
                raise httpx.ReadTimeout('SECRET customer/token URL',request=request)
            if outcome=='json':return httpx.Response(200,text='SECRET invalid JSON')
            if outcome=='key':return httpx.Response(200,json={'SECRET':'private'})
            if outcome=='server':return httpx.Response(503,text='SECRET server error')
            return httpx.Response(200,json=valid_response('wrong' if outcome=='mismatch' else 'personalized',1))
        def client(**kwargs):
            return original(**kwargs,transport=httpx.MockTransport(reply))
        with patch.object(cloud.httpx,'AsyncClient',side_effect=client):
            result=asyncio.run(cloud.warm_level('https://example.invalid',{},['PRIVATE_ID'],1,
                {'warmup_requests':0,'k':1,'seconds_per_level':10,'requests_per_level':6}))
        self.assertEqual(result['failure_counts'],{'transport:ReadTimeout':1,
            'response_parse:JSONDecodeError':1,'response_schema:KeyError':1,
            'response_contract_mismatch':1,'http_status:503':1})
        self.assertEqual(result['status_counts'],{'0':3,'-1':1,'503':1,'200':1})
        self.assertFalse(result['warm_p95_target_met'])
        self.assertNotIn('SECRET',str(result))
        self.assertNotIn('PRIVATE_ID',str(result))


class WarmupFailureTests(unittest.TestCase):
    def test_warmup_failure_is_sanitized_and_sends_no_measured_requests(self):
        cloud=importlib.import_module('scripts.cloud_verify')
        original=httpx.AsyncClient
        for outcome,category in [('timeout','transport:ReadTimeout'),
                ('http','http_status:503'),('json','response_parse:JSONDecodeError'),
                ('schema','response_schema:TypeError')]:
            seen=[]
            def reply(request):
                seen.append(1)
                if len(seen)>1:raise AssertionError('Measured traffic followed failed warmup')
                if outcome=='timeout':raise httpx.ReadTimeout('SECRET URL',request=request)
                if outcome=='http':return httpx.Response(503,text='SECRET')
                if outcome=='json':return httpx.Response(200,text='SECRET')
                return httpx.Response(200,json={'recommendation_mode':'personalized','items':'SECRET'})
            def client(**kwargs):return original(**kwargs,transport=httpx.MockTransport(reply))
            with self.subTest(outcome=outcome),patch.object(cloud.httpx,'AsyncClient',side_effect=client):
                result=asyncio.run(cloud.warm_level('https://example.invalid',{},['PRIVATE'],5,
                    {'warmup_requests':20,'k':10,'seconds_per_level':20,'requests_per_level':250}))
                self.assertEqual(result['status'],'aborted')
                self.assertEqual(result['phase'],'warmup')
                self.assertEqual(result['completed_requests'],0)
                self.assertEqual(result['warmup_failure_counts'],{category:1})
                self.assertFalse(result['warm_p95_target_met'])
                self.assertNotIn('SECRET',str(result));self.assertNotIn('PRIVATE',str(result))


class ResponseContractTests(unittest.TestCase):
    def test_malformed_success_items_cannot_pass_latency_gate(self):
        cloud=importlib.import_module('scripts.cloud_verify')
        body={'recommendation_mode':'personalized','items':[{}],
              'model_version':'logistic_fixed','release_version':'release_fixed',
              'data_as_of':'2011-05-01T00:00:00','score_kind':'raw_model_margin',
              'scores_are_calibrated_probabilities':False}
        for items in [[{}],[{'item_id':'A','ranking_score':float('inf'),'description':None}],
                [{'item_id':'A','ranking_score':True,'description':None}]]:
            with self.subTest(items=items),self.assertRaises((KeyError,TypeError,ValueError)):
                cloud.response_status(httpx.Response(200,text=json.dumps({**body,'items':items})),
                    'personalized',1)
