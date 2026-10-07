# Milestone 4 implementation contract

Use the existing logistic artifact and unchanged cleaning, recommendation,
retrieval, features and labels. The original test is never opened/evaluated.
May 1, 2011 remains the serving boundary because every training label ends by
February 1 and validation selection labels end on May 1 (exclusive).

1. Commit the previously uncommitted implementation, then add the operational
   code and commit it before rebuilding a deployable release. Require a clean
   worktree and verify the runtime source snapshot against the exact commit.
2. Verify actual training query windows from train-filtered snapshot inputs.
   Publish the existing model in an immutable archive and bake it into the
   deployment image; there is no runtime artifact download or training.
3. Use Cloud Run request billing, 1 CPU/1 GiB, 0–1 instances, concurrency 5,
   `/ready` startup gating and `/health` liveness. Publish image digests to
   Artifact Registry and annotate commit, release, model and serving date.
4. Before traffic, exclude Cloud Run automatic request logs from every editable
   project sink: URL paths contain raw customer IDs. Keep sanitized JSON logs,
   log-based operational counters/distributions and native Cloud Run metrics.
5. PR CI installs the lock, runs all offline tests and builds Docker without
   cloud credentials. Manual release CI uses GitHub OIDC restricted to the
   repository, main branch and historical-demo environment, checks the bundle
   embedded in an immutable Artifact Registry image, and deploys the digest
   with smoke checks.
6. Cloud probes record real remote results only, bound each client level to
   20 seconds/250 requests, and separate confirmed cold client samples from
   first-request and native container-startup measurements. Missing metrics or
   access are explicit, never replaced with local throughput.
7. Document rollback by revision/image digest, manual serving refresh, matured
   retraining windows and later temporal challenger validation. No automatic
   retraining, drift alerts, event ingestion or additional infrastructure.

The user supplied project `project-c0e2b13a-3cea-47e9-aad`, region `us-central1`.
Google credentials currently injected are empty JSON objects. The initial
network probe was denied; the latest reaches Google with 401 CREDENTIALS_MISSING.
Complete preparation while an authorized identity is supplied.
