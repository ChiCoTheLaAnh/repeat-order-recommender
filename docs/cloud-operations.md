# Historical demo operations — Milestone 4

The selected model remains the original training-only **logistic regression**.
Cleaning, retrieval V1, recommendation scope, feature definitions, labels and
the original test are frozen. No retraining or test evaluation occurs in this
milestone. Serving remains exclusive **2011-05-01 00:00:00**, in the workbook's
timezone-naive convention. The eleven March 2010–January 2011 training windows
end no later than **2011-02-01**; validation selection ends **2011-05-01**.
The builder checks actual train-filtered snapshot windows and original hashes.
May 1 therefore satisfies temporal consistency without opening test rows.

The original M3 bundle recorded dirty source accurately. It is not deployable.
M1–M3 implementation was committed first; M4 requires a clean committed checkout
before building. Manifest/source/config hashes, the exact commit, dependency
versions, training/selection dates and the unchanged logistic artifact identify
the release. `config/logistic_artifact_v1.json` anchors that fitted model hash
before deserialization. A model update requires a reviewed new anchor/version.

## Target and prerequisites

- Project: `project-c0e2b13a-3cea-47e9-aad`; region: `us-central1`.
- Artifact Registry Docker repository: `repeat-order-demo`.
- Cloud Run service: `repeat-order-historical-demo`.
- Request-based billing, **1 vCPU / 1 GiB**, min **0**, max **1** at both service
  and revision levels, concurrency **5**, request timeout **30 seconds**.
- `/ready` startup probe gates traffic; `/health` is process liveness. Cloud Run
  has startup and liveness probes; readiness is also explicitly smoke-tested.
- The image embeds the read-only validated bundle. Startup only loads/scores;
  it never downloads data, builds features or fits a model.
- Service access requires IAM authentication. Public access was not requested.
  The runtime account needs no application data/model access roles.

Cloud deployment has **not run** in this workspace. The injected Google ADC
files are empty JSON objects and the SDK has zero active accounts. Connections
to `run.googleapis.com` are rejected by the outbound proxy with HTTP CONNECT
403 before reaching Google. The target project is configured, but its billing,
permissions, APIs and resources cannot be verified with this access.

Apply the saved environment network draft (Google APIs, Artifact Registry,
Cloud Run service domains and identity endpoints) and connect an authorized
Google identity through the environment's credential facility. Do not paste
keys/tokens into Git or chat. One-time setup needs permission to enable APIs,
create the repository/service accounts/WIF/custom role, bind IAM, configure log
exclusions and create monitoring definitions, plus an enabled billing account.
Those checks are currently blocked; no Google service-account key is created.

Install the pinned, SHA-verified SDK locally if it is absent:

```bash
bash scripts/install_gcloud.sh
export PATH="$PWD/.cache/google-cloud-sdk-install/google-cloud-sdk/bin:$PATH"
uv sync --locked
uv run --locked python -m scripts.bootstrap_cloud          # local definitions only
uv run --locked python -m scripts.bootstrap_cloud --apply  # authorized admin setup
```

The helper honors a writable existing SDK profile and valid injected ADC. If
the injected SDK directory is read-only, it uses ignored `.cache/gcloud`.
Interactive local `gcloud` commands need the same writable configuration, e.g.
`export CLOUDSDK_CONFIG="$PWD/.cache/gcloud"`. Use your authorized identity;
installing the SDK alone does not authenticate it.

`bootstrap_cloud.py` creates only the demo's repository, runtime/deployer
accounts, GitHub identity trust, IAM bindings, additive log exclusions, four
log-based metrics and a dashboard. The deployer receives Cloud Run developer
and invoker, repository writer, monitoring/log viewer and sink-list permissions,
and may act as the runtime account. Project-level Run roles are a demo setup
tradeoff; use service-scoped bindings for a separately provisioned production
service. No databases, events, queues or hosted tracking are added.

## Build and publish from the same clean commit

Keep source on the reviewed release commit. M2 models and M3 selection evidence
must already exist; a deployable build fails instead of fitting anything.
Rebuild after merging if the merge changes the source commit.

```bash
uv run --locked python -m scripts.build_release --deployable
release_id=$(python -c "import json; print(json.load(open('outputs/releases/latest.json'))['release_id'])")
bundle="$PWD/outputs/releases/$release_id"
uv run --locked python -m scripts.cloud_deploy package --bundle "$bundle"
uv run --locked python -m scripts.cloud_deploy context \
  --bundle "$bundle" --output "outputs/cloud-context/$release_id"
docker build -t repeat-order-recommender:milestone3 .
source_commit=$(git rev-parse HEAD)
manifest_sha=$(sha256sum "$bundle/manifest.json" | cut -d' ' -f1)
image_tag="us-central1-docker.pkg.dev/project-c0e2b13a-3cea-47e9-aad/repeat-order-demo/service:$release_id"
docker build -f "outputs/cloud-context/$release_id/Dockerfile" \
  --build-arg SERVICE_IMAGE=repeat-order-recommender:milestone3 \
  --build-arg SOURCE_COMMIT="$source_commit" --build-arg RELEASE_ID="$release_id" \
  --build-arg BUNDLE_MANIFEST_SHA256="$manifest_sha" -t "$image_tag" \
  "outputs/cloud-context/$release_id"
gcloud auth configure-docker us-central1-docker.pkg.dev
docker push "$image_tag"
digest=$(gcloud artifacts docker images describe "$image_tag" \
  --format='value(image_summary.digest)')
image="${image_tag%:*}@$digest"
uv run --locked python -m scripts.cloud_deploy deploy --bundle "$bundle" --image "$image"
```

Use the README's BuildKit proxy/CA command for the base image in this managed
workspace. The second image build uses the local base; it adds only the bundle
and OCI provenance labels. A local Docker image ID is **not** a registry digest.
Only a successfully pushed/resolved `@sha256:...` URI can be deployed. The
service spec and deployment receipt retain prior traffic/revision/image state.
Archives, model artifacts, bundles, data, contexts and receipts are ignored.

## GitHub Actions

`.github/workflows/ci.yml` installs `uv.lock`, runs offline tests and builds
Docker for PRs and main. It has no cloud credentials. Third-party Actions are
pinned to verified commit SHAs.

Create the GitHub environment **historical-demo**, restrict it to main, and
set repository/environment variables:

| Variable | Value |
| --- | --- |
| `GCP_PROJECT_ID` | `project-c0e2b13a-3cea-47e9-aad` |
| `GCP_REGION` | `us-central1` |
| `GCP_WIF_PROVIDER` | Provider resource returned by `bootstrap_cloud --apply` |
| `GCP_DEPLOY_SERVICE_ACCOUNT` | `repeat-demo-deployer@project-c0e2b13a-3cea-47e9-aad.iam.gserviceaccount.com` |

Run **Deploy historical demo** on the exact main commit used to build the image.
Supply the full immutable image URI and reviewed manifest SHA256. The job uses
GitHub OIDC/WIF, restricted to repository numeric ID **1406464839**, main and
the environment subject. There is no stored service-account key. It extracts
the bundle without running the image, checks manifest/commit/model/source/
configuration/dependencies, then deploys the digest and obtains a short-lived
invoker token for health, readiness, metadata, known-customer equivalence and
unknown fallback smoke checks. Receipts are uploaded without bundle contents,
customer IDs or credentials. Generated OIDC credential files are Git/Docker
ignored. Identity/resource setup and workflow execution remain unverified
until Google access is available.

## Monitoring and privacy

Application JSON logs include route **templates**, status, latency, mode,
release/model version, startup instance ID and process memory. Raw customer IDs,
URL paths and query strings are not emitted; uvicorn access logs are disabled.
Cloud Run's automatic request logs contain raw URLs, so bootstrap adds the
service-specific `historical-demo-request-privacy` exclusion to **every active
editable project sink**, including custom exports, before traffic. Deployment
and verification fail if an exclusion is absent/disabled/changed. `_Required`
is exempt because it does not route these application request logs. Existing
logs are not deleted. Deployment/verification also inspect ancestor folder/
organization aggregated sinks. If those exporters lack the same exclusion or
cannot be inspected, preflight fails. An organization admin must configure
their exclusions and grant the deployer sink-list access there; bootstrap only
modifies this project's sinks. A project without ancestors needs no extra
bindings. Native Cloud Run metrics still work.

| Signal | Meaning and limits |
| --- | --- |
| Native request latency/errors | Frontend/container service health, including queue/startup effects; not ranking quality or business lift. |
| Application handler latency | Handler/scoring time; excludes network and frontend queue time. |
| Fallback count/rate | Successful fallback requests / successful recommendation requests, by release/model; synthetic unknown IDs are not customer churn or drift. |
| Bundle-load failures/startup latency | Artifact/runtime problems and native container-startup distributions; native startup time is not client-perceived cold HTTP latency. |
| Native memory utilization; `/ready` RSS | Container memory pressure and application process RSS/high-water RSS, respectively; different denominators. |
| Release/model version | Which artifact served traffic; does not establish recommendation quality. |

The generated dashboard covers these signals. Inspect native 4xx/5xx and
sanitized application errors separately: IAM/front-end errors may never reach
the application. Metrics export can lag minutes; missing points remain
unavailable, never zero. Small synthetic load tests say nothing about real
purchase behavior. This milestone adds no live statistical drift alerts or
automated retraining.

## Remote smoke and bounded measurements

After deployment, use the receipt's URL and an authorized invoker identity:

```bash
cloud_url=$(python -c "import json; print(json.load(open('outputs/milestone4/deployment.json'))['url'])")
uv run --locked python -m scripts.cloud_verify --url "$cloud_url" --bundle "$bundle"
```

For service-account impersonation, supply an audience-specific short-lived ID
token in `CLOUD_RUN_ID_TOKEN` without echoing it. CI handles this through OIDC.
The load is closed-loop HTTPS from the invoking machine, 200 stored candidates,
K=10, synthetic 90% known / 10% fallback. Each 1/5-client level excludes 20 warmup
requests and stops at 20 seconds or 250 measured requests (in-flight requests
have a 30-second timeout). Report actual duration, count, all-attempt and
successful p50/p95, throughput, error rate, instance changes and memory. Warm
p95 ≤250 ms at five clients is an engineering target, not a guarantee; the
report records whether it is met. Client networking/TLS is included. Native
metrics and process RSS are recorded separately. Local measurements must never
be substituted for these cloud results.

Cold start is a separate isolated experiment: stop other clients/probes, wait
for scale-to-zero, inspect native instance-count samples/current state, and
save the observation as JSON with `project`, `region`, `service`,
`active_instances: 0`, `observed_at` (UTC ISO date). Pass it with
`--cold-zero-evidence <file>`. It must match the service and be ≤120 seconds old;
the first response must show a newly started process (uptime ≤10 seconds).
The report preserves that evidence and its limits; delayed metrics cannot
guarantee an instantaneous zero count. Without suitable evidence the first
request is **unconfirmed**, and cold latency is null. Native container-startup
distributions are always distinguished from end-to-end cold latency.

Current cloud results: **not measured**, URL/revision/digest unavailable.
Warm p50/p95, throughput, cloud memory/error rate and cold-start latency are
unavailable because Google credentials/network access are missing. The ignored
`outputs/milestone4/` receipts distinguish preparation, actual local verification
and blocked cloud verification. No cloud capacity claim is made.

## Rollback

Keep the previous deployment receipt and immutable image. To restore its known
ready revision, use its recorded name (not `latest`):

```bash
gcloud run services update-traffic repeat-order-historical-demo \
  --project project-c0e2b13a-3cea-47e9-aad --region us-central1 \
  --to-revisions="<previous-ready-revision>=100"
```

Smoke-test using that revision's matching old bundle and metadata. If the
revision is unavailable, check out its clean source commit and redeploy its
recorded image digest with its matching bundle. Do not pair current code with
old artifacts. Investigate sanitized logs, startup/readiness and memory before
another promotion. A failed post-deploy smoke requires manual rollback;
workflow success alone is not a model-quality decision.

## Manual model and data updates

1. Record new data provenance and a completed observation watermark. Preserve
   raw files and frozen cleaning/eligibility policies. Refresh serving history,
   candidates, display metadata and fallback strictly before a declared cutoff;
   anonymous purchases remain catalog-only. Require all existing training and
   selection windows to finish by that cutoff. The current V1 builder deliberately
   fixes May 1; a later refresh requires a reviewed new release configuration/
   builder version, not an arbitrary request date or silent V1 edit.
2. Retrain only when new training target months fully mature. Predeclare later
   chronological train/validation windows and keep the original test unopened.
   Fit preprocessing on training only; keep equal query weights, full retrieved
   pools and frozen features/metrics. No unretrieved positives are injected.
3. Compare a challenger and the incumbent on the same later temporal validation
   pools. Inspect full-target NDCG/recall/oracles, repeat/discovery, segments,
   customer-cluster paired intervals, retrieval failures, latency and memory.
   Document losses honestly; uncertainty is not proof of business lift.
4. Promote manually only with a justified quality/operational tradeoff. Review
   the new model anchor/configuration, commit clean source, generate immutable
   bundle/image, verify maturity/hash/schema and stage smoke/load tests. Retain
   the incumbent digest/receipt for rollback. Reject an inconclusive or worse
   challenger and record the evidence. Current logistic selection is unchanged.

The optional deprecation-as-error check fails before application tests because
Starlette's test adapter references AnyIO's deprecated `BlockingPortal` alias.
Locked normal tests and real service execution are the relevant compatibility
checks; this warning alone does not demonstrate a serving failure. A future
dependency upgrade should test both adapter and runtime rather than suppress
all warnings or change frozen model dependencies during this release.
