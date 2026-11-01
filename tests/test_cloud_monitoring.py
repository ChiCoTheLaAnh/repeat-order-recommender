import importlib
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
