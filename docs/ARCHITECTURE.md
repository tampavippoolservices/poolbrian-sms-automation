# Architecture and reliability decisions

## Durable inbound boundary

PoolBrain webhooks are authenticated with an HMAC over the unchanged request body. Twilio requests
are validated with Twilio's request validator against the canonical public URL, full path, query
string, and form values. Microsoft notifications require the configured `clientState`. A validated
event is inserted with a provider/external-ID uniqueness constraint before a 2xx response is sent.

Webhook routes do not perform PoolBrain customer lookups or send messages. This keeps acknowledgement
fast and makes provider retries safe.

## Message state machine

```text
queued/retry -> leased -> sending -> accepted -> sent -> delivered
                      \-> retry/failed/dead
                      \-> delivery_unknown
```

- Claims use row locks with `SKIP LOCKED`, a lease expiry, a worker ID, and an incremented attempt.
- Idempotency keys prevent multiple jobs for the same source event/campaign step.
- Retry delays grow exponentially and stop at one hour.
- Retryable errors known to occur before provider acceptance requeue until `max_attempts`; permanent
  errors fail immediately.
- A timeout, connection loss, or ambiguous 5xx during the provider send mutation immediately becomes
  `delivery_unknown`; it is never automatically retried.
- Once a provider reports acceptance, an unexpected persistence error never causes an automatic
  resend. Lease recovery changes the record to `delivery_unknown` for manual reconciliation.
- Twilio callback ranks prevent a delayed lower-status callback from overwriting a terminal status.

This supports multiple concurrent workers without duplicate claims. PostgreSQL remains the queue so
there is no second infrastructure dependency at current volume. When sustained throughput makes
database polling expensive, the repository boundary allows a later move to SQS or another broker
without changing route or provider logic.

## Campaign invariants

- `source_job_id` is unique.
- One active/recent campaign per customer is enforced under a PostgreSQL advisory transaction lock.
- Confirmed reviews suppress future campaigns indefinitely unless an administrator explicitly undoes
  the confirmation.
- Active/recent campaigns suppress duplicates for `REVIEW_SUPPRESSION_DAYS` (120 by default).
- Pending campaign jobs are cancelled on reply, manual cancellation, confirmed review, or matching
  channel suppression.
- Google name matching cannot auto-confirm an ambiguous review.

## Security and privacy

- Production dashboard authentication uses Microsoft OIDC with an explicit email/domain allowlist.
- Render's single trusted forwarding hop is normalized for accurate scheme and audit IP data; host
  and provider signature validation still use configured canonical values.
- All modifying dashboard forms use session CSRF tokens and produce audit records.
- OAuth state is random, hashed in storage, short-lived, and single use.
- OAuth refresh tokens and inbound SMS bodies are encrypted with Fernet at rest.
- Dashboard contacts are masked; Sentry does not collect default PII; logs mask phone numbers,
  email addresses, bearer credentials, and OAuth secrets. Access logs use route templates so OAuth,
  review, and unsubscribe tokens never appear in logged URLs.
- Security headers deny framing, MIME sniffing, unnecessary browser permissions, and cross-origin
  referrers. Production enables HSTS and secure cookies.
- Email unsubscribe uses a GET confirmation and POST action so link scanners cannot unsubscribe a
  customer merely by previewing a link.
- The daily retention job scrubs encrypted inbound and forwarded message content after 90 days and
  expires low-value operational/audit records on their configured schedules.

## Email and Google behavior

Outlook messages are created as MIME drafts containing `text/plain` and `text/html` alternatives and
an internal job header, then sent as a separate Graph action. A successful send action means
"accepted," not delivered. Permanent NDRs are detected conservatively, suppress that exact address,
and fail its latest email job. Temporary delays do not suppress.

Google reviews are fetched from every response page. Provider review IDs make import idempotent. An
exact normalized reviewer/customer name with one eligible campaign can auto-match; otherwise the
review is a dashboard candidate for manual approval.

## Scaling path

1. Current: one web service, PostgreSQL, overlapping-safe five-minute cron workers.
2. Increased volume: separate continuous message/event workers and independently scale web workers.
3. Higher throughput: add PgBouncer/managed connection pooling and reduce direct connection counts.
4. Large scale: replace database polling with a managed queue while retaining database idempotency.
5. Multiple locations: add tenant/location ownership columns and per-location provider credentials.

Do not scale worker concurrency by merely increasing Gunicorn workers; message work runs through the
CLI worker process, not request threads.
