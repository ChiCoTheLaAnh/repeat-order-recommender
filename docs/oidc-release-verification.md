# OIDC deployment and rollback verified — November 5, 2026

GitHub release run [42149437150, attempt 3](https://github.com/ChiCoTheLaAnh/repeat-order-recommender/actions/runs/42149437150/attempts/3)
completed successfully. GitHub job statuses confirm initial WIF authentication,
image/bundle validation, deployment, both smoke checks and rollback all passed.
The user's uploaded receipt ZIP was inspected offline; the Codex runner did not
obtain a Google identity or issue additional recommendation/load requests.

## Evidence and immutable release

The uploaded artifact `historical-demo-deployment-2b21696656722163e9f9b2c41f60a031e9ea2251.zip`
has SHA256 `f5c04a472fabfa4300927cf2bba036a7ebcf46f4b33ad1a65b71219e9bd32333`.
Its five JSON files contain the deployment receipt, rendered service spec,
post-deployment smoke report, rollback receipt and post-rollback smoke report.
Raw receipts remain in ignored `outputs/milestone4/received-oidc-attempt3/`.

- Project/region: `project-c0e2b13a-3cea-47e9-aad`, `us-central1`.
- Artifact source: `b4accd82988a47798df4460f0c205cb95c2c7b74`.
- Deployment operator: `2b21696656722163e9f9b2c41f60a031e9ea2251`.
- Model: `logistic_dd79f226da8964d6`, unchanged.
- Image digest: `sha256:71958cb3350563b0099fb700168978435e5df42f291d7a7212403792de7713ae`.
- Bundle manifest SHA256: `fb62b8e85893bb2bb58526f8680aa78e94e24960eabee54cad2bf41c1955c742`.
- Approval-file SHA256: `b34ebe202c7c9c5c21b5e59cd683889d4a750b16b24cb87667091b613157d76a`.

Release/model/manifest/image values match the committed approval. The rollback
receipt's deployment-receipt hash also matches the exact uploaded JSON bytes.
No model fitting, dependency changes, policy changes or held-out model evaluation
were part of this workflow. `warm_levels` is empty in both smoke reports: these
are functional checks, not another load benchmark.

## Actual traffic change and smoke results

| Stage | Revision receiving 100% traffic | Result |
| --- | --- | --- |
| Before deployment | `repeat-order-historical-demo-00001-2jz` | Captured in previous service state |
| After deployment | `repeat-order-historical-demo-00002-svx` | Smoke passed |
| After rollback | `repeat-order-historical-demo-00001-2jz` | Restored traffic; smoke passed |

`changed_traffic` is **true**. The rollback's previous-serving revision equals
the deployed revision; its restored revision equals the original revision and
the final service-state traffic entry. Both smoke-report revision names agree
with these states. This was a real switch between distinct revisions of the
same approved frozen image, not a no-op or a different-model rollback.

Both smoke checks passed health, readiness, release metadata, known-customer
API/offline equivalence at lookup positions 0, 1221 and 2442, unknown-customer
`popularity_fallback`, and invalid-K behavior. No raw customer IDs are reported.
The deployment smoke interval is November 5 **20:33:27–20:33:28 CST**
(`2026-11-06T02:33:27.186076Z`–`02:33:28.048978Z`); rollback smoke is
**20:33:45–20:34:05 CST** (`02:33:45.953120Z`–`02:34:05.124512Z`).

The control-plane rollback receipt retains `smoke_verified: false` by design:
that command does not run smoke. The separate successful post-rollback report
supplies functional verification; the original receipt was not rewritten.
The receipt records IAM-authenticated access and passed project/ancestor privacy
preflight. Rendered resource limits remain 1 vCPU / 1 GiB, concurrency 5,
request-based billing and min 0/max 1. This is a timestamped verification record,
not an independent assertion about all future service state or reliability.

## Earlier failures and fixes

- Attempt 1: Google rejected the legacy name-only subject. Repository OIDC uses
  immutable owner/repository IDs. The user updated the provider's exact subject
  while retaining repo-ID, main-ref and environment restrictions; see the
  [auth diagnosis](oidc-auth-failure.md). Attempt 2 then authenticated successfully.
- Attempt 2: privacy preflight failed at `projects get-ancestors`. The same
  command under deployer impersonation identified an unenabled Resource Manager
  API. The user enabled `cloudresourcemanager.googleapis.com` with the admin
  identity, then verified ancestry and organization sink access as the deployer.
  No extra Owner role or disabled privacy guard was needed. Bootstrap now includes
  that API; a focused regression reproduced its omission before the fix.
- Attempt 3: corrected prerequisites allowed the existing workflow to finish.
  Failed attempts are retained; success does not erase their diagnostic history.

## Remaining limitations

Alert policy application, notification receipt and the full alert delivery path
still require separate verification. The [operations checklist](operations-checklist-vi.md#4-monitoring-và-thông-báo-email)
continues there; do not rerun deployment or load tests merely to populate evidence.
The original 249/250 cloud load result and later 250/250 diagnostic remain separate.
Client cold-start is still unconfirmed; no new latency/capacity or business-lift
claim is made. Final held-out model quality evaluation remains deferred.
