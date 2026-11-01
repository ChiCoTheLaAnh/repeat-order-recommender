# One bounded diagnostic cloud run

The original five-client run remains 249/250 successful (0.4% unsuccessful).
Its status-0 exception type was not retained and cannot be recovered. A new run
classifies new failures; even an error-free sample cannot explain the old one.
No automatic retries or repeated runs until success are used.

`warm_level.failure_counts` contains only fixed categories and library exception
class names: `transport:*`, `response_parse:*`, `response_schema:*`,
`response_contract_mismatch`, or `http_status:<code>`. Exception messages,
URLs, response bodies, tokens and customer IDs are omitted. HTTP status counts
and the original zero-error + p95 gate are preserved. Response structure failures
are counted without printing invalid content. Warmup failures abort before
measured traffic and are recorded separately in `warmup_failure_counts`.

`scripts.cloud_diagnostic` writes a receipt before approval, auth or network work.
Setup failures retain the phase and a sanitized category; an existing receipt
prevents any repeat, even after setup failure. Abrupt process termination may
leave only the started receipt; this is incomplete evidence, never a pass.

## Cloud Shell: execute once

Use the user's authorized Cloud Shell. The Codex runner has no ready Google
identity; configured project/region variables do not authenticate it. Do not
paste tokens or keys into chat. These commands use a separate operator checkout;
the original detached `b4accd8` checkout and ignored bundle stay unchanged.
Do not rebuild or retrain if the bundle is missing: stop and recover the exact
checksummed release instead.

```bash
(
  set +x
  set -euo pipefail
  repeat_ops_dir="$HOME/repeat-order-operations"
  test ! -e "$repeat_ops_dir"
  git clone --single-branch --branch fix/cloud-load-diagnostics \
    https://github.com/ChiCoTheLaAnh/repeat-order-recommender.git "$repeat_ops_dir"
  cd "$repeat_ops_dir"
  export PATH="$HOME/.repeat-order-deploy-tools/bin:$HOME/repeat-order-recommender-release-b4accd8/.cache/google-cloud-sdk-install/google-cloud-sdk/bin:$PATH"
  export UV_CACHE_DIR="$PWD/.cache/uv"
  uv sync --locked
  uv run --locked python -m scripts.cloud_diagnostic \
    --bundle "$HOME/repeat-order-recommender-release-b4accd8/outputs/cloudshell-release/bundle" \
    --output outputs/milestone4/cloudshell-diagnostic-once.json
)
```

After the command finishes, download its JSON **even if the command exits 1**:

```bash
cloudshell download "$HOME/repeat-order-operations/outputs/milestone4/cloudshell-diagnostic-once.json"
```

Do not rerun or remove the receipt. If this operator directory already exists,
inspect it and its receipt rather than cloning/running again. Upload the JSON
for analysis. A setup failure does not imply a service failure.

The test checks privacy exclusions and immutable release identity before
recommendation traffic. It uses five clients, 20 excluded warmup requests,
at most 250 measured requests, a 20-second admission window, K=10 and the
existing 30-second per-request timeout. In-flight requests can finish after the
admission window. The mix is 90% known queries and 10% unknown fallback; no raw
IDs are reported. A metadata request precedes warmup. There is no cold-start
claim, no deployment, and no held-out test evaluation. Collector/lock hashes,
service revision, timestamps and basic client conditions are saved.

Exit 0 means this sample completed and met the original combined zero-error /
p95 <=250 ms gate. Exit 1 means failed setup, aborted warmup, or a completed run
which missed that gate; consult the receipt. Exit 2 refuses an existing receipt.
Transport categories locate the observed failure boundary; they do not prove
that the service was healthy or determine an underlying network cause.
