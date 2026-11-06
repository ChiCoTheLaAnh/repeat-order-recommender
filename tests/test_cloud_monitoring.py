import importlib
import json
import unittest
from unittest.mock import patch
import httpx


class MonitoringTests(unittest.TestCase):
    def test_alerts_scope_to_service_and_do_not_treat_missing_data_as_errors(self):
        module=importlib.import_module('scripts.cloud_monitoring')
        policies=module.alert_policies('projects/project-c0e2b13a-3cea-47e9-aad/notificationChannels/123')
        self.assertEqual(len(policies),2)
        for policy in policies:
            condition=policy['conditions'][0]['conditionThreshold']
            self.assertIn('repeat-order-historical-demo',condition['filter'])
            self.assertEqual(condition['comparison'],'COMPARISON_GT')
            self.assertEqual(condition['thresholdValue'],0)
            self.assertEqual(condition['aggregations'][0]['alignmentPeriod'],'300s')
            self.assertNotIn('customer_id',condition['filter'])
            self.assertNotIn('EVALUATION_MISSING_DATA_ACTIVE',str(policy))
        self.assertIn('response_code_class="5xx"',policies[0]['conditions'][0]['conditionThreshold']['filter'])
        with self.assertRaises(ValueError):module.alert_policies('arbitrary-email@example.org')

    def test_unverified_channel_cannot_apply_policies(self):
        module=importlib.import_module('scripts.cloud_monitoring')
        original=httpx.Client
        def reply(request):
            self.assertEqual(request.method,'GET')
            return httpx.Response(200,json={'verificationStatus':'UNVERIFIED','enabled':True})
        def client(**kwargs):return original(**kwargs,transport=httpx.MockTransport(reply))
        with patch.object(module.httpx,'Client',side_effect=client), \
             patch.object(module,'gcloud',return_value='SYNTHETIC-TOKEN'):
            with self.assertRaisesRegex(ValueError,'verified'):
                module.apply_alerts('projects/project-c0e2b13a-3cea-47e9-aad/notificationChannels/123')

    def test_inventory_uses_v1_dashboards_and_v3_alerts(self):
        module=importlib.import_module('scripts.cloud_monitoring')
        original=httpx.Client
        def reply(request):
            path=request.url.path
            if path.endswith('/dashboards'):
                self.assertTrue(path.startswith('/v1/'),path)
                return httpx.Response(200,json={'dashboards':[{'displayName':
                    'Historical recommendation demo — operational metrics'}]})
            self.assertTrue(path.startswith('/v3/'),path)
            if path.endswith('/alertPolicies'):return httpx.Response(200,json={})
            return httpx.Response(200,json={'verificationStatus':'VERIFIED','enabled':True})
        def client(**kwargs):return original(**kwargs,transport=httpx.MockTransport(reply))
        def google(*args,**kwargs):
            if args[:2]==('auth','print-access-token'):return 'SYNTHETIC'
            return {}  # Missing log metric config is not marked healthy.
        with patch.object(module.httpx,'Client',side_effect=client),patch.object(module,'gcloud',side_effect=google):
            result=module.inspect_monitoring('projects/project-c0e2b13a-3cea-47e9-aad/notificationChannels/123')
        self.assertTrue(result['dashboard_present'])
        self.assertFalse(result['notification_delivery_verified'])
        self.assertTrue(all(not item['filter_matches'] for item in result['log_metrics']))
        self.assertTrue(all(not item['enabled'] for item in result['alerts']))

    def test_omitted_verification_status_allows_configuration_without_delivery_claim(self):
        module=importlib.import_module('scripts.cloud_monitoring')
        original=httpx.Client
        def reply(request):
            if request.method=='POST':
                policy=json.loads(request.content)
                self.assertEqual(policy['notificationChannels'],[
                    'projects/project-c0e2b13a-3cea-47e9-aad/notificationChannels/123'])
                return httpx.Response(200,json={'name':'projects/demo/alertPolicies/'+str(len(policy['displayName']))})
            if request.url.path.endswith('/alertPolicies'):return httpx.Response(200,json={})
            return httpx.Response(200,json={'type':'email','enabled':True})
        def client(**kwargs):return original(**kwargs,transport=httpx.MockTransport(reply))
        with patch.object(module.httpx,'Client',side_effect=client),patch.object(module,'gcloud',return_value='SYNTHETIC'):
            result=module.apply_alerts('projects/project-c0e2b13a-3cea-47e9-aad/notificationChannels/123')
        self.assertEqual(result['status'],'configured')
        self.assertEqual(len(result['policies']),2)
        self.assertTrue(result['channel_usable'])
        self.assertFalse(result['channel_verified'])
        self.assertEqual(result['channel_verification_status'],'VERIFICATION_STATUS_UNSPECIFIED')
        self.assertFalse(result['notification_delivery_verified'])

    def test_disabled_or_unknown_verification_state_is_rejected_before_write(self):
        module=importlib.import_module('scripts.cloud_monitoring')
        original=httpx.Client
        for state in [{'verificationStatus':'VERIFIED','enabled':False},
                      {'verificationStatus':'FUTURE_UNKNOWN','enabled':True}]:
            def reply(request):
                self.assertEqual(request.method,'GET')
                return httpx.Response(200,json=state)
            def client(**kwargs):return original(**kwargs,transport=httpx.MockTransport(reply))
            with self.subTest(state=state),patch.object(module.httpx,'Client',side_effect=client), \
                 patch.object(module,'gcloud',return_value='SYNTHETIC'):
                with self.assertRaises(ValueError):
                    module.apply_alerts('projects/project-c0e2b13a-3cea-47e9-aad/notificationChannels/123')

    def test_inventory_treats_omitted_numeric_zero_as_default_but_rejects_real_drift(self):
        module=importlib.import_module('scripts.cloud_monitoring')
        original=httpx.Client
        channel='projects/project-c0e2b13a-3cea-47e9-aad/notificationChannels/123'
        common='resource.type="cloud_run_revision" AND resource.labels.service_name="repeat-order-historical-demo" AND resource.labels.location="us-central1"'
        for change,expected in [(None,True),({'thresholdValue':0},True),({'thresholdValue':1},False),
                ({'comparison':'COMPARISON_LT'},False),({'duration':'300s'},False),
                ({'filter':'wrong-service'},False),({'thresholdValue':False},False)]:
            actual=[]
            for title,metric,extra in [('Historical demo: service 5xx','run.googleapis.com/request_count',' AND metric.labels.response_code_class="5xx"'),
                    ('Historical demo: bundle load failed','logging.googleapis.com/user/repeat_demo_startup_failures','')]:
                threshold={'filter':common+' AND metric.type="'+metric+'"'+extra,
                    'comparison':'COMPARISON_GT','duration':'0s','trigger':{'count':1},
                    'aggregations':[{'alignmentPeriod':'300s','perSeriesAligner':'ALIGN_SUM','crossSeriesReducer':'REDUCE_SUM'}]}
                actual.append({'displayName':title,'enabled':True,'notificationChannels':[channel],
                    'conditions':[{'conditionThreshold':threshold}]})
            if change:actual[0]['conditions'][0]['conditionThreshold'].update(change)
            def reply(request):
                self.assertEqual(request.method,'GET')
                if request.url.path.endswith('/alertPolicies'):return httpx.Response(200,json={'alertPolicies':actual})
                if request.url.path.endswith('/dashboards'):return httpx.Response(200,json={})
                return httpx.Response(200,json={'enabled':True})
            def client(**kwargs):return original(**kwargs,transport=httpx.MockTransport(reply))
            def google(*args,**kwargs):return 'SYNTHETIC' if args[:2]==('auth','print-access-token') else {}
            with self.subTest(change=change),patch.object(module.httpx,'Client',side_effect=client),patch.object(module,'gcloud',side_effect=google):
                result=module.inspect_monitoring(channel)
            self.assertEqual(result['alerts'][0]['condition_matches'],expected)
            self.assertTrue(result['alerts'][1]['condition_matches'])
            self.assertFalse(result['notification_delivery_verified'])
