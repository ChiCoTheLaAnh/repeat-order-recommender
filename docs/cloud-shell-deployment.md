# Cloud Shell deployment of the frozen historical release

Continue [draft PR #2](https://github.com/ChiCoTheLaAnh/repeat-order-recommender/pull/2).
This uses its existing Artifact Registry / Cloud Run architecture and deployment
scripts. No model is fitted, no test split is loaded, and no policy is changed.

Authentication checked in the Codex environment on October 9, 2026: no Google
ADC, no SDK accounts in either the default or cached profile, and project access
fails with “You do not currently have an active account selected.” Only project
and region values are bound; those values are not credentials. The available
environment tools expose no Google account connector or secure interactive
Google login. Proxy-secret declarations are not a mechanism for provisioning
Google SDK credentials. Browser Cloud Console sign-in does not authenticate
this environment. The previous instruction to connect an identity through an
unspecified environment credential facility was insufficient.

The supported alternative is **Cloud Shell's own authorization flow**. It has
a user-controlled terminal and Google account authorization. Authorize its SDK
when prompted, then verify the account and project below. No service-account
key, pasted token, or chat authorization code is needed. Official references:
[Cloud Shell authorization](https://cloud.google.com/shell/docs/auth) and
[file upload/download](https://cloud.google.com/shell/docs/uploading-and-downloading-files).
These pages were proxy-blocked from the current Codex runner; Cloud Shell steps
remain unexecuted here. The local transfer/validation steps are verified.

The deployment checkout is deliberately pinned to **b4accd8**, the clean reviewed
runtime commit used by the existing bundle, rather than the later documentation
tip of PR #2. A checkout of another commit is rejected by the deployment scripts.
This manual deployment does not invoke the main-only GitHub release workflow.
A subsequent release from main needs a bundle built for that exact main commit.

Run the numbered blocks in order in **one Cloud Shell terminal**. Stop on any
failed check. Do not use shell tracing or print credentials.

1. Open [Google Cloud Console for this project](https://console.cloud.google.com/?project=project-c0e2b13a-3cea-47e9-aad),
   activate Cloud Shell, and approve its **Authorize Cloud Shell** prompt when
   the SDK needs access. Run the following checks; the account email is identity
   information, not a token. If Cloud Shell has no active account after its
   authorization prompt, run `gcloud auth login` **in that terminal** and complete
   its Google browser flow there, then repeat these checks. Do not send the flow's
   codes or credentials to chat. A permission failure requires access from the
   project/billing administrator; selecting a project does not grant access.

    ```bash
    set +x
    set -euo pipefail
    export GCP_PROJECT_ID='project-c0e2b13a-3cea-47e9-aad'
    export GCP_REGION='us-central1'
    gcloud config set project "$GCP_PROJECT_ID"
    gcloud projects describe "$GCP_PROJECT_ID" \
      --format='table(projectId,projectNumber,lifecycleState)'
    repeat_active_account="$(gcloud auth list --filter='status:ACTIVE' --format='value(account)')"
    test -n "$repeat_active_account"
    printf 'Authorized Cloud Shell account: %s\n' "$repeat_active_account"
    gcloud billing projects describe "$GCP_PROJECT_ID" \
      --format='yaml(projectId,billingEnabled,billingAccountName)'
    test "$(gcloud billing projects describe "$GCP_PROJECT_ID" --format='value(billingEnabled)' | tr '[:upper:]' '[:lower:]')" = 'true'
    ```

2. Clone the actual public repository into a new directory and pin the release
   source. GitHub authentication is not required for this public read. The new
   directory avoids overwriting an existing checkout.

    ```bash
    repeat_repo_dir="$HOME/repeat-order-recommender-release-b4accd8"
    repeat_source_commit='b4accd82988a47798df4460f0c205cb95c2c7b74'
    test ! -e "$repeat_repo_dir"
    git clone --single-branch --branch milestone4-cloud-release \
      https://github.com/ChiCoTheLaAnh/repeat-order-recommender.git "$repeat_repo_dir"
    cd "$repeat_repo_dir"
    git checkout --detach "$repeat_source_commit"
    test "$(git rev-parse HEAD)" = "$repeat_source_commit"
    test -z "$(git status --porcelain)"
    ```

3. Install the pinned deployment tools and locked Python dependencies. Keep
   Cloud Shell's existing SDK credential profile: **do not replace
   `CLOUDSDK_CONFIG`**. Installing another SDK does not authenticate it; recheck
   that it uses the authorized account. These scripts use SDK credentials, so
   a separate `gcloud auth application-default login` is unnecessary.

    ```bash
    python3 -m venv "$HOME/.repeat-order-deploy-tools"
    "$HOME/.repeat-order-deploy-tools/bin/python" -m pip install 'uv==0.12.19'
    export PATH="$HOME/.repeat-order-deploy-tools/bin:$PATH"
    export UV_CACHE_DIR="$repeat_repo_dir/.cache/uv"
    uv sync --locked --python 3.12.14
    bash scripts/install_gcloud.sh
    export PATH="$repeat_repo_dir/.cache/google-cloud-sdk-install/google-cloud-sdk/bin:$PATH"
    gcloud version
    test "$(gcloud auth list --filter='status:ACTIVE' --format='value(account)')" = "$repeat_active_account"
    gcloud projects describe "$GCP_PROJECT_ID" --format='value(projectId)'
    ```

4. Transfer the existing archive. In this Codex workspace the download file is
   `outputs/cloudshell-handoff/repeat-order-release-b4accd8.tar.gz`
   (**6,363,478 bytes**). Save that file locally using the workspace file link
   supplied in chat. In Cloud Shell's terminal **More → Upload → Upload file**,
   upload it to your Cloud Shell home directory (`$HOME`). This is a private
   file transfer into your authenticated session; do not publish the bundle as
   a public GitHub attachment/release. It contains customer lookup data.

   Git ignores the models and all `outputs/`: **cloning alone cannot reproduce
   this release**. The archive already includes the original fitted logistic
   pipeline/preprocessing, candidate features, lookup, display/fallback data,
   frozen policies, source snapshot and manifest. No raw workbook, training
   outputs or test labels need to be transferred. Do not run `milestone2` or any
   training command as a workaround for a missing archive. If this archive and
   the original frozen artifacts are lost, stop; fitting a replacement would
   not preserve the selected model.

    ```bash
    test -f "$HOME/repeat-order-release-b4accd8.tar.gz"
    mkdir -p outputs/cloudshell-input
    cp "$HOME/repeat-order-release-b4accd8.tar.gz" outputs/cloudshell-input/
    printf '%s  %s\n' \
      'cd78d77cdbbec1fc6a164efddcdca2424e680865228f9d4a3da4c4eb6f3492ef' \
      'outputs/cloudshell-input/repeat-order-release-b4accd8.tar.gz' | sha256sum --check
    uv run --locked python -m scripts.cloud_deploy extract \
      --archive outputs/cloudshell-input/repeat-order-release-b4accd8.tar.gz \
      --sha256 cd78d77cdbbec1fc6a164efddcdca2424e680865228f9d4a3da4c4eb6f3492ef \
      --output outputs/cloudshell-release
    export REPEAT_BUNDLE="$repeat_repo_dir/outputs/cloudshell-release/bundle"
    ```

5. Independently verify manifest/model hashes, source commit and the complete
   bundle. `extract` checks the trusted archive checksum and safe archive paths,
   then verifies the clean commit, frozen model, every payload/source/config
   hash, dependency/schema versions and temporal windows before scoring.
   The following adds explicit expected manifest/model checks. The model's last
   training labels end February 1, 2011; serving is May 1, 2011, exclusive.

    ```bash
    uv run --locked python - <<'PY'
    import json, os
    from pathlib import Path
    from scripts.audit_raw import sha256
    from scripts.cloud_deploy import clean_commit, validate_deployable_manifest
    from scripts.release_bundle import ReleaseBundle
    p=Path(os.environ['REPEAT_BUNDLE'])
    assert sha256(p/'manifest.json')=='fb62b8e85893bb2bb58526f8680aa78e94e24960eabee54cad2bf41c1955c742'
    assert sha256(p/'model.joblib')=='dd79f226da8964d6607185d6905829404ecd126dfaa65848d3bdc555f576f17d'
    m=json.loads((p/'manifest.json').read_text())
    assert clean_commit()=='b4accd82988a47798df4460f0c205cb95c2c7b74'
    validate_deployable_manifest(m,clean_commit())
    bundle=ReleaseBundle.load(p)
    assert m['model_name']=='logistic' and m['data_as_of']=='2011-05-01T00:00:00'
    assert m['customers']==2443 and m['candidate_rows']==488600
    print(json.dumps({'release_id':m['release_id'],'source_commit':m['provenance']['code_commit'],
                      'model':'logistic','data_as_of':m['data_as_of'],'verified':True},indent=2))
    PY
    ```

6. Run the reviewed one-time admin setup. It enables the existing architecture's
   APIs and creates its registry, service accounts, WIF bindings and monitoring/
   privacy exclusions. Your identity needs the permissions described in
   [cloud-operations.md](cloud-operations.md); denied IAM/billing operations are
   external prerequisites, not reasons to weaken privacy checks. Ancestor
   aggregated sinks, if present, require inspection/exclusions by their admin.

    ```bash
    mkdir -p outputs/milestone4
    uv run --locked python -m scripts.bootstrap_cloud --apply \
      --project "$GCP_PROJECT_ID" --region "$GCP_REGION" \
      > outputs/milestone4/cloudshell-bootstrap.json
    uv run --locked python - <<'PY'
    import json, os
    from scripts.cloud_deploy import privacy_preflight
    print(json.dumps(privacy_preflight(os.environ['GCP_PROJECT_ID']),indent=2))
    PY
    ```

7. Build the existing Docker service with the immutable bundle embedded.
   There are no startup downloads or training. This uses Cloud Shell's Docker
   daemon and normal TLS, not Codex-specific proxy build arguments.

    ```bash
    export DOCKER_BUILDKIT=1
    repeat_release_id='release_v1_35f8ccbb4cd8b9ada5b40696103a25e8f3969f3a54081e310a84354fd81221f6'
    repeat_context="outputs/cloud-context/$repeat_release_id"
    uv run --locked python -m scripts.cloud_deploy context \
      --bundle "$REPEAT_BUNDLE" --output "$repeat_context"
    docker build -t repeat-order-recommender:milestone3 .
    repeat_image_tag="us-central1-docker.pkg.dev/$GCP_PROJECT_ID/repeat-order-demo/service:$repeat_release_id"
    docker build -f "$repeat_context/Dockerfile" \
      --build-arg SERVICE_IMAGE=repeat-order-recommender:milestone3 \
      --build-arg SOURCE_COMMIT="$repeat_source_commit" \
      --build-arg RELEASE_ID="$repeat_release_id" \
      --build-arg BUNDLE_MANIFEST_SHA256=fb62b8e85893bb2bb58526f8680aa78e94e24960eabee54cad2bf41c1955c742 \
      -t "$repeat_image_tag" "$repeat_context"
    ```

8. Publish and resolve the **registry digest**, then deploy using the reviewed
   helper. A local Docker image ID is not a registry digest. The helper inspects
   the image's OCI labels and embedded bundle without starting it, checks log
   privacy and prior service access, and records previous revision/traffic for
   rollback. Configuration stays request billing, 1 vCPU/1 GiB, 0–1 instances,
   concurrency 5 and startup/liveness probes; IAM authentication stays enabled.

    ```bash
    gcloud auth configure-docker us-central1-docker.pkg.dev --quiet
    docker push "$repeat_image_tag"
    repeat_digest="$(gcloud artifacts docker images describe "$repeat_image_tag" \
      --project "$GCP_PROJECT_ID" --format='value(image_summary.digest)')"
    printf '%s\n' "$repeat_digest" | grep -Eq '^sha256:[0-9a-f]{64}$'
    repeat_image="${repeat_image_tag%:*}@$repeat_digest"
    uv run --locked python -m scripts.cloud_deploy deploy \
      --bundle "$REPEAT_BUNDLE" --image "$repeat_image" \
      --project "$GCP_PROJECT_ID" --region "$GCP_REGION" \
      --output outputs/milestone4/cloudshell-deployment \
      > outputs/milestone4/cloudshell-deployment-summary.json
    repeat_cloud_url="$(uv run --locked python -c \
      "import json; print(json.load(open('outputs/milestone4/cloudshell-deployment/deployment.json'))['url'])")"
    printf 'Historical demo URL (IAM authenticated): %s\n' "$repeat_cloud_url"
    ```

9. Authorize audience-specific invocation **without a key**. The same existing
   deployer account already has Run invoker. Grant your active Cloud Shell
   identity token-creator access on that account only, then capture its
   short-lived ID token in memory. The grant requires service-account IAM admin
   permission. An administrator may grant it instead if your identity lacks
   that permission. Token commands below are captured, never printed.

    ```bash
    repeat_deployer="repeat-demo-deployer@$GCP_PROJECT_ID.iam.gserviceaccount.com"
    case "$repeat_active_account" in
      *.gserviceaccount.com) repeat_invoker_member="serviceAccount:$repeat_active_account" ;;
      *) repeat_invoker_member="user:$repeat_active_account" ;;
    esac
    gcloud iam service-accounts add-iam-policy-binding "$repeat_deployer" \
      --project "$GCP_PROJECT_ID" --member "$repeat_invoker_member" \
      --role roles/iam.serviceAccountTokenCreator --quiet >/dev/null
    repeat_id_token="$(gcloud auth print-identity-token \
      --impersonate-service-account "$repeat_deployer" \
      --audiences "$repeat_cloud_url" --include-email)"
    test -n "$repeat_id_token"
    export CLOUD_RUN_ID_TOKEN="$repeat_id_token"
    unset repeat_id_token
    ```

10. Run actual remote smoke and bounded warm measurements. This covers health,
    readiness, release metadata, known-customer offline/API equivalence,
    unknown fallback and K validation, then 1/5-client levels (20 warmup,
    at most 250 measured requests or 20 seconds each). It records latency,
    throughput, errors, process RSS and native memory/startup distributions.
    The first request alone is explicitly **not** a confirmed cold-start sample.
    Save client hardware separately; Cloud Run does not disclose its host CPU
    model. Report Cloud Shell client measurements as cloud HTTP, not local
    Docker throughput or real customer behavior. Empty native metric samples
    remain unavailable; export can lag minutes.

    ```bash
    uv run --locked python -c \
      "import json; from scripts.model_selection import hardware; print(json.dumps(hardware(),indent=2))" \
      > outputs/milestone4/cloudshell-client-hardware.json
    uv run --locked python -m scripts.cloud_verify \
      --url "$repeat_cloud_url" --bundle "$REPEAT_BUNDLE" \
      --project "$GCP_PROJECT_ID" --region "$GCP_REGION" \
      --output outputs/milestone4/cloudshell-warm
    unset CLOUD_RUN_ID_TOKEN
    ```

11. Measure cold start separately after stopping all invocations, synthetic
    clients and other external probes. Cloud Run scale-to-zero can take several
    minutes; a fixed sleep is not proof. Inspect its native instance-count
    metric in Monitoring. Only if fresh explicit samples show **zero total
    instances, including idle**, save the observation below. Missing samples
    are not zero. The code fails without sufficiently fresh active+idle zero
    samples; wait and repeat this block rather than inventing evidence. Sampling
    lag still limits the observation's certainty, and the existing verifier also
    requires a newly started process (uptime ≤10 seconds). If explicit zero
    samples cannot be obtained, retain `confirmed_client_cold_start_ms: null`
    and report the unconfirmed first request/native startup distribution honestly.

    ```bash
    if uv run --locked python - <<'PY'
    import json, os
    from datetime import datetime, timedelta, timezone
    from pathlib import Path
    import httpx
    from scripts.cloud_deploy import config, gcloud, privacy_preflight
    from scripts.cloud_verify import serving_revision, tls_context
    project,region=os.environ['GCP_PROJECT_ID'],os.environ['GCP_REGION']
    privacy_preflight(project)
    state=gcloud('run','services','describe',config()['service'],
                 '--project',project,'--region',region,json_output=True)
    revision=serving_revision(state)
    now=datetime.now(timezone.utc)
    metric='run.googleapis.com/container/instance_count'
    query_filter=(f'metric.type="{metric}" AND resource.type="cloud_run_revision" '
        f'AND resource.labels.service_name="{config()["service"]}" '
        f'AND resource.labels.location="{region}" AND resource.labels.revision_name="{revision}"')
    access=gcloud('auth','print-access-token')
    with httpx.Client(headers={'Authorization':'Bearer '+access},verify=tls_context(),timeout=30) as client:
        response=client.get(f'https://monitoring.googleapis.com/v3/projects/{project}/timeSeries',
            params={'filter':query_filter,'interval.startTime':(now-timedelta(minutes=10)).isoformat(),
                    'interval.endTime':now.isoformat(),'pageSize':1000})
        response.raise_for_status()
        document=response.json()
    assert not document.get('nextPageToken'),'Incomplete instance-count query; do not assume zero'
    samples=[]
    for series in document.get('timeSeries',[]):
        points=series.get('points',[])
        assert points,'Missing observations are not zero'
        point=max(points,key=lambda p:p['interval']['endTime'])
        sample_time=datetime.fromisoformat(point['interval']['endTime'].replace('Z','+00:00'))
        assert 0 <= (now-sample_time).total_seconds() <= 120,'Stale instance-count sample'
        value=point['value']
        count=int(value['int64Value']) if 'int64Value' in value else float(value['doubleValue'])
        samples.append({'state':series['metric']['labels'].get('state'),
                        'count':count,'sample_time':sample_time.isoformat()})
    assert {'active','idle'}.issubset({s['state'] for s in samples}), 'Require explicit active and idle observations'
    assert all(s['count']==0 for s in samples),'Instances remain; wait for scale-to-zero'
    evidence={'project':project,'region':region,'service':config()['service'],'revision':revision,
        'observed_at':now.isoformat(),'active_instances':0,'total_instances':0,'samples':samples,
        'source':'Native Monitoring instance_count explicit recent zero samples; sampling may lag'}
    Path('outputs/milestone4/cloudshell-zero-instances.json').write_text(json.dumps(evidence,indent=2)+'\n')
    print('Zero-instance evidence saved; make the isolated request immediately.')
    PY
    then
      repeat_id_token="$(gcloud auth print-identity-token \
        --impersonate-service-account "$repeat_deployer" \
        --audiences "$repeat_cloud_url" --include-email)"
      test -n "$repeat_id_token"
      export CLOUD_RUN_ID_TOKEN="$repeat_id_token"
      unset repeat_id_token
      uv run --locked python -m scripts.cloud_verify --smoke-only \
        --url "$repeat_cloud_url" --bundle "$REPEAT_BUNDLE" \
        --project "$GCP_PROJECT_ID" --region "$GCP_REGION" \
        --cold-zero-evidence outputs/milestone4/cloudshell-zero-instances.json \
        --output outputs/milestone4/cloudshell-cold
      unset CLOUD_RUN_ID_TOKEN
    else
      printf 'No verified zero-instance evidence. Cold latency remains unconfirmed; retry step 11 later.\n'
    fi
    ```

12. Export the evidence for review. This archive contains deployment/configuration,
    warm/cold measurements when available, timestamps, checks and client hardware;
    it excludes the bundle, raw data, customer-ID lookup and credentials. Download
    it from Cloud Shell and attach it to this chat to finish recording actual
    cloud results. Do not attach credential files or shell/environment dumps.
    Use the receipt's previous revision/image and the existing rollback procedure
    if smoke fails. No cloud results are claimed until these steps run successfully.

    ```bash
    uv run --locked python - <<'PY'
    from pathlib import Path
    import tarfile
    root=Path('outputs/milestone4')
    names=['cloudshell-bootstrap.json','cloudshell-client-hardware.json',
           'cloudshell-deployment/deployment.json','cloudshell-deployment/cloud_run_service.json',
           'cloudshell-warm/cloud_verification.json','cloudshell-cold/cloud_verification.json',
           'cloudshell-zero-instances.json']
    assert (root/'cloudshell-warm/cloud_verification.json').is_file(), 'Warm verification has not completed'
    with tarfile.open(Path.home()/'repeat-order-cloud-verification.tar.gz','w:gz') as archive:
        for name in names:
            if (root/name).is_file(): archive.add(root/name,arcname=name)
    PY
    cloudshell download "$HOME/repeat-order-cloud-verification.tar.gz"
    ```

The original held-out test remains unopened throughout: the serving archive
contains past-only features/catalog plus the already selected fitted model,
and no test labels or test quality measurements. IAM authentication, privacy
preflight and the frozen release checks must stay enabled.
