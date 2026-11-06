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
