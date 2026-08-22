# Operations runbook

## Daily dashboard review

Check:

- due jobs and oldest-due age;
- failed/dead jobs and provider error codes;
- `delivery_unknown` records;
- last success/failure for every worker;
- unmatched or ambiguous Google reviews;
- sudden changes in SMS/email suppression counts.

An accepted Outlook message is not proof of delivery. Delivery is considered failed only after a
permanent NDR. A Twilio `delivered` callback is the strongest SMS delivery state.

## Safe retry procedure

The dashboard exposes retry only for `failed` and `dead` jobs. Before retrying:

1. Inspect the provider console using the stored provider message ID.
2. Confirm the provider did not accept or deliver the original.
3. Correct the destination/configuration problem.
4. Use the audited dashboard retry action once.

Never blindly retry `delivery_unknown`. That state intentionally blocks automatic resending because
the provider may already have the message.

## Worker or cron failure

1. Check the matching `worker_heartbeats` row and Render log request IDs.
2. Verify `/health/ready` and PostgreSQL availability.
3. Correct credentials/rate limits before triggering a manual run.
4. Run `python -m app.cli recover-stale-work`.
5. Run the smallest affected command rather than `process-all` when diagnosing.
6. Confirm the next scheduled run succeeds.

Multiple workers can overlap safely, but a sustained overlap means capacity or provider latency needs
attention.

## Provider incidents

### Twilio

- 401/403 callback: verify the exact `PUBLIC_BASE_URL`, webhook URL, proxy scheme, and auth token.
- 429 before acceptance retries with backoff; ambiguous timeout/5xx send results become
  `delivery_unknown` and require provider-console reconciliation.
- invalid destination or opt-out: permanent failure/suppression; do not retry unchanged.

### PoolBrain

- 403 webhook: compare the configured signing secret and raw-body signing method.
- API 401/403: rotate/update `POOLBRAIN_API_KEY`.
- malformed alert ID: event is retained/retried and the invalid alert is logged without sending.

### Microsoft Outlook

- `invalid_grant`: reconnect Outlook through the dashboard.
- 429 before acceptance retries with backoff; ambiguous timeout/5xx on the final send action becomes
  `delivery_unknown` and requires provider-console reconciliation.
- permanent NDR: exact recipient is email-suppressed.
- temporary delay: no suppression.

### Google Business Profile

- 429 while access is pending: keep `GOOGLE_SYNC_ENABLED=false` and wait for project approval.
- `invalid_grant`: reconnect Google.
- missing account/location: list accounts with the dashboard test, then correct the IDs.
- ambiguous reviewer: approve only after comparing the real Google review and customer record.

## Data and privacy

The daily retention command defaults to:

- 90 days for encrypted inbound content, forwarded reply bodies, and legacy plaintext inbound
  messages;
- 365 days for completed/dead inbound event payloads, provider callbacks, and attempt details;
- 730 days for administrator audit events.

Preference history and current suppression records are retained because they are evidence that a
customer opted out. Review campaigns and message summaries remain available for operational history.
Change retention only after confirming legal/accounting requirements.

## Backups and restore drills

- Enable the Render PostgreSQL plan's backup/PITR capability before production cutover.
- Review backup status weekly.
- Quarterly, restore to a separate database, run `alembic current`, compare key row counts, load the
  dashboard, and execute integration tests against the restored copy.
- Record restore duration and any manual steps. A backup that has not been restored in a drill is not
  considered verified.

## Secret rotation

- Twilio/PoolBrain/Microsoft/Google client secrets can be rotated by overlapping old/new provider
  credentials where supported, updating every service, testing, then revoking the old secret.
- `SECRET_KEY` invalidates dashboard sessions and may be rotated during a planned logout window.
- `TOKEN_ENCRYPTION_KEY` requires decrypting each stored token with the old key and re-encrypting with
  the new key. Never simply replace it.

## Scaling indicators

Scale or redesign when any of these persist:

- oldest due age above 10 minutes during sending hours;
- leases frequently expiring;
- database connection exhaustion;
- a worker run approaching the five-minute schedule interval;
- provider rate limits caused by concurrency;
- dashboard queries slowing as history grows.

First split `process-all` into independent continuous workers and tune each claim limit. Then add
managed pooling. Introduce a separate queue only after measuring PostgreSQL polling pressure.
