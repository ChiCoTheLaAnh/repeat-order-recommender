# Chốt demo phỏng vấn và kiểm tra chi phí/quyền

## Bộ demo: bốn bằng chứng có sẵn

| Thứ tự | Mở bằng chứng này | Điều cần nói |
| --- | --- | --- |
| 1 | [README: validation và model selection](../README.md#verified-validation-results-and-recommendation) và [release comparison](../README.md#milestone-3-historical-release-and-local-inference) | Logistic NDCG@10 khoảng 0,3780 vs heuristic 0,3351 trên primary validation queries; chọn logistic vì quality gap nhỏ với XGB, artifact nhỏ hơn khoảng 66 lần |
| 2 | [GitHub workflow thành công, attempt 3](https://github.com/ChiCoTheLaAnh/repeat-order-recommender/actions/runs/42149437150/attempts/3) | OIDC và immutable image verification pass; model/data không được tải mới hoặc train lúc startup |
| 3 | [Receipt summary deploy/rollback](oidc-release-verification.md#actual-traffic-change-and-smoke-results) | Revision 00001 → 00002 → 00001, 100% traffic restored, smoke cả hai revision pass; cùng frozen model/image |
| 4 | [Monitoring verification](monitoring-verification.md) và [Cloud Monitoring dashboards](https://console.cloud.google.com/monitoring/dashboards?project=project-c0e2b13a-3cea-47e9-aad) | Hai alert cấu hình đúng, bốn filters/extractors khớp, dashboard tồn tại; email receipt và full alert path chưa xác minh |

Có thể trình bày bằng report nếu không mở được Console. Khi mở dashboard, dùng
tên **Historical recommendation demo — operational metrics**, ghi rõ time window
và chỉ nói về samples thực sự hiển thị. Không thay dashboard bằng ảnh mẫu hoặc
gọi native samples cũ là số đo hiện tại. Chưa có sanitized dashboard screenshot
được lưu trong repo; API inventory/report là bằng chứng hiện có.

Dùng [walkthrough 3 phút](interview-demo-vi.md). Tập một lượt
ngoại tuyến với đồng hồ, tự kiểm tra có thể giải thích leakage, lựa chọn logistic,
retrieval-vs-ranking failures và ba mức monitoring. Việc tập nói phải do m thực
hiện; tài liệu hoàn chỉnh không đồng nghĩa đã tập xong.

Giữ các giới hạn khi pitch: khoảng 12,8% là relative validation NDCG improvement,
không phải doanh thu; metrics chính chỉ bao phủ nhóm có known-catalog targets;
discovery yếu; historical precomputed serving; chưa final held-out evaluation;
cold-start chưa xác nhận; một lỗi 1/250 cũ chưa rõ nguyên nhân. Không cần thêm
request hoặc chạy lại benchmark để mở các bằng chứng trên.

## Kiểm tra hiện tại: không thay cấu hình, không tạo tài nguyên

Receipt OIDC gần nhất chứng minh cấu hình tại lần deploy là min 0/max 1,
1 vCPU/1 GiB và IAM-authenticated. **Trạng thái Google hiện tại và actual billing
chưa được đọc trong đợt chốt này** vì Codex không có Google identity. Không suy
ra chi phí bằng 0 hoặc quyền hiện tại từ cấu hình/receipt cũ.

### A. Cloud Run — kiểm tra trên Console bằng account của m

1. Mở [service hiện có](https://console.cloud.google.com/run/detail/us-central1/repeat-order-historical-demo/metrics?project=project-c0e2b13a-3cea-47e9-aad).
2. Xem Scaling và xác nhận service min instances **0**, max **1**; không bấm sửa
   hoặc deploy. Trong Revisions, traffic phải ở một revision rõ ràng; đối chiếu
   revision **đang phục vụ**, không chỉ revision mới tạo gần nhất.
3. Kiểm tra scaling/resource của revision đang phục vụ: min 0/max 1,
   1 vCPU/1 GiB, concurrency 5. Nếu khác thì ghi nhận để review, không tự sửa.
4. Trong Security/Permissions xác nhận invoker IAM check đang bật, authentication
   required và không có public invoker grant `allUsers`/`allAuthenticatedUsers`.
   Nếu UI không cho kiểm tra đủ hoặc không đọc được inherited access, ghi unknown;
   không coi UI thiếu trường là đã private. Không cần gửi request lên dịch vụ.
5. Lưu thời gian kiểm tra, revision và kết quả; screenshot phải che email/member
   identities. Kiểm tra Console này là manual review, không phải phép chứng minh
   mọi effective IAM permission hoặc mọi trạng thái trong tương lai.

### B. Billing — chỉ xem số liệu đã có

1. Mở [Billing](https://console.cloud.google.com/billing?project=project-c0e2b13a-3cea-47e9-aad)
   bằng account có quyền đọc Billing Reports của project.
2. Trong Reports chọn đúng project ID, kỳ **tháng hiện tại đến hôm nay**, group
   theo **Service**. Ghi tổng chi phí, currency, kỳ và thời gian xem; giữ nguyên
   cách Console hiển thị credits/discounts để không trộn gross/net cost.
3. Xem các dịch vụ phát sinh phí, đặc biệt Cloud Run, Artifact Registry,
   Logging/Monitoring và network nếu xuất hiện. Ghi các dịch vụ không mong đợi
   hoặc khoản tăng cần giải thích; không mặc định mọi khoản phí do demo này tạo.
4. Credits miễn phí và min instances 0 không chứng minh không có chi phí. Billing
   có độ trễ; không coi chưa có data là $0. Nếu bị từ chối quyền, ghi **chưa kiểm
   tra được**, không mở thêm BigQuery export hoặc cấp Owner để vượt lỗi.
5. Không tạo budget, xóa image, đổi scaling hoặc bật thêm dịch vụ trong bước
   review này. Che billing account ID, địa chỉ cá nhân và payment details nếu
   chia sẻ screenshot. Có thể gửi lại chỉ amount/currency/kỳ và tên service.

## Điểm dừng

Sau khi review, cập nhật trạng thái từ bằng chứng thật. Nếu chưa có live Billing/
IAM observation, giữ pending; demo vẫn phải nêu rõ giới hạn đó. Không tiếp tục
tìm nút test email trong giao diện đã xem, gây lỗi dịch vụ, gửi verification code
để thay test notification, retrain, mở held-out test hoặc chạy lại load test.
