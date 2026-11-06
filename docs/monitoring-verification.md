# Monitoring configuration verification

The owner-provided read-only `monitoring-inventory-normalized.json` has SHA256
`b869b639b890b6d346fe234099a973c002fd926125b5f15a9316860d621b93a7`.
It was inspected offline and retained unchanged in ignored
`outputs/milestone4/received-monitoring-configuration/`. The collector fix is
commit `d49c5456b3c27577d1d6945e10b4cf649c5c480b`; PR #6 passed CI and was merged
at `ec786552c066fe69c058754d47f44268eb035b45`.

## What the receipt establishes

| Check | Result |
| --- | --- |
| Service 5xx policy | Unique, enabled, channel and normalized condition match |
| Bundle-load failure policy | Unique, enabled, channel and normalized condition match |
| Four log-based metrics | Filters and label extractors match |
| Dashboard | Present |
| Channel | Enabled and usable; verification status unspecified, not explicitly VERIFIED |
| Received test email | **Not verified** |
| Full condition-to-notification path | **Not verified** |
| Availability/freshness of metric samples | Not checked by this inventory |

The two alerts apply to `repeat-order-historical-demo` in `us-central1`: positive
5xx count or bundle-load-failure count in a 300-second aligned window. No model,
serving policy or customer data was changed. This record does not assert that a
real incident occurred, a notification arrived, or the service meets an SLA.

## Preserve the original observations

The earlier configuration archive has SHA256
`7e4af37d8254798eb445fa636b69060f64bab882dcd9aecc4344767dd1f64ccc`.
It reported both conditions false under strict JSON equality. The owner's policy
GET showed an omitted numeric-zero threshold; the corrected read-only inventory
normalized that proto3 default and confirmed matching conditions. Neither the
original receipt nor live policies were rewritten to manufacture a pass.

The channel also omits `verificationStatus`. Google treats unspecified as
usable, which is different from proving receipt of mail. The owner-supplied
current Email-channel UI showed link/edit/delete controls and no test button.
No test email, forced production error, verification-code workaround, or new load
run was used. Record email delivery/full alert path as unverified rather than
continuing to search that interface or asserting all channel types lack testing.
See [API compatibility details](monitoring-channel-status.md).

## Evidence limits and completion boundary

This is owner-supplied, timestamp-unembedded configuration evidence, checked by
the Codex runner offline. It is not a fresh independent Google API observation
by Codex, which has no Google identity. Dashboard existence does not verify its
current samples. Customer behavior, conversion/revenue lift, sustained capacity,
client cold-start and held-out model quality remain outside these checks.

The operational demo can be presented with these explicit limits. No further
alert reapply, deployment or load test is required to complete this evidence pack.
