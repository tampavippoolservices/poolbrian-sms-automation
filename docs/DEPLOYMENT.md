# Controlled production deployment

This is a stateful migration from the existing Render service. Use a maintenance window and keep the
old service available for rollback. Never allow old and new completed-job schedulers to run together.

## 1. Before the window

1. Require both GitHub Actions jobs on the production branch.
2. Take a Render PostgreSQL backup and confirm the restore point is visible.
3. Save the currently deployed commit ID and export the existing environment-variable names (not
   their values) for comparison.
4. Create a staging service and database from `render.yaml`; use test Twilio/Outlook destinations.
5. Run `alembic upgrade head` in staging and exercise the smoke checklist below.
6. Confirm Google Business Profile API access. A 429/pending response means leave Google sync disabled;
   the rest of the deployment can proceed.

## 2. Required shared configuration

The following values must be identical on the web service and every cron service that uses them:

- `DATABASE_URL`
- `TOKEN_ENCRYPTION_KEY`
- PoolBrain/Twilio credentials
- Google client credentials, account/location IDs, and redirect URL
- Microsoft tenant/client credentials, sender, redirect URL, and webhook client state
- `PUBLIC_BASE_URL`

Render `sync: false` variables must be entered manually for each existing service. YAML aliases copy
the declaration, not a secret value from one already-created service. A mismatched encryption key
will break OAuth token reads.

Use production OIDC administration:

```text
ADMIN_AUTH_MODE=oidc
MICROSOFT_ADMIN_REDIRECT_URI=https://<host>/auth/callback
ADMIN_ALLOWED_EMAILS=<comma-separated administrators>
```

Provider redirect URIs must exactly match:

```text
https://<host>/google/oauth/callback
https://<host>/microsoft/oauth/callback
https://<host>/auth/callback
```

## 3. Migration and cutover

1. Stop/disable the old completed-service and review-request cron jobs.
2. Deploy the new web release. The Render pre-deploy command applies Alembic migrations.
3. Verify `GET /health/live` returns 200 and `GET /health/ready` returns 200.
4. Sign in through `/auth/login`; confirm the dashboard loads and unauthorized accounts receive 403.
5. Update PoolBrain to `POST https://<host>/webhooks/poolbrain` and retain only the required alert
   event. Copy the signing secret into Render.
6. Update Twilio incoming messages to
   `POST https://<host>/webhooks/twilio/inbound`. Status callbacks are attached automatically when
   each new message is sent.
7. Review recent PoolBrain completed jobs, then run exactly once:

   ```bash
   python -m app.cli initialize-baseline --confirm
   ```

8. Trigger `process-all` manually. Expect zero historical customer sends and a successful heartbeat.
9. Enable the new five-minute worker cron. Confirm two consecutive successful runs.
10. Connect Outlook from the dashboard, send to a controlled address, verify the multipart email and
    unsubscribe confirmation, set `OUTLOOK_BOUNCE_SYNC_ENABLED=true`, then verify the bounce cron.
11. When Google approval is active, connect Google, test the connection, run one review sync, and then
    set `GOOGLE_SYNC_ENABLED=true` and verify its cron. Until those flags are enabled, both cron jobs
    exit successfully as disabled instead of producing incident noise.

## 4. Smoke checklist

- Invalid PoolBrain and Twilio signatures return 403 and create no rows.
- Duplicate PoolBrain event IDs create one inbound event.
- A controlled completed job creates one completed-service SMS, one review SMS, and two email jobs.
- Re-running workers does not create extra jobs or messages.
- Twilio delivered and failed callbacks appear on the linked message job.
- A test reply stops the campaign and pending reminders.
- STOP records an SMS suppression event and cancels pending SMS.
- Email unsubscribe requires confirmation and cancels pending email for that address.
- Manual review confirmation cancels reminders; undo restores review eligibility without resending old
  cancelled jobs.
- Dashboard dates match America/New_York and contacts remain masked.
- Sentry receives a controlled non-customer exception without message bodies or full phone numbers.

## 5. Rollback

1. Disable all new cron jobs first.
2. Point PoolBrain and Twilio webhooks back to the previous release if it is on a different hostname.
3. Redeploy the saved old commit.
4. Do **not** downgrade the database during an incident. The first migration preserves legacy tables
   and its new tables are additive; leaving them in place is safer.
5. Re-enable only the old scheduler after confirming the new scheduler is stopped.
6. Reconcile any `accepted`, `sending`, or `delivery_unknown` messages in Twilio/Outlook before
   scheduling retries.

Only use `alembic downgrade` in a tested restore environment. For a severe migration incident, restore
the pre-deploy database backup rather than attempting ad-hoc production DDL.
