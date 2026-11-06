# Hoàn tất deploy, rollback và monitoring

## Trạng thái đã kiểm tra

- PR #4 đã review và merge vào `main`, merge commit
  `2b21696656722163e9f9b2c41f60a031e9ea2251`; CI của PR đã pass.
- Diagnostic duy nhất đã hoàn tất 250/250 request; giữ nguyên lỗi 1/250 của lần cũ.
- Owner đã tạo `historical-demo`; API xác nhận custom deployment branch policy
  chỉ cho branch `main`. Bốn variables đã được owner cấu hình; integration không
  có quyền đọc độc lập toàn bộ values hoặc sửa environment (HTTP 403).
- Release run `42149437150` dừng ở bước Google authentication: WIF từ chối
  attribute condition. Các bước deploy/smoke/rollback đều bị skip, chưa đổi service.
- GitHub OIDC settings xác nhận immutable subject có prefix
  `repo:ChiCoTheLaAnh@185126752/repeat-order-recommender@1406464839`; provider
  trước đó kiểm tra legacy subject không có IDs. Đây là lỗi trust-format,
  không phải warning Node20 và không phải model failure.
- Codex chưa có Google identity. Việc sửa provider vẫn cần Cloud Shell của m;
  không có deploy/rollback hoặc alert-delivery pass mới nào được suy ra từ CI.

Không tạo key, không dán token vào chat, không cấp Owner để vượt lỗi. Không chạy
lại load test, build bundle hoặc retrain model. Giữ nguyên checkout/bundle gốc
`$HOME/repeat-order-recommender-release-b4accd8`.

## Sửa trust-format sau lần OIDC bị từ chối

Đã đọc thông báo lỗi từ GitHub check annotation và cấu hình OIDC repository,
không lấy hoặc in token. Google condition cần khớp immutable subject mà GitHub
đang phát hành. Trong Cloud Shell đã xác thực, cập nhật **chỉ** attribute condition:

```bash
gcloud iam workload-identity-pools providers update-oidc github \
  --workload-identity-pool=github-repeat-demo \
  --location=global \
  --project=project-c0e2b13a-3cea-47e9-aad \
  --attribute-condition="assertion.repository_id == '1406464839' && assertion.ref == 'refs/heads/main' && assertion.sub == 'repo:ChiCoTheLaAnh@185126752/repeat-order-recommender@1406464839:environment:historical-demo'"
```

Giữ nguyên ba ràng buộc repository ID, main ref và exact environment subject;
không bỏ condition, không thêm legacy OR fallback và không đổi mapping/IAM roles.
Chạy preflight chỉ đọc ở bước 1 để xác nhận giá trị đã lưu. Sau khi preflight đạt,
chạy **một** workflow mới với cùng approved inputs và rehearsal bật. Lưu cả run
bị từ chối, không thay thế nó bằng run mới. Nếu gặp lỗi khác thì dừng để phân tích.

## 1. Lấy cấu hình thật từ Cloud Shell — chỉ đọc

Mở Cloud Shell của project `project-c0e2b13a-3cea-47e9-aad`. Khối này cập nhật
**operator checkout riêng**, kiểm tra WIF và các binding hiện có, rồi xuất bốn
GitHub variables. Nó không sửa Google IAM hoặc gửi request recommendation.
Nếu operator checkout có thay đổi tracked, dừng để review; không reset/xóa.

```bash
(
  set +x
  set -euo pipefail
  cd "$HOME/repeat-order-operations"
  test -z "$(git status --porcelain)"
  git fetch origin main
  git switch --detach FETCH_HEAD
  export PATH="$HOME/.repeat-order-deploy-tools/bin:$HOME/repeat-order-recommender-release-b4accd8/.cache/google-cloud-sdk-install/google-cloud-sdk/bin:$PATH"
  export UV_CACHE_DIR="$PWD/.cache/uv"
  uv sync --locked
  uv run --locked python - <<'PY'
import json
from pathlib import Path
from scripts.cloud_deploy import config, gcloud, privacy_preflight
from scripts.cloud_verify import failure_category
import subprocess

report={'status':'failed','actor':'Cloud Shell active identity; not GitHub OIDC'}
phase='identity'
try:
    c=config();project=c['project']
    if not gcloud('auth','list','--filter=status:ACTIVE','--format=value(account)'):
        raise ValueError('No active identity')
    phase='project'
    p=gcloud('projects','describe',project,json_output=True)
    if p['projectId']!=project or p.get('lifecycleState')!='ACTIVE':
        raise ValueError('Project does not match active demo')
    number=str(p['projectNumber'])
    phase='provider'
    provider=gcloud('iam','workload-identity-pools','providers','describe',c['wif_provider'],
        '--workload-identity-pool',c['wif_pool'],'--location=global','--project',project,json_output=True)
    expected_name=f'projects/{number}/locations/global/workloadIdentityPools/{c["wif_pool"]}/providers/{c["wif_provider"]}'
    # Standalone so an older clean operator checkout can run this read-only check.
    expected_condition=("assertion.repository_id == '1406464839' && assertion.ref == 'refs/heads/main' && "
        "assertion.sub == 'repo:ChiCoTheLaAnh@185126752/repeat-order-recommender@1406464839:environment:historical-demo'")
    if (provider['name']!=expected_name or provider.get('disabled')
        or provider.get('attributeCondition')!=expected_condition
        or provider.get('oidc',{}).get('issuerUri')!='https://token.actions.githubusercontent.com'
        or provider.get('attributeMapping',{}).get('google.subject')!='assertion.sub'
        or provider.get('attributeMapping',{}).get('attribute.repository_id')!='assertion.repository_id'):
        raise ValueError('WIF trust differs from reviewed policy')
    phase='bindings'
    deployer=c['deploy_account']+'@'+project+'.iam.gserviceaccount.com'
    runtime=c['runtime_account']+'@'+project+'.iam.gserviceaccount.com'
    member='serviceAccount:'+deployer
    principal=f'principalSet://iam.googleapis.com/projects/{number}/locations/global/workloadIdentityPools/{c["wif_pool"]}/attribute.repository_id/1406464839'
    def has(policy,role,identity):
        return any(b['role']==role and identity in b.get('members',[]) and not b.get('condition')
                   for b in policy.get('bindings',[]))
    deploy_policy=gcloud('iam','service-accounts','get-iam-policy',deployer,'--project',project,json_output=True)
    runtime_policy=gcloud('iam','service-accounts','get-iam-policy',runtime,'--project',project,json_output=True)
    project_policy=gcloud('projects','get-iam-policy',project,json_output=True)
    registry_policy=gcloud('artifacts','repositories','get-iam-policy',c['repository'],
                           '--location',c['region'],'--project',project,json_output=True)
    checks={'wif_binding':has(deploy_policy,'roles/iam.workloadIdentityUser',principal),
        'runtime_act_as':has(runtime_policy,'roles/iam.serviceAccountUser',member),
        'registry_role':any(has(registry_policy,r,member) for r in
                            ['roles/artifactregistry.reader','roles/artifactregistry.writer']),
        'run_developer':has(project_policy,'roles/run.developer',member),
        'run_invoker':has(project_policy,'roles/run.invoker',member)}
    report['expected_explicit_bindings']=checks
    # These are the reviewed bootstrap bindings, not a general IAM policy evaluator.
    # An inherited/custom/conditional equivalent needs manual review, not more roles.
    if not all(checks.values()):raise ValueError('Expected explicit binding missing; review before changing IAM')
    phase='privacy'
    report['privacy_for_active_identity']=privacy_preflight(project)
    approval=json.loads(Path('config/approved_cloud_release_v1.json').read_text())
    report.update({'status':'ready_for_github_configuration','provider_name':provider['name'],
        'github_variables':{'GCP_PROJECT_ID':project,'GCP_REGION':c['region'],
            'GCP_WIF_PROVIDER':provider['name'],'GCP_DEPLOY_SERVICE_ACCOUNT':deployer},
        'workflow_inputs':{'image_digest':approval['image_digest'],
            'bundle_manifest_sha256':approval['manifest_sha256'],'rehearse_rollback':True},
        'deployer_privacy_access':'Must still be verified by actual OIDC workflow',
        'oidc_execution_verified':False})
except Exception as error:
    report.update({'phase':phase,'failure_category':'setup:GoogleCommandFailed'
        if isinstance(error,subprocess.CalledProcessError) else failure_category(error)})
output=Path('outputs/milestone4/oidc-preflight.json');output.parent.mkdir(parents=True,exist_ok=True)
output.write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps(report,indent=2))
raise SystemExit(0 if report['status']=='ready_for_github_configuration' else 1)
PY
)
```

Nếu không ra `ready_for_github_configuration`, dừng và gửi JSON; không tự thêm
roles. Binding check chỉ kiểm tra các grant rõ ràng của bootstrap, không chứng
minh mọi quyền hiệu lực hoặc điều kiện IAM. Nếu đạt, dùng `github_variables`
và `workflow_inputs` ở output cho hai bước tiếp theo. Provider được lấy bằng
`providers describe`, không suy đoán từ project ID hay browser login.

## 2. GitHub UI: tạo environment và variables

1. Mở [Repository Settings → Environments](https://github.com/ChiCoTheLaAnh/repeat-order-recommender/settings/environments).
2. Chọn **New environment**, nhập chính xác `historical-demo`.
3. Trong **Deployment branches and tags**, chọn **Selected branches and tags**,
   thêm rule loại **Branch**, tên chính xác `main`; không dùng wildcard hoặc
   cho phép mọi branch. Nếu GitHub plan không hỗ trợ, dừng và ghi rõ giới hạn.
4. Trong **Environment variables** (không phải Secrets), thêm bốn cặp key/value
   từ `github_variables` của bước 1. Đây là identifiers, không phải credentials.
5. Không thêm required reviewer bắt chính m tự approve. Nếu dùng reviewer khác,
   người đó phải thực sự có thể approve. Không cấu hình chờ vô hạn cho tài khoản
   chỉ có một người.

Owner đã hoàn thành bước tạo environment và main-only rule. Chỉ review lại
values nếu cần; không tạo environment khác. Integration vẫn không có quyền ghi
environment (HTTP 403), nên mọi thay đổi UI cần account admin. Không cần thay token hoặc đưa key cho Codex.

## 3. Chạy deploy và rollback đúng một lần

1. Mở [Actions → Deploy historical demo](https://github.com/ChiCoTheLaAnh/repeat-order-recommender/actions/workflows/release.yml).
2. Chọn **Run workflow**, branch **main**. Điền `image_digest` và
   `bundle_manifest_sha256` từ bước 1, bật **rehearse_rollback**.
3. Chạy một lần. Nếu lỗi, giữ run ID và artifact; dừng để phân tích, không rerun
   hoặc cấp thêm quyền tùy đoán. Job chỉ dùng short-lived OIDC, không dùng key.
4. Tải artifact `historical-demo-deployment-<operator commit>` trong run. Nó
   chứa deployment receipt, service spec, smoke sau deploy, rollback receipt và
   smoke sau rollback; không chứa model hoặc customer lookup.

Chỉ đánh dấu **đã xác minh** khi đủ:

- CI và OIDC auth pass; approved image/manifest/source/model khớp.
- Smoke sau deploy pass: health, readiness, metadata, known-customer offline/API
  equivalence, unknown fallback, K validation.
- `rollback.json.changed_traffic` là **true**, revision được phục vụ trước rollback
  khác revision khôi phục, revision khôi phục đúng revision trước deployment.
- `service_state.status.traffic` trả 100% về revision cũ, không split traffic;
  smoke report sau rollback có `revision` trùng revision được khôi phục và pass.
- Model và release IDs giống nhau qua các bước; operator commit được ghi riêng.
  Dịch vụ giữ IAM authentication, min 0/max 1, 1 vCPU/1 GiB và concurrency 5.

`smoke_verified: false` trong rollback control-plane receipt không tự biến thành
true; dùng **smoke report riêng** làm bằng chứng. Nếu `changed_traffic: false`, đó
là no-op, chưa xác minh chuyển traffic. Workflow kết thúc bằng việc giữ revision
cũ phục vụ. Nếu smoke fail trước bước rollback, xem quy trình rollback thủ công
trong [release runbook](release-reliability.md); không có automatic rollback.
Không xóa image/revision gốc hoặc nới public IAM để sửa smoke 403.

## 4. Monitoring và thông báo email

1. Trong [Google Cloud Monitoring](https://console.cloud.google.com/monitoring?project=project-c0e2b13a-3cea-47e9-aad),
   mở **Alerting → Edit notification channels**. Chọn hoặc tạo channel loại
   **Email** bằng email Google account hiện tại của m. Xác minh trực tiếp trong
   Console/inbox; không đưa địa chỉ hoặc nội dung thư vào repo/chat.
2. Lấy resource name `projects/.../notificationChannels/<number>` của channel
   verified/enabled. Resource ID là identifier, không phải token. Chạy ba lệnh
   `prepare`, `apply`, `inspect` trong [release runbook](release-reliability.md#monitoring-configuration-and-delivery)
   từ operator checkout, dùng đúng channel đó. Hai policies là native 5xx và
   bundle-load failure, scoped vào service/region hiện có.
3. Xem JSON `alerts-applied.json` và `monitoring-inventory.json`. Tất cả filters,
   label extractors, alert conditions/channels/enabled phải khớp; exit 0 của
   inventory không phải kết luận mọi field đạt. Nếu bị 403, dùng account có
   Monitoring editor phù hợp; giữ deployer read-only với monitoring.
4. Mở dashboard **Historical recommendation demo — operational metrics**.
   Xem native samples trong cửa sổ đã có bằng chứng
   `2026-10-27 02:45–03:21 UTC` và các samples mới nếu có. Không gọi dữ liệu lịch
   sử là số đo của run OIDC. Đối chiếu sanitized logs theo release/model; không
   chụp raw request URLs/customer IDs. Thiếu samples nghĩa là chưa quan sát được.
5. Dùng chức năng test notification nếu Console hỗ trợ, kiểm tra inbox và ghi
   thời gian nhận. Không cố gây 5xx hoặc làm hỏng bundle thật. Nếu không có test
   facility, ghi **delivery chưa xác minh**, không báo pass từ policy creation.

Báo cáo riêng ba mức: **policies configured**, **test notification received**,
**full condition-to-notification path verified**. Hai mức đầu không tự chứng
minh mức thứ ba. Chỉ giữ screenshot đã che email/IDs; không lưu thư thô hoặc PII.

## 5. Gói bằng chứng và dừng

Gửi workflow artifact, preflight JSON và hai JSON monitoring để review. Lưu raw
receipts trong ignored `outputs/`; version-control chỉ summary/hash đã khử dữ
liệu nhạy cảm. Cập nhật [walkthrough phỏng vấn](interview-demo-vi.md) sau khi đọc
bằng chứng thật. Không tick các mục đang pending chỉ vì cấu hình đã tồn tại.
