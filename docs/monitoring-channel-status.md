# Notification channel usable does not mean delivery verified

The owner created an enabled email channel, but ListNotificationChannels omitted
`verificationStatus`. The previous collector required the literal `VERIFIED`
value and would reject this valid API case before creating policies.

The official [Monitoring v3 discovery schema](https://monitoring.googleapis.com/$discovery/rest?version=v3),
`NotificationChannel.verificationStatus`, states:

> If the value is UNVERIFIED, then it indicates that the channel is
> non-functioning (it both requires verification and lacks verification);
> otherwise, it is assumed that the channel works.

The unspecified enum can mean omitted, not applicable, or verification-exempt.
It does not prove that an email has been received. This fix changes only the
operator monitoring collector, not serving or model behavior:

| Google status | Enabled channel may be configured | Explicitly verified |
| --- | --- | --- |
| `VERIFIED` | Yes | Yes |
| Omitted / `VERIFICATION_STATUS_UNSPECIFIED` | Yes | No |
| `UNVERIFIED` | No | No |
| Unknown future value | No; review first | No |

Disabled channels are rejected regardless of verification status. `apply` and
`inspect` now expose `channel_usable`, `channel_verified` and the normalized
`channel_verification_status`. `notification_delivery_verified` remains false;
policy configuration, test email receipt and full alert-path testing remain
separate evidence levels. No exception messages, channel labels or email
addresses are emitted.

The focused reproduction failed against the strict old guard and passes after
the fix. Tests retain pre-write rejection for explicitly unverified, disabled
and unknown-state channels. No live alert mutation or delivery test has been
performed by the Codex runner, which still has no Google identity.

From an authorized Cloud Shell, first update a clean **operator** checkout to
the reviewed fix (never the original `b4accd8` bundle checkout). Then apply the
existing two scoped policies and inspect them. A nonzero command means stop and
send its sanitized JSON, rather than rerun or add roles. Use the actual chosen
channel resource name; never a token or an email address. Keep both resulting
JSON receipts in ignored `outputs/` and download them for review.

## Observed policy readback: omitted zero threshold

The owner applied both policies successfully. The uploaded
`repeat-monitoring-configuration.tar.gz` has SHA256
`7e4af37d8254798eb445fa636b69060f64bab882dcd9aecc4344767dd1f64ccc`.
Its inventory confirms enabled unique policies, matching channel, four matching
log-metric filters/label extractors and an existing dashboard. Both `condition_matches`
flags were false in that original receipt; this receipt is retained unchanged.

A subsequent read-only policy GET showed that the filters, comparison, duration,
trigger and aggregation exactly matched the requested policy, but Google omitted
`thresholdValue: 0`. The [official protobuf definition](https://github.com/googleapis/googleapis/blob/master/google/monitoring/v3/alert.proto)
uses `double threshold_value = 5`; its implicit numeric default is zero. Strict
JSON dictionary equality therefore produced a false mismatch.

The inventory now normalizes **only** the missing numeric-zero threshold.
Nonzero thresholds, boolean/non-numeric values, changed service filters,
comparison, duration, aggregation or extra/missing conditions still mismatch.
No policy is edited by this fix. Re-run only the read-only `inspect` operation
into a new receipt, preserving the original false-flag report. Configuration
compatibility and dashboard existence remain separate from exported sample
availability, email receipt and end-to-end alert testing, which are unverified.
