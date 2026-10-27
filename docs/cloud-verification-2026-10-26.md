# Cloud verification: October 26, 2026

The historical demo was deployed and its API smoke checks passed from the
user's authorized Google Cloud Shell. The frozen logistic model, cleaning,
retrieval V1, features and label definitions are unchanged; the original test
split remains unevaluated. Operational verification is **partial**: the
five-client run contains one unsuccessful attempt, native Monitoring collection
failed, and client-perceived cold-start latency is unconfirmed.

## Evidence and release

- URL: <https://repeat-order-historical-demo-yqfhlbro6a-uc.a.run.app>.
  IAM authentication is required; this is not a public recommendation endpoint.
- Project: `project-c0e2b13a-3cea-47e9-aad`; region: `us-central1`.
- Revision: `repeat-order-historical-demo-00001-2jz`.
- Runtime source commit: `b4accd82988a47798df4460f0c205cb95c2c7b74`.
  Later documentation commits do not replace the deployed runtime source.
- Image: `us-central1-docker.pkg.dev/project-c0e2b13a-3cea-47e9-aad/repeat-order-demo/service@sha256:71958cb3350563b0099fb700168978435e5df42f291d7a7212403792de7713ae`.
- Release: `release_v1_35f8ccbb4cd8b9ada5b40696103a25e8f3969f3a54081e310a84354fd81221f6`.
- Model: `logistic_dd79f226da8964d6`; original model SHA256:
  `dd79f226da8964d6607185d6905829404ecd126dfaa65848d3bdc555f576f17d`.
- Bundle manifest SHA256:
  `fb62b8e85893bb2bb58526f8680aa78e94e24960eabee54cad2bf41c1955c742`.
- Serving data is strictly before May 1, 2011. Model training labels end
  February 1, 2011; validation selection labels end May 1, 2011.

The uploaded `repeat-order-cloud-verification.tar.gz` has SHA256
`8439f07af5925941b1d162621dbafc5ad84e8408cc07faf60645da98a4fc6104`.
Its four JSON files contain the deployment receipt, rendered service spec,
remote verification report and client hardware. They were inspected offline
for matching project/release/source/model/manifest/image anchors and consistent
count, error-rate, throughput and gate arithmetic. Raw timing samples were not
provided, so percentiles cannot be independently recalculated. The Codex
runner did not obtain its own Google identity or independently repeat these
cloud requests. Raw receipts remain in ignored `outputs/`; this document is
the curated public summary and contains no customer IDs or credentials.

The deployment helper verified the image's embedded bundle without executing
it, checked clean-source provenance and temporal windows, and passed privacy
preflight before deployment. Project log sinks were checked; the organization
had zero aggregated sinks. No privacy checks were disabled.

## Conditions

Cloud Run uses request-based billing, 1 vCPU / 1 GiB, minimum 0 and maximum 1
instances, concurrency 5, a 30-second request timeout, `/ready` startup and
`/health` liveness probes. The immutable image includes the bundle; startup
does not download or train. The physical Cloud Run host CPU is undisclosed.

The client was Google Cloud Shell: AMD EPYC 7B12, four visible and affinity
CPUs, Linux 6.18.55+, glibc 2.39. The hardware collector ran under system Python
3.12.3; the verifier uses the locked Python 3.12.14 environment. None of the
checked cgroup v1/v2 limit files were readable/present, so effective client
CPU quota and memory limit are **unknown**. Visible CPUs are not a quota claim.

The run interval was October 26, 2026, **22:15:09–22:15:39 CDT**
(`2026-10-27T03:15:09.263908Z` through `03:15:39.775825Z`). Each level used
20 excluded warmup requests and stopped after 250 measured requests, before
its 20-second cap. These are short closed-loop samples, not 20-second sustained
capacity tests. Requests used HTTPS, K=10, and an attempted mix of 225 known
customers / 25 unknown-customer fallbacks per level. Timings include client
networking and TLS; traffic is synthetic, not evidence of customer behavior.

## Results

Health, readiness and release metadata checks passed. Known-customer API
rankings/scores matched the offline bundle at lookup positions 0, 1221 and
2442. Unknown-customer fallback and invalid K checks passed. One instance
start ID was observed across the requests which returned that header.

| Clients | Successful / attempted | Measured duration | All-attempt p50 | All-attempt p95 | Completed requests/sec | Successful requests/sec | Error rate |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 250 / 250 | 4.374 s | 16.867 ms | 20.300 ms | 57.159 | 57.159 | 0% |
| 5 | 249 / 250 | 10.048 s | 17.584 ms | 27.125 ms | 24.881 | 24.782 | 0.4% |

At five clients, successful-response p95 was 26.571 ms. All-attempt mean was
119.078 ms, versus 79.236 ms for successful responses, showing that the small
p95 does not describe the entire tail. The unsuccessful attempt was recorded
as synthetic status **0**, not an HTTP status. The frozen verifier assigns 0
to HTTP-client/transport exceptions and response JSON/key errors; it does not
retain the exception class. The report therefore cannot establish whether
this was a network, timeout or response-parsing failure. There were no recorded
HTTP 4xx/5xx responses in these measured attempts; status 0 is not proof of a
server 500 or a model error.

The **numerical 250 ms p95 threshold was met at both levels**. The existing
`warm_p95_target_met` flag also requires zero errors, so it is true at one
client and **false at five clients**. This stricter combined gate was not
changed or reinterpreted as a clean pass. No repeated run was substituted to
remove the failure, and the observed rates are not a throughput guarantee.

Bundle validation/loading took **2,105.661 ms**. `/ready` reported process RSS
**442.203 MiB** before measured load and **444.609 MiB** after load, with a
process-lifetime peak of **515.273 MiB**. This peak includes startup. These are
API-process RSS/high-water readings, not whole-container or native Cloud Run
memory utilization; they do not prove the absence of all memory pressure.

The first recommendation request took **14,367.935 ms** and returned process
uptime 3.769 seconds. There was no preceding scale-to-zero observation, so
`confirmed_client_cold_start_ms` remains **null**. Neither the first request
nor the bundle-load duration is presented as confirmed end-to-end cold latency.

Native metrics collection returned `unavailable: HTTPStatusError` with an empty
metrics object. A subsequent Cloud Shell diagnostic established **HTTP 400**:
`Invalid metric name: run.googleapis.com%2Fcontainer%2Fstartup_latencies`.
The helper percent-encoded slashes in the metric descriptor path. The collector
now preserves literal `/` separators; a regression test first reproduced the
encoded request and then passed with the fix. This is a collector URL bug,
not evidence of missing IAM permission. It does not change the deployed model,
metric definitions, load policy or original run's results. Native startup,
request-count and container-memory observations still require a successful
follow-up collection; none are retroactively filled into this original report.

## Remaining work and release decision

1. Recollect native metrics with the corrected metric descriptor URL. The
   original HTTP 400 has been diagnosed and fixed in the collector. Do not add
   IAM roles to address this URL bug; read existing metrics without generating
   new recommendation traffic, and diagnose any subsequent error separately.
2. After all clients stop, attempt the separate zero-instance-evidenced cold
   experiment in the [Cloud Shell guide](cloud-shell-deployment.md). Missing
   instance-count samples remain unknown, not zero.
3. Investigate the status-0 attempt. A subsequent bounded diagnostic run may
   record exception classes without URLs/customer IDs; retain this original
   failed-gate result and do not claim its cause from these aggregates alone.
4. GitHub OIDC deployment and rollback have not been exercised by this manual
   Cloud Shell run. The deployment receipt has no previous service to restore;
   this is the first recorded revision. Existing documented CI/rollback steps
   still require their own verification.

Keep the selected logistic model for this IAM-protected historical demo. Smoke
equivalence verifies the serving implementation, not model quality or business
lift. Cloud verification is not fully closed, and this small synthetic run does
not justify an unrestricted production launch, additional tuning or opening
the original held-out test.
