# Release reliability follow-up

This change prepares operational verification; it does not assert a new cloud
pass. The original cloud report, failed 5-client gate, and unconfirmed client
cold start remain authoritative. The selected logistic weights and runtime image
are unchanged. No held-out test, retraining or new recommendation policy is used.

## Provenance: two separate sources

`config/approved_cloud_release_v1.json` binds the previously reviewed image
digest, exact manifest SHA256, release/model identities and artifact-source
commit `b4accd82988a47798df4460f0c205cb95c2c7b74`. Changing that record requires
code review. Workflow inputs cannot override it. Existing file hashes, policy
compatibility, model anchor, temporal windows, image OCI labels and embedded
bundle checks are retained. The operator exports the approved artifact-source
commit from local Git into a temporary directory, verifies every bundled source
hash against that tree, and imports its original loader/scorer in an isolated
Python namespace. This preserves the existing runtime source-equality check;
there is no skip-integrity flag. The Git object must be available locally (the
release workflow checks out full history). No source is downloaded at startup.

Packaging, extraction and image construction still require the original clean
artifact-source checkout. Deployment can instead use a newer clean operator
checkout, provided the bundle and image match the committed approval exactly.
Approval is checked **before** joblib deserialization. Deployment receipts now
record `code_commit` (artifact source), `operator_code_commit`, approval contents
and approval-file SHA256 separately. A revision label records the operator
commit; it does not change model weights or image contents.

Offline verification with the recovered bundle:

```bash
uv run --locked python -m scripts.cloud_deploy verify-release \
  --bundle "$HOME/repeat-order-recommender-release-b4accd8/outputs/cloudshell-release/bundle" \
  --image "$(python3 -c "import json; print(json.load(open('config/approved_cloud_release_v1.json'))['image_digest'])")"
```

## One diagnostic run first

Follow [the diagnostic commands](cloud-load-diagnostics.md). Preserve its receipt
even if setup fails. Diagnose new failures without replacing the original
249/250 successful measurement. A passing new sample cannot recover the original
exception class. There is no retry-until-success loop.

## OIDC deployment and rollback rehearsal

After this PR is reviewed and merged to `main`, use the existing GitHub
`historical-demo` environment and configured WIF variables. There is no stored
service-account key. Do not dispatch the old workflow before the provenance fix
is on `main`; it rejects the old bundle against the new checkout.

From a clean checkout of that reviewed main revision:

```bash
gh workflow run release.yml --ref main \
  -f image_digest="$(python3 -c "import json; print(json.load(open('config/approved_cloud_release_v1.json'))['image_digest'])")" \
  -f bundle_manifest_sha256="$(python3 -c "import json; print(json.load(open('config/approved_cloud_release_v1.json'))['manifest_sha256'])")" \
  -F rehearse_rollback=true
```

The workflow pulls the approved immutable image, verifies its embedded bundle,
deploys it using existing resource limits, and runs offline/API smoke checks.
A newer operator label creates a new revision when the operator source changes.
If rehearsal is requested, it restores 100% traffic to the previously captured
revision **only if** that revision still references the approved image and
artifact-source label. It then repeats smoke checks using the same frozen bundle.
The rehearsal deliberately leaves the previous revision serving. A no-op traffic
restore is recorded as `changed_traffic: false`, not proof of switching revisions.

Download the workflow receipt artifact, review both deployment and rollback
smoke reports, and retain the exact image/revision/operator hashes. The workflow
has not been exercised merely because offline CI passes. Failure before the
rehearsal step leaves the deployed revision in place; use the manual rollback
procedure after inspecting the captured receipt. There is no automatic rollback.

Manual restore, from the clean reviewed operator checkout, using the downloaded
receipt (not a hand-written revision name):

```bash
uv run --locked python -m scripts.cloud_deploy rollback \
  --receipt outputs/ci-release/deployment.json --output outputs/ci-rollback
```

This verifies privacy, target image/source and final traffic and writes
`rollback.json`; `smoke_verified` stays false until a separate smoke report
exists. Follow the existing authenticated `--smoke-only` commands in
[cloud operations](cloud-operations.md) with the approved extracted bundle.
Do not call a rollback successful based on traffic control-plane state alone.
The command intentionally supports this frozen approved release only. A future
challenger requires a reviewed approval change and a revised rollback policy.

## Monitoring configuration and delivery

The existing bootstrap defines log-based metrics and the service dashboard.
`cloud_monitoring` adds two narrowly scoped infrastructure policies: any native
5xx count and any bundle-load-failure count in a 5-minute aligned window. These
are demo diagnostics, not business SLAs or statistical drift alerts. Native
latency, memory, startup, version and fallback signals stay on the dashboard.
No fallback-rate alert is added: the synthetic unknown-query mix is deliberate.

Select an existing **verified, enabled** notification channel resource in this
project using the Cloud Console. No email addresses or channel credentials go
into source. These commands do not create a contact or send a test message:

```bash
repeat_channel='projects/project-c0e2b13a-3cea-47e9-aad/notificationChannels/REPLACE_WITH_CHANNEL_NUMBER'
uv run --locked python -m scripts.cloud_monitoring prepare --channel "$repeat_channel" \
  --output outputs/milestone4/alerts-prepared.json
uv run --locked python -m scripts.cloud_monitoring apply --channel "$repeat_channel" \
  --output outputs/milestone4/alerts-applied.json
uv run --locked python -m scripts.cloud_monitoring inspect --channel "$repeat_channel" \
  --output outputs/milestone4/monitoring-inventory.json
```

An authorized Monitoring editor is needed to apply; inspection needs Monitoring
and log-metric read permissions. The deploy service account remains read-only
for monitoring. Inspect reports are inventories: check each field rather than
treating exit 0 as all configured correctly. Metric existence does not prove
sample export, and channel verification does not prove alert delivery.

In Cloud Console, review the scoped dashboard against recorded native samples
and synthetic request counts. Inspect recent sanitized application logs by
release/model version. Review notification-channel configuration and, when ready,
use its test-notification facility if available and record receipt at the chosen
destination. Do not deliberately corrupt the live bundle or generate 5xx to
force an incident. If end-to-end condition/notification testing is unavailable,
record that gap; do not claim delivery from policy creation alone. Missing metric
samples remain unavailable, never zero or proof of a healthy service.

## Current execution limits

The managed environment reports no outbound Google identity and no credential
bindings. Its gcloud invocation selects an empty read-only credential directory;
project/region variables alone provide no authorization. Consequently no new
cloud requests, deployments, traffic switches, alert changes or delivery tests
are claimed here. The user's authorized Cloud Shell and GitHub OIDC workflow
remain the supported execution paths. No token or service-account key is needed
in chat.
