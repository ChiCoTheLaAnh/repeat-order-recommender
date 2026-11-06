# First OIDC release attempt: immutable subject mismatch

Release run [42149437150](https://github.com/ChiCoTheLaAnh/repeat-order-recommender/actions/runs/42149437150)
on operator commit `2b21696656722163e9f9b2c41f60a031e9ea2251` failed at the first
`google-github-actions/auth` step. Dependency installation and offline tests
passed. Setup-gcloud, image validation/pull, deployment, smoke and rollback were
all skipped. This workflow did not change the Cloud Run service.

The check-run annotation says `unauthorized_client`, with
`The given credential is rejected by the attribute condition.` The Node20
metadata annotation is a separate warning. The logs download endpoint was not
accessible to this integration; the full auth error and skipped-step statuses
were retrieved through the check/job APIs instead. No OIDC token was retrieved.

The repository OIDC customization API returned:

```json
{
  "use_default": true,
  "use_immutable_subject": true,
  "sub_claim_prefix": "repo:ChiCoTheLaAnh@185126752/repeat-order-recommender@1406464839"
}
```

Repository metadata independently confirms repository ID `1406464839`. The
previous provider condition expected `repo:ChiCoTheLaAnh/repeat-order-recommender:environment:historical-demo`,
which does not match the immutable prefix above. The earlier Cloud Shell
preflight established that provider configuration matched the legacy recipe;
it did not prove that actual GitHub federation worked.

The corrected exact subject is:

```text
repo:ChiCoTheLaAnh@185126752/repeat-order-recommender@1406464839:environment:historical-demo
```

The correction preserves the repository-ID check, `refs/heads/main` check and
exact environment subject. Bootstrap and the standalone preflight are updated;
no broad prefix match, legacy OR branch, new IAM role or service-account key is
introduced. Frozen model/retrieval/cleaning/feature policies are unchanged.

Apply the one-provider condition update through the user's authorized Cloud
Shell using the [operations checklist](operations-checklist-vi.md#sửa-trust-format-sau-lần-oidc-bị-từ-chối),
then confirm with its read-only preflight before one new release attempt. The
provider update and successful OIDC/rollback still need live evidence. Do not
mark authentication verified from these code changes or offline tests alone.
