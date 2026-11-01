# Release reliability implementation plan

Goal: implement the user-approved diagnostics, provenance, deployment/rollback,
and monitoring follow-up without changing runtime policies, weights or labels.

Architecture: keep clean-source build checks; authorize redeployment through a
reviewed immutable release record binding image, manifest, artifact source and
model. Record the clean operator source independently. Use existing OIDC,
Cloud Run, log metrics and native Monitoring; no new service architecture.

Constraints: preserve original 1/250 result and unconfirmed cold classification;
never evaluate the held-out test or retrain. No raw IDs, URLs, exception messages
or tokens in diagnostic output. Live checks require an authorized Google identity.

- [x] Add failing tests for approved older artifacts and changed digest/hash/source;
      implement approved release verification before model deserialization.
- [x] Add failing tests for warmup/schema/transport failures and persistent
      one-attempt diagnostic receipts; implement bounded diagnostic CLI.
- [x] Update OIDC workflow to use approved artifacts; add explicit traffic rollback
      path with receipt and smoke checks, preserving source/hash/privacy guards.
- [x] Define native service-error and bundle-load alerts with an explicit verified
      notification channel; provide read-only monitoring inventory and document
      delivery checks as manual, not inferred from configuration existence.
- [ ] Run focused red/green cycles and full locked tests, review changes, commit,
      push and update PR. Attempt live checks only if authorized access exists;
      otherwise report exact outstanding commands and evidence gaps.

Review focus: approval bypass before pickle loading; malformed 200 responses;
warmup/setup failures leaking data; rollback to unreviewed images or mixed traffic;
missing Monitoring samples/channels being mislabeled as success.


Execution notes:
- Focused tests were observed failing before approval, warmup receipts, rollback,
  and monitoring implementations, then passing.
- Review found and fixed the older loader source mismatch using a verified Git
  source mirror (no serving-loader changes), the v1 dashboard endpoint, and
  incomplete successful-response item validation. Regression tests cover each.
- Ruling: no live cloud operations without a ready Google identity. No identity
  or credential bindings are configured; gcloud selects an empty read-only
  identity directory. Keep live diagnostic/OIDC/rollback/notification results
  pending rather than infer a pass from offline tests.
- Ruling: notification delivery requires an existing verified channel and a
  separately observed test receipt. Do not invent a recipient or manufacture
  production errors to test it.
- Full locked suite: 107 tests passed; this is synthetic/offline verification,
  not the original held-out test or live cloud verification.
