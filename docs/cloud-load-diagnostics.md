# One bounded diagnostic cloud run

The original five-client run remains 249/250 successful (0.4% unsuccessful).
Its status-0 exception type was not retained and cannot be recovered. A new run
can classify new failures, not establish the old failure's cause. No automatic
retries or repeated runs until success are used.

`warm_level.failure_counts` contains only fixed categories and library exception
class names: `transport:*`, `response_parse:*`, `response_schema:*`,
`response_contract_mismatch`, or `http_status:<code>`. Exception messages,
URLs, response bodies, tokens and customer IDs are omitted. HTTP status counts
and the existing zero-error + p95 gate remain unchanged. TypeError from malformed
response structure is now counted as a schema failure rather than aborting the
whole run. These are diagnostic classifications, not proof of service root cause.

The Codex runner has no gcloud executable/authorized Google identity. Execute
this once in the user's authorized Cloud Shell, retaining its frozen checkout
and original bundle. Do not pull into that checkout or rebuild/retrain anything.
The command loads only the new operator collector into an ignored file. It uses
five clients, 20 warmup requests, at most 250 measured requests, a 20-second
admission window and the existing 30-second per-request timeout. In-flight
requests may finish after the admission window. There is no cold-start claim.

```bash
(
  set +x
  set -euo pipefail
  cd "$HOME/repeat-order-recommender-release-b4accd8"
  export PATH="$HOME/.repeat-order-deploy-tools/bin:$PWD/.cache/google-cloud-sdk-install/google-cloud-sdk/bin:$PATH"
  export UV_CACHE_DIR="$PWD/.cache/uv"
  git fetch origin fix/cloud-load-diagnostics
  mkdir -p outputs/milestone4
  git show FETCH_HEAD:scripts/cloud_verify.py > outputs/milestone4/cloud_verify_diagnostic.py
  uv run --locked python - <<'PY'
import asyncio, importlib.util, json
from datetime import datetime, timezone
from pathlib import Path
from scripts.cloud_deploy import gcloud, privacy_preflight
from scripts.release_bundle import ReleaseBundle

output=Path('outputs/milestone4/cloudshell-diagnostic-once.json')
# Do not silently overwrite evidence or repeat an already attempted run.
with output.open('x') as f:
    json.dump({'status':'started; do not automatically retry'},f)
project='project-c0e2b13a-3cea-47e9-aad'
region='us-central1'
service='repeat-order-historical-demo'
privacy_preflight(project)
spec=importlib.util.spec_from_file_location('scripts.cloud_verify_diagnostic',
    'outputs/milestone4/cloud_verify_diagnostic.py')
collector=importlib.util.module_from_spec(spec)
spec.loader.exec_module(collector)
state=gcloud('run','services','describe',service,'--project',project,
             '--region',region,json_output=True)
revision=collector.serving_revision(state)
url=state['status']['url']
bundle=ReleaseBundle.load(Path('outputs/cloudshell-release/bundle'))
sa='repeat-demo-deployer@'+project+'.iam.gserviceaccount.com'
token=gcloud('auth','print-identity-token','--impersonate-service-account',sa,
             '--audiences',url,'--include-email')
headers={'Authorization':'Bearer '+token}
# Verify immutable release identity before sending recommendation traffic.
import httpx
with httpx.Client(headers=headers,verify=collector.tls_context(),timeout=30) as client:
    response=client.get(url+'/model');response.raise_for_status()
    metadata=response.json()
    assert metadata['release_id']==bundle.manifest['release_id']
    assert metadata['model_version']==bundle.manifest['model_version']
start=datetime.now(timezone.utc).isoformat()
load={'warmup_requests':20,'requests_per_level':250,'seconds_per_level':20,'k':10}
result=asyncio.run(collector.warm_level(url,headers,sorted(bundle.index),5,load))
report={'status':'completed','project':project,'region':region,'revision':revision,
        'release_id':metadata['release_id'],'start_utc':start,
        'end_utc':datetime.now(timezone.utc).isoformat(),'warm_level':result,
        'original_five_client_failure_preserved':True,
        'cold_start':'not measured','raw_customer_ids_reported':False}
output.write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps(report,indent=2))
PY
  cloudshell download outputs/milestone4/cloudshell-diagnostic-once.json
)
```

If setup or warmup fails, stop and report the sanitized failure category; do not
remove the started marker and retry without reviewing the failure. Warmup
failures abort the run rather than enter measured metrics. Upload the JSON for
analysis. A successful new sample does not resolve the original failure; a
transport classification also does not prove that the service was healthy.
