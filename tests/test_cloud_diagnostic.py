import importlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch


class DiagnosticReceiptTests(unittest.TestCase):
    def test_setup_failure_writes_sanitized_receipt_and_refuses_repeat(self):
        module=importlib.import_module('scripts.cloud_diagnostic')
        with tempfile.TemporaryDirectory() as temp:
            output=Path(temp)/'once.json'
            with patch.object(module,'approved_release',side_effect=ValueError('SECRET CUSTOMER URL')):
                result=module.diagnose(Path(temp)/'bundle',output)
            self.assertEqual(result['status'],'failed')
            self.assertEqual(result['phase'],'approval')
            self.assertEqual(result['failure_category'],'validation:ValueError')
            self.assertNotIn('SECRET',output.read_text())
            before=output.read_bytes()
            with self.assertRaises(FileExistsError):module.diagnose(Path(temp)/'bundle',output)
            self.assertEqual(output.read_bytes(),before)

    def test_gcloud_stderr_is_never_in_failure_receipt(self):
        module=importlib.import_module('scripts.cloud_diagnostic')
        with tempfile.TemporaryDirectory() as temp:
            output=Path(temp)/'once.json'
            with patch.object(module,'approved_release',return_value={}), \
                 patch.object(module,'gcloud',side_effect=subprocess.CalledProcessError(
                     1,['gcloud'],stderr='SECRET account token URL')):
                result=module.diagnose(Path(temp)/'bundle',output)
            self.assertEqual(result['phase'],'identity')
            self.assertEqual(result['failure_category'],'setup:GoogleCommandFailed')
            self.assertNotIn('SECRET',output.read_text())
            self.assertEqual(json.loads(output.read_text()),result)

    def test_single_bounded_run_persists_new_failure_without_retrying(self):
        module=importlib.import_module('scripts.cloud_diagnostic')
        from types import SimpleNamespace
        from tests.test_cloud_verify import valid_response
        import httpx
        approval=json.loads(Path('config/approved_cloud_release_v1.json').read_text())
        fake=SimpleNamespace(index={'PRIVATE': [0,200]},manifest={
            'release_id':approval['release_id'],'model_version':approval['model_version'],
            'data_as_of':'2011-05-01T00:00:00'})
        metadata={**fake.manifest,'provenance':{'code_commit':approval['artifact_source_commit']}}
        requests=[]
        def google(*args,**kwargs):
            if args[:2]==('auth','list'):return 'PRIVATE-ACCOUNT'
            if args[:3]==('run','services','describe'):
                return {'status':{'url':'https://example.run.app','traffic':[{'revisionName':'old','percent':100}]},
                    'spec':{'template':{'spec':{'containers':[{'image':approval['image_digest']}]}}}}
            return 'SECRET-TOKEN'
        original_async,original_sync=httpx.AsyncClient,httpx.Client
        def async_reply(request):
            requests.append(request)
            if len(requests)==25:raise httpx.ReadTimeout('SECRET private URL',request=request)
            mode='popularity_fallback' if 'unknown-cloud-load' in request.url.path else 'personalized'
            return httpx.Response(200,json=valid_response(mode,10))
        def async_client(**kwargs):return original_async(**kwargs,transport=httpx.MockTransport(async_reply))
        def sync_client(**kwargs):return original_sync(**kwargs,
            transport=httpx.MockTransport(lambda request:httpx.Response(200,json=metadata)))
        with tempfile.TemporaryDirectory() as temp,patch.object(module,'approved_release',return_value=approval), \
             patch.object(module,'load_approved_bundle',return_value=fake), \
             patch.object(module,'privacy_preflight',return_value={'checked':True}), \
             patch.object(module,'gcloud',side_effect=google), \
             patch.object(httpx,'AsyncClient',side_effect=async_client), \
             patch.object(httpx,'Client',side_effect=sync_client):
            output=Path(temp)/'once.json'
            result=module.diagnose(Path(temp)/'bundle',output)
            self.assertEqual(result['status'],'completed')
            self.assertEqual(result['warm_level']['completed_requests'],250)
            self.assertEqual(result['warm_level']['successful_requests'],249)
            self.assertEqual(result['warm_level']['failure_counts'],{'transport:ReadTimeout':1})
            self.assertFalse(result['warm_level']['warm_p95_target_met'])
            self.assertEqual(len(requests),270)
            self.assertNotIn('PRIVATE',output.read_text());self.assertNotIn('SECRET',output.read_text())
