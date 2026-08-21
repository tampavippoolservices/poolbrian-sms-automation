from flask import (
    Flask,
    request,
    jsonify,
    render_template_string,
    Response,
    redirect
)
from twilio.rest import Client
from twilio.request_validator import RequestValidator
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from urllib.parse import urlencode
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import psycopg
import json
import os
import secrets
import hmac
import hashlib

app = Flask(__name__)

def check_dashboard_auth():
    username = os.environ.get("DASHBOARD_USERNAME")
    password = os.environ.get("DASHBOARD_PASSWORD")

    auth = request.authorization

    return (
        auth is not None
        and auth.username == username
        and auth.password == password
    )


def require_dashboard_auth():
    if check_dashboard_auth():
        return None

    return Response(
        "Login required",
        401,
        {
            "WWW-Authenticate": 'Basic realm="Tampa VIP SMS Dashboard"'
        }
    )
def valid_twilio_request():
    auth_token = os.environ.get(
        "TWILIO_AUTH_TOKEN"
    )
    signature = request.headers.get(
        "X-Twilio-Signature",
        ""
    )
    public_base_url = os.environ.get(
        "PUBLIC_BASE_URL"
    )

    if (
        not auth_token
        or not signature
        or not public_base_url
    ):
        return False

    requested_path = request.full_path

    if requested_path.endswith("?"):
        requested_path = requested_path[:-1]

    validation_url = (
        public_base_url.rstrip("/")
        + requested_path
    )

    validator = RequestValidator(auth_token)

    return validator.validate(
        validation_url,
        request.form,
        signature
    )
    
POOLBRAIN_BASE_URL = "https://prodapi.poolbrain.com"

def valid_poolbrain_webhook():
    signing_secret = os.environ.get(
        "POOLBRAIN_WEBHOOK_SIGNING_SECRET"
    )
    received_signature = request.headers.get(
        "X-Webhook-Signature",
        ""
    )

    if not signing_secret or not received_signature:
        return False

    raw_request_body = request.get_data()

    expected_signature = hmac.new(
        signing_secret.encode("utf-8"),
        raw_request_body,
        hashlib.sha256
    ).hexdigest()

    return hmac.compare_digest(
        expected_signature,
        received_signature.strip()
    )
    
def get_db_connection():
    database_url = os.environ.get("DATABASE_URL")

    if not database_url:
        raise Exception("DATABASE_URL is missing")

    return psycopg.connect(database_url)


def init_db():
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS processed_jobs (
                    record_id BIGINT PRIMARY KEY,
                    customer_id BIGINT,
                    status TEXT NOT NULL,
                    processed_at TIMESTAMPTZ DEFAULT NOW(),
                    twilio_message_sid TEXT
                )
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS processed_alerts (
                alert_id BIGINT PRIMARY KEY,
                customer_id BIGINT,
                job_id BIGINT,
                alert_type TEXT NOT NULL,
                status TEXT NOT NULL,
                processed_at TIMESTAMPTZ DEFAULT NOW(),
                twilio_message_sid TEXT
             )
         """)
            
            cur.execute("""
                CREATE TABLE IF NOT EXISTS automation_state (
                    key TEXT PRIMARY KEY,
                    value TEXT
                )
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS review_requests (
                    record_id BIGINT PRIMARY KEY,
                    customer_id BIGINT NOT NULL,
                    customer_phone TEXT,
                    status TEXT NOT NULL DEFAULT 'queued',
                    scheduled_for TIMESTAMPTZ NOT NULL,
                    first_message_sid TEXT,
                    first_sent_at TIMESTAMPTZ,
                    link_clicked_at TIMESTAMPTZ,
                    customer_replied_at TIMESTAMPTZ,
                    reminder_message_sid TEXT,
                    reminder_sent_at TIMESTAMPTZ,
                    cancelled_reason TEXT,
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    updated_at TIMESTAMPTZ DEFAULT NOW()
                )
            """)

            cur.execute("""
                ALTER TABLE review_requests
                ADD COLUMN IF NOT EXISTS review_token TEXT
            """)
            
            cur.execute("""
                ALTER TABLE review_requests
                ADD COLUMN IF NOT EXISTS confirmed_review_at TIMESTAMPTZ
            """)
            
            cur.execute("""
                ALTER TABLE review_requests
                ADD COLUMN IF NOT EXISTS google_review_id TEXT
            """)
            
            cur.execute("""
                ALTER TABLE review_requests
                ADD COLUMN IF NOT EXISTS google_reviewer_name TEXT
            """)
            
            cur.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS
                review_requests_review_token_key
                ON review_requests (review_token)
                WHERE review_token IS NOT NULL
            """)

            cur.execute("""
                ALTER TABLE review_requests
                ADD COLUMN IF NOT EXISTS customer_name TEXT
            """)

            cur.execute("""
                ALTER TABLE review_requests
                ADD COLUMN IF NOT EXISTS first_delivery_status TEXT
            """)
            
            cur.execute("""
                ALTER TABLE review_requests
                ADD COLUMN IF NOT EXISTS first_delivery_updated_at TIMESTAMPTZ
            """)

                        cur.execute("""
                CREATE TABLE IF NOT EXISTS
                communication_suppressions (
                    channel TEXT NOT NULL
                        CHECK (channel IN ('sms', 'email')),
                    destination TEXT NOT NULL,
                    is_suppressed BOOLEAN NOT NULL
                        DEFAULT TRUE,
                    reason TEXT,
                    source TEXT,
                    suppressed_at TIMESTAMPTZ
                        DEFAULT NOW(),
                    resumed_at TIMESTAMPTZ,
                    created_at TIMESTAMPTZ
                        DEFAULT NOW(),
                    updated_at TIMESTAMPTZ
                        DEFAULT NOW(),
                    PRIMARY KEY (channel, destination)
                )
            """)
            
            cur.execute("""
                CREATE TABLE IF NOT EXISTS google_reviews (
                    google_review_id TEXT PRIMARY KEY,
                    reviewer_name TEXT,
                    star_rating TEXT,
                    review_comment TEXT,
                    review_created_at TIMESTAMPTZ,
                    review_updated_at TIMESTAMPTZ,
                    matched_record_id BIGINT,
                    match_status TEXT NOT NULL DEFAULT 'unmatched',
                    imported_at TIMESTAMPTZ DEFAULT NOW(),
                    updated_at TIMESTAMPTZ DEFAULT NOW()
                )
            """)
            
            cur.execute("""
                CREATE INDEX IF NOT EXISTS
                google_reviews_match_status_idx
                ON google_reviews (match_status)
            """)

        conn.commit()

def claim_water_alert(
    alert_id,
    customer_id,
    job_id
):
    if not alert_id:
        return False

    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO processed_alerts (
                    alert_id,
                    customer_id,
                    job_id,
                    alert_type,
                    status
                )
                VALUES (
                    %s,
                    %s,
                    %s,
                    'WaterLevelLow',
                    'processing'
                )
                ON CONFLICT (alert_id) DO NOTHING
                RETURNING alert_id
            """, (
                alert_id,
                customer_id,
                job_id
            ))

            claimed_row = cur.fetchone()

        conn.commit()

    return claimed_row is not None
    
def alert_already_processed(alert_id):
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT status
                FROM processed_alerts
                WHERE alert_id = %s
            """, (alert_id,))

            row = cur.fetchone()

            return row is not None


def save_processed_alert(
    alert_id,
    customer_id,
    job_id,
    alert_type,
    status,
    twilio_message_sid=None
):
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO processed_alerts (
                    alert_id,
                    customer_id,
                    job_id,
                    alert_type,
                    status,
                    twilio_message_sid
                )
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (alert_id)
                DO UPDATE SET
                    customer_id = EXCLUDED.customer_id,
                    job_id = EXCLUDED.job_id,
                    alert_type = EXCLUDED.alert_type,
                    status = EXCLUDED.status,
                    twilio_message_sid = EXCLUDED.twilio_message_sid,
                    processed_at = NOW()
            """, (
                alert_id,
                customer_id,
                job_id,
                alert_type,
                status,
                twilio_message_sid
            ))

        conn.commit()

def claim_completed_job(
    record_id,
    customer_id
):
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO processed_jobs (
                    record_id,
                    customer_id,
                    status
                )
                VALUES (%s, %s, 'processing')
                ON CONFLICT (record_id) DO NOTHING
                RETURNING record_id
            """, (
                record_id,
                customer_id
            ))

            claimed_row = cur.fetchone()

        conn.commit()

    return claimed_row is not None

def job_already_processed(record_id):
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT status
                FROM processed_jobs
                WHERE record_id = %s
            """, (record_id,))

            row = cur.fetchone()

            if not row:
                return False

            return row[0] in (
                "baseline",
                "text_sent"
            )


def save_processed_job(
    record_id,
    customer_id,
    status,
    twilio_message_sid=None
):
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO processed_jobs (
                    record_id,
                    customer_id,
                    status,
                    twilio_message_sid
                )
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (record_id)
                DO UPDATE SET
                    customer_id = EXCLUDED.customer_id,
                    status = EXCLUDED.status,
                    twilio_message_sid = EXCLUDED.twilio_message_sid,
                    processed_at = NOW()
            """, (
                record_id,
                customer_id,
                status,
                twilio_message_sid
            ))

        conn.commit()

def queue_review_request(
    record_id,
    customer_id,
    customer_name,
    customer_phone
):
    scheduled_for = (
        datetime.now(ZoneInfo("America/New_York"))
        + timedelta(hours=3)
    )

    review_token = secrets.token_urlsafe(24)

    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
               SELECT 1
               FROM review_requests
               WHERE customer_id = %s
               AND (
                    confirmed_review_at IS NOT NULL
                    OR (
                        status IN (
                            'queued',
                            'first_sent',
                            'clicked',
                            'reminder_sent',
                            'completed'
                        )
                        AND created_at >= NOW() - INTERVAL '120 days'
                    )
                )
                LIMIT 1
                
            """, (customer_id,))

            recent_request = cur.fetchone()

            if recent_request:
                print(
                    f"Review request skipped for customer "
                    f"{customer_id}: request sent or queued "
                    f"within the last 120 days."
                )
                return False

            cur.execute("""
                INSERT INTO review_requests (
                    record_id,
                    customer_id,
                    customer_name,
                    customer_phone,
                    status,
                    scheduled_for,
                    review_token
                )
                VALUES (%s, %s, %s,%s, 'queued',%s, %s)
                ON CONFLICT (record_id) DO NOTHING
            """, (
                record_id,
                customer_id,
                customer_name,
                customer_phone,
                scheduled_for,
                review_token
            ))

            request_created = cur.rowcount == 1

        conn.commit()

    if request_created:
        print(
            f"Review request queued for customer "
            f"{customer_id} at {scheduled_for}."
        )

    return request_created


def baseline_is_complete():
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT value
                FROM automation_state
                WHERE key = 'completed_service_baseline'
            """)

            row = cur.fetchone()

            return row is not None and row[0] == "complete"


def mark_baseline_complete():
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO automation_state (key, value)
                VALUES ('completed_service_baseline', 'complete')
                ON CONFLICT (key)
                DO UPDATE SET value = EXCLUDED.value
            """)

        conn.commit()

def send_sms(
    to_number,
    message_body,
    message_type="general",
    reference_id=None
):
    account_sid = os.environ.get("TWILIO_ACCOUNT_SID")
    auth_token = os.environ.get("TWILIO_AUTH_TOKEN")
    twilio_number = os.environ.get("TWILIO_PHONE_NUMBER")
    status_callback = os.environ.get(
        "TWILIO_STATUS_CALLBACK_URL"
    )

    client = Client(account_sid, auth_token)

    callback_url = status_callback

    if status_callback:
        callback_params = {
            "message_type": message_type
        }

        if reference_id is not None:
            callback_params["reference_id"] = str(
                reference_id
            )

        separator = "&" if "?" in status_callback else "?"
        callback_url = (
            status_callback
            + separator
            + urlencode(callback_params)
        )

    message = client.messages.create(
        body=message_body,
        from_=twilio_number,
        to=to_number,
        status_callback=callback_url
    )

    return message.sid


def get_google_access_token():
    client_id = os.environ.get(
        "GOOGLE_OAUTH_CLIENT_ID"
    )
    client_secret = os.environ.get(
        "GOOGLE_OAUTH_CLIENT_SECRET"
    )

    if not client_id or not client_secret:
        raise Exception(
            "Google OAuth client configuration is missing"
        )

    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT value
                FROM automation_state
                WHERE key = 'google_refresh_token'
            """)

            refresh_row = cur.fetchone()

    if not refresh_row:
        raise Exception(
            "Google Business Profile is not connected"
        )

    refresh_token = refresh_row[0]

    token_request_data = urlencode({
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token"
    }).encode("utf-8")

    token_request = Request(
        "https://oauth2.googleapis.com/token",
        data=token_request_data,
        headers={
            "Content-Type": (
                "application/x-www-form-urlencoded"
            )
        },
        method="POST"
    )

    with urlopen(token_request, timeout=15) as response:
        token_data = json.loads(
            response.read().decode("utf-8")
        )

    access_token = token_data.get("access_token")

    if not access_token:
        raise Exception(
            "Google did not return an access token"
        )

    return access_token

def poolbrain_get(endpoint, params=None):
    api_key = os.environ.get("POOLBRAIN_API_KEY")

    if not api_key:
        raise Exception("POOLBRAIN_API_KEY is missing")

    url = POOLBRAIN_BASE_URL + endpoint

    if params:
        url += "?" + urlencode(params)

    req = Request(
        url,
        headers={
            "ACCESS-KEY": api_key,
            "Accept": "application/json"
        }
    )

    with urlopen(req, timeout=10) as response:
        return json.loads(
            response.read().decode("utf-8")
        )


def get_customer(customer_id):
    result = poolbrain_get(
        "/v2/customer_detail",
        {
            "customerId": str(customer_id)
        }
    )

    customers = result.get("data", [])

    if not customers:
        return None

    if isinstance(customers, list):
        return customers[0]

    if isinstance(customers, dict):
        if "CustomerName" in customers:
            return customers

        for value in customers.values():
            if (
                isinstance(value, dict)
                and "CustomerName" in value
            ):
                return value

    return None


def get_recent_completed_jobs():
    now = datetime.now(
        ZoneInfo("America/New_York")
    )

    from_date = (
        now - timedelta(days=1)
    ).strftime("%Y-%m-%d")

    to_date = now.strftime("%Y-%m-%d")

    result = poolbrain_get(
        "/v2/route_stops_job_list",
        {
            "fromDate": from_date,
            "toDate": to_date
        }
    )

    jobs = result.get("data", [])

    return [
        job
        for job in jobs
        if job.get("JobStatus") == "Completed"
    ]
@app.route("/incoming-sms", methods=["POST"])
def incoming_sms():
    if not valid_twilio_request():
        print("Rejected invalid incoming Twilio request.")
        return "Unauthorized", 403
    message_sid = request.form.get("MessageSid")
    from_number = request.form.get("From")
    to_number = request.form.get("To")
    message_body = request.form.get("Body", "")

    customer_name = "Unknown customer"

    try:
        clean_phone = "".join(
            digit for digit in from_number
            if digit.isdigit()
        )

        if clean_phone.startswith("1") and len(clean_phone) == 11:
            clean_phone = clean_phone[1:]

        result = poolbrain_get(
            "/v2/customer_detail",
            {
                "contactPhoneNumber": clean_phone
            }
        )

        customers = result.get("data", [])

        if isinstance(customers, list) and customers:
            customer_name = customers[0].get(
                "CustomerName",
                "Unknown customer"
            )

    except Exception as e:
        print("Customer lookup failed:", e)

    print(
        "Incoming customer SMS:",
        message_sid,
        from_number,
        customer_name,
        message_body
    )

    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS inbound_sms (
                    message_sid TEXT PRIMARY KEY,
                    from_number TEXT NOT NULL,
                    to_number TEXT,
                    message_body TEXT,
                    received_at TIMESTAMPTZ DEFAULT NOW()
                )
            """)

            cur.execute("""
                INSERT INTO inbound_sms (
                    message_sid,
                    from_number,
                    to_number,
                    message_body
                )
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (message_sid) DO NOTHING
            """, (
                message_sid,
                from_number,
                to_number,
                message_body
            ))

            from_digits = "".join(
                digit for digit in (from_number or "")
                if digit.isdigit()
            )
            
            cur.execute("""
                UPDATE review_requests
                SET
                    customer_replied_at = NOW(),
                    status = 'completed',
                    updated_at = NOW()
                WHERE record_id = (
                    SELECT record_id
                    FROM review_requests
                    WHERE RIGHT(
                        regexp_replace(
                            customer_phone,
                            '[^0-9]',
                            '',
                            'g'
                        ),
                        10
                    ) = RIGHT(%s, 10)
                    AND status IN (
                        'first_sent',
                        'reminder_sent'
                    )
                    ORDER BY first_sent_at DESC
                    LIMIT 1
                )
            """, (from_digits,))

        conn.commit()

    company_number = os.environ.get("COMPANY_PHONE_NUMBER")
    account_sid = os.environ.get("TWILIO_ACCOUNT_SID")
    auth_token = os.environ.get("TWILIO_AUTH_TOKEN")
    twilio_number = os.environ.get("TWILIO_PHONE_NUMBER")

    if company_number:
        try:
            client = Client(account_sid, auth_token)

            client.messages.create(
                body=(
                    f"Customer SMS reply from {customer_name} "
                    f"({from_number}): {message_body}"
                ),
                from_=twilio_number,
                to=company_number
            )

            print("Customer reply forwarded to company phone.")

        except Exception as e:
            print("Failed to forward customer reply:", e)

    return "", 204
@app.route("/twilio-status", methods=["POST"])
def twilio_status():
    if not valid_twilio_request():
        print("Rejected invalid Twilio status callback.")
        return "Unauthorized", 403
    message_sid = request.form.get("MessageSid")
    message_status = request.form.get("MessageStatus")
    error_code = request.form.get("ErrorCode")
    destination_number = request.form.get("To")
    message_type = request.args.get("message_type", "general")
    reference_id = request.args.get("reference_id")
    review_record_id = None
    
    if reference_id:
        try:
            review_record_id = int(reference_id)
    
            if review_record_id <= 0:
                review_record_id = None
    
        except (TypeError, ValueError):
            review_record_id = None

    print(
        "Twilio status update:",
        message_sid,
        message_status,
        error_code,
        destination_number,
        message_type
    )

    should_alert = False

    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS sms_delivery_status (
                    message_sid TEXT PRIMARY KEY,
                    message_status TEXT,
                    error_code TEXT,
                    updated_at TIMESTAMPTZ DEFAULT NOW()
                )
            """)

            cur.execute("""
                ALTER TABLE sms_delivery_status
                ADD COLUMN IF NOT EXISTS destination_number TEXT
            """)

            cur.execute("""
                ALTER TABLE sms_delivery_status
                ADD COLUMN IF NOT EXISTS alert_sent BOOLEAN DEFAULT FALSE
            """)

            cur.execute("""
                ALTER TABLE sms_delivery_status
                ADD COLUMN IF NOT EXISTS message_type TEXT DEFAULT 'general'
            """)

            cur.execute("""
                SELECT alert_sent
                FROM sms_delivery_status
                WHERE message_sid = %s
            """, (message_sid,))

            existing = cur.fetchone()

            already_alerted = (
                existing is not None
                and existing[0] is True
            )

            cur.execute("""
                INSERT INTO sms_delivery_status (
                    message_sid,
                    message_status,
                    error_code,
                    destination_number,
                    message_type
                )
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (message_sid)
                DO UPDATE SET
                    message_status = EXCLUDED.message_status,
                    error_code = EXCLUDED.error_code,
                    destination_number = EXCLUDED.destination_number,
                    message_type = EXCLUDED.message_type,
                    updated_at = NOW()
            """, (
                message_sid,
                message_status,
                error_code,
                destination_number,
                message_type
            ))

            if (
                message_type == "google_review"
                and review_record_id is not None
            ):
                delivery_failure = message_status in (
                    "failed",
                    "undelivered"
                )

                failure_reason = None

                if delivery_failure:
                    failure_reason = (
                        f"twilio_{message_status}"
                        + (
                            f"_error_{error_code}"
                            if error_code
                            else ""
                        )
                    )

                cur.execute("""
                    UPDATE review_requests
                    SET
                        first_message_sid = COALESCE(
                            first_message_sid,
                            %s
                        ),
                        first_delivery_status = %s,
                        first_delivery_updated_at = NOW(),
                        status = CASE
                            WHEN %s
                            AND status IN (
                                'sending',
                                'first_sent'
                            )
                                THEN 'send_failed'
                            ELSE status
                        END,
                        cancelled_reason = CASE
                            WHEN %s
                            AND status IN (
                                'sending',
                                'first_sent'
                            )
                                THEN %s
                            ELSE cancelled_reason
                        END,
                        updated_at = NOW()
                    WHERE record_id = %s
                """, (
                    message_sid,
                    message_status,
                    delivery_failure,
                    delivery_failure,
                    failure_reason,
                    review_record_id
                ))

            if (
                message_status in ("failed", "undelivered")
                and not already_alerted
            ):
                should_alert = True

        conn.commit()

    if should_alert:
        admin_number = os.environ.get("TEST_PHONE_NUMBER")
        account_sid = os.environ.get("TWILIO_ACCOUNT_SID")
        auth_token = os.environ.get("TWILIO_AUTH_TOKEN")
        twilio_number = os.environ.get("TWILIO_PHONE_NUMBER")

        if admin_number:
            try:
                client = Client(account_sid, auth_token)

                client.messages.create(
                    body=(
                        f"SMS ALERT: Message to {destination_number} "
                        f"was {message_status}. "
                        f"Twilio error: {error_code or 'none'}."
                    ),
                    from_=twilio_number,
                    to=admin_number
                )

                with get_db_connection() as conn:
                    with conn.cursor() as cur:
                        cur.execute("""
                            UPDATE sms_delivery_status
                            SET alert_sent = TRUE
                            WHERE message_sid = %s
                        """, (message_sid,))

                    conn.commit()

            except Exception as e:
                print("Failed to send admin SMS alert:", e)

    return "", 204

@app.route(
    "/confirm-review/<int:record_id>",
    methods=["POST"]
)
def confirm_review(record_id):
    auth_response = require_dashboard_auth()

    if auth_response:
        return auth_response

    reviewer_name = request.form.get(
        "reviewer_name",
        ""
    ).strip()

    if not reviewer_name:
        return (
            "Enter the name shown on Google before "
            "confirming this review.",
            400
        )

    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE review_requests
                SET
                    status = 'confirmed_review',
                    confirmed_review_at = COALESCE(
                        confirmed_review_at,
                        NOW()
                    ),
                    google_reviewer_name = NULLIF(%s, ''),
                    updated_at = NOW()
                WHERE record_id = %s
                RETURNING record_id
            """, (
                reviewer_name,
                record_id
            ))

            confirmed = cur.fetchone()

        conn.commit()

    if not confirmed:
        return "Review request not found.", 404

    return redirect("/dashboard")

@app.route(
    "/undo-review-confirmation/<int:record_id>",
    methods=["POST"]
)
def undo_review_confirmation(record_id):
    auth_response = require_dashboard_auth()

    if auth_response:
        return auth_response

    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE review_requests
                SET
                    status = CASE
                        WHEN link_clicked_at IS NOT NULL
                            THEN 'clicked'
                        WHEN first_sent_at IS NOT NULL
                            THEN 'first_sent'
                        ELSE 'queued'
                    END,
                    confirmed_review_at = NULL,
                    google_review_id = NULL,
                    google_reviewer_name = NULL,
                    updated_at = NOW()
                WHERE record_id = %s
                AND confirmed_review_at IS NOT NULL
                RETURNING record_id
            """, (record_id,))

            undone = cur.fetchone()

        conn.commit()

    if not undone:
        return "Confirmed review not found.", 404

    return redirect("/dashboard")

@app.route("/google/oauth/callback", methods=["GET"])
def google_oauth_callback():
    authorization_error = request.args.get("error")
    authorization_code = request.args.get("code")
    returned_state = request.args.get("state")

    if authorization_error:
        return (
            f"Google authorization failed: "
            f"{authorization_error}",
            400
        )

    if not authorization_code or not returned_state:
        return "Google authorization response is incomplete.", 400

    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT value
                FROM automation_state
                WHERE key = 'google_oauth_state'
            """)

            state_row = cur.fetchone()

    expected_state = (
        state_row[0]
        if state_row
        else None
    )

    if (
        not expected_state
        or returned_state != expected_state
    ):
        return "Invalid or expired Google authorization.", 400

    client_id = os.environ.get(
        "GOOGLE_OAUTH_CLIENT_ID"
    )
    client_secret = os.environ.get(
        "GOOGLE_OAUTH_CLIENT_SECRET"
    )
    redirect_uri = os.environ.get(
        "GOOGLE_OAUTH_REDIRECT_URI"
    )

    if not client_id or not client_secret or not redirect_uri:
        return "Google OAuth configuration is missing.", 500

    token_request_data = urlencode({
        "code": authorization_code,
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code"
    }).encode("utf-8")

    token_request = Request(
        "https://oauth2.googleapis.com/token",
        data=token_request_data,
        headers={
            "Content-Type": (
                "application/x-www-form-urlencoded"
            )
        },
        method="POST"
    )

    try:
        with urlopen(token_request, timeout=15) as response:
            token_data = json.loads(
                response.read().decode("utf-8")
            )
    except Exception as e:
        print("Google token exchange failed:", e)
        return "Google token exchange failed.", 500

    refresh_token = token_data.get("refresh_token")

    if not refresh_token:
        return (
            "Google did not return a refresh token. "
            "Please start the connection again.",
            400
        )

    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO automation_state (key, value)
                VALUES ('google_refresh_token', %s)
                ON CONFLICT (key)
                DO UPDATE SET value = EXCLUDED.value
            """, (refresh_token,))

            cur.execute("""
                DELETE FROM automation_state
                WHERE key = 'google_oauth_state'
            """)

        conn.commit()

    return render_template_string("""
        <!doctype html>
        <html>
        <head>
            <title>Google Connected</title>
        </head>
        <body style="
            font-family: Arial, sans-serif;
            max-width: 700px;
            margin: 60px auto;
        ">
            <h1>Google Business Profile Connected</h1>
            <p>
                Authorization was completed successfully.
            </p>
            <p>
                You may now return to the SMS dashboard.
            </p>
            <p>
                <a href="/dashboard">Open dashboard</a>
            </p>
        </body>
        </html>
    """)

@app.route("/google/test-connection", methods=["GET"])
def google_test_connection():
    auth_response = require_dashboard_auth()

    if auth_response:
        return auth_response

    try:
        access_token = get_google_access_token()

        accounts_request = Request(
            (
                "https://mybusinessaccountmanagement."
                "googleapis.com/v1/accounts"
            ),
            headers={
                "Authorization": (
                    f"Bearer {access_token}"
                ),
                "Accept": "application/json"
            }
        )

        with urlopen(
            accounts_request,
            timeout=15
        ) as response:
            accounts_data = json.loads(
                response.read().decode("utf-8")
            )

    except HTTPError as e:
        print(
            "Google Business Profile API error:",
            e.code,
            e.read().decode("utf-8")
        )

        return jsonify({
            "connected": False,
            "http_status": e.code,
            "message": (
                "Google API access is pending, disabled, "
                "or unavailable for this project."
            )
        }), e.code

    except Exception as e:
        print("Google connection test failed:", e)

        return jsonify({
            "connected": False,
            "message": str(e)
        }), 500

    accounts = accounts_data.get("accounts", [])

    return jsonify({
        "connected": True,
        "account_count": len(accounts),
        "accounts": [
            {
                "name": account.get("name"),
                "account_name": account.get(
                    "accountName"
                ),
                "type": account.get("type")
            }
            for account in accounts
        ]
    })

@app.route("/google/connect", methods=["GET"])
def google_connect():
    auth_response = require_dashboard_auth()

    if auth_response:
        return auth_response

    client_id = os.environ.get(
        "GOOGLE_OAUTH_CLIENT_ID"
    )
    redirect_uri = os.environ.get(
        "GOOGLE_OAUTH_REDIRECT_URI"
    )

    if not client_id or not redirect_uri:
        return "Google OAuth configuration is missing.", 500

    oauth_state = secrets.token_urlsafe(32)

    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO automation_state (key, value)
                VALUES ('google_oauth_state', %s)
                ON CONFLICT (key)
                DO UPDATE SET value = EXCLUDED.value
            """, (oauth_state,))

        conn.commit()

    authorization_url = (
        "https://accounts.google.com/o/oauth2/v2/auth?"
        + urlencode({
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": (
                "https://www.googleapis.com/auth/"
                "business.manage"
            ),
            "access_type": "offline",
            "prompt": "consent",
            "include_granted_scopes": "true",
            "state": oauth_state
        })
    )

    return redirect(authorization_url)

@app.route("/dashboard", methods=["GET"])
def dashboard():
    auth_response = require_dashboard_auth()

    if auth_response:
        return auth_response

    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT COUNT(*)
                FROM sms_delivery_status
                WHERE (updated_at AT TIME ZONE 'America/New_York')::date =
                    (NOW() AT TIME ZONE 'America/New_York')::date
            """)
            total_today = cur.fetchone()[0]

            cur.execute("""
                SELECT COUNT(*)
                FROM sms_delivery_status
                WHERE message_status = 'delivered'
                AND (updated_at AT TIME ZONE 'America/New_York')::date =
                    (NOW() AT TIME ZONE 'America/New_York')::date
            """)
            delivered_today = cur.fetchone()[0]

            cur.execute("""
                SELECT COUNT(*)
                FROM sms_delivery_status
                WHERE message_status IN ('failed', 'undelivered')
                AND (updated_at AT TIME ZONE 'America/New_York')::date =
                    (NOW() AT TIME ZONE 'America/New_York')::date
            """)
            failed_today = cur.fetchone()[0]

            cur.execute("""
                SELECT COUNT(*)
                FROM review_requests
                WHERE status = 'queued'
            """)
            reviews_queued = cur.fetchone()[0]
            
            cur.execute("""
                SELECT COUNT(*)
                FROM review_requests
                WHERE status = 'queued'
                AND scheduled_for <= NOW()
            """)
            reviews_due = cur.fetchone()[0]
            
            cur.execute("""
                SELECT COUNT(*)
                FROM review_requests
                WHERE first_sent_at IS NOT NULL
            """)
            reviews_sent = cur.fetchone()[0]
            
            cur.execute("""
                SELECT COUNT(*)
                FROM review_requests
                WHERE status = 'send_failed'
            """)
            reviews_failed = cur.fetchone()[0]

            cur.execute("""
                SELECT
                    message_sid,
                    message_status,
                    destination_number,
                    error_code,
                    message_type,
                    updated_at
                FROM sms_delivery_status
                ORDER BY updated_at DESC
                LIMIT 20
            """)    
            recent_rows = cur.fetchall()
            recent_rows = [
                (
                    row[0],
                    row[1],
                    row[2],
                    row[3],
                    row[4],
                    row[5]
                    .astimezone(ZoneInfo("America/New_York"))
                    .strftime("%Y-%m-%d %I:%M:%S %p")
                )
                for row in recent_rows
            ]
            cur.execute("""
                SELECT
                    from_number,
                    message_body,
                    received_at
                FROM inbound_sms
                ORDER BY received_at DESC
                LIMIT 20
            """)

            inbound_rows = cur.fetchall()

            inbound_rows = [
                (
                    row[0],
                    row[1],
                    row[2]
                    .astimezone(ZoneInfo("America/New_York"))
                    .strftime("%Y-%m-%d %I:%M:%S %p")
                )
                for row in inbound_rows
            ]
            cur.execute("""
                SELECT
                    record_id,
                    customer_name,
                    status,
                    TO_CHAR(
                        first_sent_at AT TIME ZONE 'America/New_York',
                        'YYYY-MM-DD HH12:MI AM'
                    ),
                    TO_CHAR(
                        link_clicked_at AT TIME ZONE 'America/New_York',
                        'YYYY-MM-DD HH12:MI AM'
                    ),
                    TO_CHAR(
                        confirmed_review_at AT TIME ZONE 'America/New_York',
                        'YYYY-MM-DD HH12:MI AM'
                    ),
                    google_reviewer_name
                FROM review_requests
                ORDER BY created_at DESC
                LIMIT 50
            """)
            
            review_rows = cur.fetchall()

    return render_template_string("""
        <!doctype html>
        <html>
        <head>
            <title>Tampa VIP SMS Dashboard</title>
            <style>
                body {
                    font-family: Arial, sans-serif;
                    max-width: 1000px;
                    margin: 40px auto;
                    padding: 0 20px;
                }

                .cards {
                    display: flex;
                    gap: 20px;
                    margin-bottom: 30px;
                }

                .card {
                    border: 1px solid #ddd;
                    border-radius: 10px;
                    padding: 20px;
                    flex: 1;
                }

                .number {
                    font-size: 32px;
                    font-weight: bold;
                }

                table {
                    width: 100%;
                    border-collapse: collapse;
                }

                th, td {
                    border-bottom: 1px solid #ddd;
                    padding: 10px;
                    text-align: left;
                }
            </style>
        </head>
        <body>
            <h1>Tampa VIP SMS Dashboard</h1>

            <div class="cards">
                <div class="card">
                    <div>Texts tracked today</div>
                    <div class="number">{{ total_today }}</div>
                </div>

                <div class="card">
                    <div>Delivered today</div>
                    <div class="number">{{ delivered_today }}</div>
                </div>

                <div class="card">
                    <div>Failed / undelivered</div>
                    <div class="number">{{ failed_today }}</div>
                </div>
            </div>

            <h2>Google Review Requests</h2>

            <div class="cards">
                <div class="card">
                    <div>Queued</div>
                    <div class="number">{{ reviews_queued }}</div>
                </div>
            
                <div class="card">
                    <div>Due now</div>
                    <div class="number">{{ reviews_due }}</div>
                </div>
            
                <div class="card">
                    <div>First messages sent</div>
                    <div class="number">{{ reviews_sent }}</div>
                </div>
            
                <div class="card">
                    <div>Send failures</div>
                    <div class="number">{{ reviews_failed }}</div>
                </div>
            </div>

            <h2>Recent SMS Statuses</h2>

            <table>
                <tr>
                    <th>Status</th>
                    <th>Phone</th>
                    <th>Type</th>
                    <th>Error</th>
                    <th>Updated</th>
                </tr>

                {% for row in recent_rows %}
                <tr>
                    <td>{{ row[1] }}</td>
                    <td>{{ row[2] or "Not recorded" }}</td>
                    <td>{{ row[4] or "general" }}</td>
                    <td>{{ row[3] or "" }}</td>
                    <td>{{ row[5] }}</td>
                </tr>
                {% endfor %}
            </table>
            <h2 style="margin-top: 40px;">Recent Customer Replies</h2>

            <table>
                <tr>
                    <th>Phone</th>
                    <th>Message</th>
                    <th>Received</th>
                </tr>
            
                {% for row in inbound_rows %}
                <tr>
                    <td>{{ row[0] }}</td>
                    <td>{{ row[1] }}</td>
                    <td>{{ row[2] }}</td>
                </tr>
                {% endfor %}
            </table>

            <h2 style="margin-top: 40px;">
                Google Review Tracking
            </h2>
            
            <table>
                <tr>
                    <th>Customer</th>
                    <th>Status</th>
                    <th>Sent</th>
                    <th>Clicked</th>
                    <th>Confirmed</th>
                    <th>Google reviewer</th>
                    <th>Action</th>
                </tr>
            
                {% for row in review_rows %}
                <tr>
                    <td>{{ row[1] or "Unknown" }}</td>
                    <td>{{ row[2] }}</td>
                    <td>{{ row[3] or "" }}</td>
                    <td>{{ row[4] or "" }}</td>
                    <td>{{ row[5] or "" }}</td>
                    <td>{{ row[6] or "" }}</td>
                    <td>
                        {% if row[2] != "confirmed_review" %}
                        <form
                            method="post"
                            action="/confirm-review/{{ row[0] }}"
                        >
                            <input
                                type="text"
                                name="reviewer_name"
                                placeholder="Name shown on Google"
                            >
                            <button type="submit">
                                Confirm review
                            </button>
                        </form>
                        {% else %}
                            <form
                                method="post"
                                action="/undo-review-confirmation/{{ row[0] }}"
                            >
                                <button type="submit">
                                    Undo confirmation
                                </button>
                            </form>
                        {% endif %}
                    </td>
                </tr>
                {% endfor %}
            </table>
        </body>
        </html>
    """,
        total_today=total_today,
        delivered_today=delivered_today,
        failed_today=failed_today,
        reviews_queued=reviews_queued,
        reviews_due=reviews_due,
        reviews_sent=reviews_sent,
        reviews_failed=reviews_failed,
        recent_rows=recent_rows,
        inbound_rows=inbound_rows,
        review_rows=review_rows
    )


@app.route("/", methods=["GET"])
def home():
    return "PoolBrain SMS Automation is running!"


@app.route("/webhook", methods=["POST"])
def poolbrain_webhook():
    if not valid_poolbrain_webhook():
        print("Rejected invalid PoolBrain webhook.")
        return jsonify({
            "success": False,
            "message": "Unauthorized"
        }), 403

    data = request.get_json(silent=True) or {}

    print(
        "PoolBrain webhook received:",
        data.get("id"),
        data.get("event")
    )

    event_type = data.get("event")

    if event_type == "alert.triggered":
        jobs = data.get("data", {}).get("data", [])

        for job in jobs:
            customer_id = job.get("CustomerID")
            job_id = job.get("JobID")

            alert_categories = job.get("AlertCategories", [])

            for category in alert_categories:
                issue_reports = category.get("IssueReport", [])

                for alert in issue_reports:
                    alert_name = (
                        alert.get("AlertName")
                        or alert.get("type")
                        or ""
                    )

                    print(f"Alert detected: {alert_name}")

                    alert_text = alert_name.lower()

                    if alert_name == "WaterLevelLow":
                        raw_alert_id = alert.get("alertId")
                        
                        try:
                            alert_id = int(raw_alert_id)
                            customer_id = int(customer_id)
                        
                            if job_id is not None:
                                job_id = int(job_id)
                        
                        except (TypeError, ValueError):
                            print(
                                "Skipped Water Level Low alert "
                                "with invalid identifiers."
                            )
                            continue
                        
                        if alert_id <= 0 or customer_id <= 0:
                            print(
                                "Skipped Water Level Low alert "
                                "with non-positive identifiers."
                            )
                            continue
                        
                        print("WATER LEVEL LOW DETECTED")
                        print(f"CustomerId: {customer_id}")
                        print(f"JobID: {job_id}")
                        print(f"AlertId: {alert_id}")

                        init_db()

                        if not claim_water_alert(
                            alert_id,
                            customer_id,
                            job_id
                        ):
                            print(
                                f"Water Level Low alert {alert_id} "
                                f"was already claimed or is invalid."
                            )
                            continue

                        customer = get_customer(customer_id)

                        if not customer:
                            print(f"Customer {customer_id} not found")
                            continue

                        customer_name = customer.get(
                            "CustomerName",
                            "Customer"
                        )

                        customer_phone = customer.get("Phone")

                        if not customer_phone:
                            print(
                                f"No phone number for customer "
                                f"{customer_id}"
                            )
                        
                            save_processed_alert(
                                alert_id,
                                customer_id,
                                job_id,
                                "WaterLevelLow",
                                "no_phone"
                            )

                        continue

                        message_body = (
                            f"Hi {customer_name}, your pool technician noticed that "
                            f"your pool water level is low. Please add water to the "
                            f"normal operating level when convenient. "
                            f"Tampa VIP Pool Services"
                        )

                        try:
                            sid = send_sms(
                                customer_phone,
                                message_body,
                                "water_level_low"
                            )
                        except Exception as e:
                            print(
                                f"Water Level Low SMS failed for "
                                f"alert {alert_id}: {e}"
                            )
                        
                            save_processed_alert(
                                alert_id,
                                customer_id,
                                job_id,
                                "WaterLevelLow",
                                "send_failed"
                            )
                        
                            continue
                        

                        save_processed_alert(
                            alert_id,
                            customer_id,
                            job_id,
                            "WaterLevelLow",
                            "text_sent",
                            sid
                        )

                        print(
                            f"Water Level Low SMS sent to {customer_name}. "
                            f"Twilio SID: {sid}"
                        )

    return jsonify({
        "success": True,
        "message": "Webhook received"
    }), 200


@app.route("/process-completed-services", methods=["GET"])
def process_completed_services():
    provided_secret = request.headers.get("X-Process-Secret")
    expected_secret = os.environ.get("PROCESS_SECRET")

    if not expected_secret or provided_secret != expected_secret:
        return "Unauthorized", 401

    now = datetime.now(ZoneInfo("America/New_York"))
    current_hour = now.hour

    if current_hour < 6 or current_hour >= 19:
        return "Outside allowed processing hours.", 200

    init_db()

    completed_jobs = get_recent_completed_jobs()

    if not baseline_is_complete():
        count = 0

        for job in completed_jobs:
            record_id = job.get("RecordID")
            customer_id = job.get("CustomerId")

            if not record_id:
                continue

            save_processed_job(
                record_id,
                customer_id,
                "baseline"
            )

            count += 1

        mark_baseline_complete()

        return (
            f"Baseline complete. "
            f"{count} existing completed jobs were remembered. "
            f"No customer texts were sent."
        )

    sent_count = 0
    skipped_count = 0

    for job in completed_jobs:
        record_id = job.get("RecordID")
        customer_id = job.get("CustomerId")

        if not record_id or not customer_id:
            continue

        if not claim_completed_job(
            record_id,
            customer_id
        ):
            skipped_count += 1
            continue

        customer = get_customer(customer_id)

        if not customer:
            save_processed_job(
                record_id,
                customer_id,
                "customer_not_found"
            )
            continue

        customer_name = customer.get(
            "CustomerName",
            "Customer"
        )

        customer_phone = customer.get("Phone")

        if not customer_phone:
            save_processed_job(
                record_id,
                customer_id,
                "no_phone"
            )
            continue

        message_body = (
            f"Hi {customer_name}, your pool service has been completed. "
            f"You can view your detailed service report here: "
            f"https://tampavippoolservices.poolbrain.com"
        )

        try:
            sid = send_sms(
                customer_phone,
                message_body,
                "completed_service"
            )
        except Exception as e:
            print(
                f"Completed service SMS failed for "
                f"record {record_id}: {e}"
            )
        
            save_processed_job(
                record_id,
                customer_id,
                "send_failed"
            )
        
            continue

        save_processed_job(
            record_id,
            customer_id,
            "text_sent",
            sid
        )
        
        queue_review_request(
            record_id,
            customer_id,
            customer_name,
            customer_phone
        )
        
        sent_count += 1

    return (
        f"Processing complete. "
        f"{sent_count} new service texts sent. "
        f"{skipped_count} jobs were already processed."
    )

@app.route("/review/<review_token>", methods=["GET"])
def open_google_review(review_token):
    review_url = os.environ.get("GOOGLE_REVIEW_URL")

    if not review_url:
        return "Google review link is unavailable.", 500

    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE review_requests
                SET
                    link_clicked_at = COALESCE(
                        link_clicked_at,
                        NOW()
                    ),
                    status = CASE
                        WHEN confirmed_review_at IS NOT NULL
                            THEN status
                        ELSE 'clicked'
                    END,
                    updated_at = NOW()
                WHERE review_token = %s
                RETURNING record_id
            """, (review_token,))

            tracked_request = cur.fetchone()

        conn.commit()

    if not tracked_request:
        return "This review link is invalid or expired.", 404

    return redirect(review_url, code=302)

@app.route("/review-queue-status", methods=["GET"])
def review_queue_status():
    provided_secret = request.headers.get("X-Process-Secret")
    expected_secret = os.environ.get("PROCESS_SECRET")

    if not expected_secret or provided_secret != expected_secret:
        return "Unauthorized", 401

    init_db()

    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT COUNT(*)
                FROM review_requests
                WHERE status = 'queued'
            """)
            queued_count = cur.fetchone()[0]

            cur.execute("""
                SELECT COUNT(*)
                FROM review_requests
                WHERE status = 'queued'
                AND scheduled_for <= NOW()
            """)
            due_count = cur.fetchone()[0]

            cur.execute("""
                SELECT COUNT(*)
                FROM review_requests
                WHERE status = 'first_sent'
            """)
            sent_count = cur.fetchone()[0]

            cur.execute("""
                SELECT COUNT(*)
                FROM review_requests
                WHERE status = 'send_failed'
            """)
            failed_count = cur.fetchone()[0]

    return jsonify({
        "queued": queued_count,
        "due_now": due_count,
        "first_messages_sent": sent_count,
        "send_failed": failed_count
    })

@app.route("/process-review-requests", methods=["GET"])
def process_review_requests():
    provided_secret = request.headers.get("X-Process-Secret")
    expected_secret = os.environ.get("PROCESS_SECRET")

    if not expected_secret or provided_secret != expected_secret:
        return "Unauthorized", 401

    now = datetime.now(ZoneInfo("America/New_York"))

    if now.hour < 9 or now.hour >= 19:
        return "Outside review-message hours.", 200

    review_url = os.environ.get("GOOGLE_REVIEW_URL")

    if not review_url:
        return "GOOGLE_REVIEW_URL is missing.", 500

    public_base_url = os.environ.get("PUBLIC_BASE_URL")

    if not public_base_url:
        return "PUBLIC_BASE_URL is missing.", 500
    
    public_base_url = public_base_url.rstrip("/")

    init_db()

    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
               WITH due AS (
                    SELECT record_id
                    FROM review_requests
                    WHERE status = 'queued'
                    AND scheduled_for <= NOW()
                    ORDER BY scheduled_for ASC
                    LIMIT 50
                    FOR UPDATE SKIP LOCKED
                )
                UPDATE review_requests AS review
                SET
                    status = 'sending',
                    updated_at = NOW()
                FROM due
                WHERE review.record_id = due.record_id
                RETURNING
                    review.record_id,
                    review.customer_id,
                    review.customer_phone,
                    review.review_token
            """)

            due_requests = cur.fetchall()
            conn.commit()

    sent_count = 0
    failed_count = 0

    for (
        record_id,
        customer_id,
        customer_phone,
        review_token
    ) in due_requests:
        if not review_token:
            review_token = secrets.token_urlsafe(24)
        
            with get_db_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        UPDATE review_requests
                        SET
                            review_token = %s,
                            updated_at = NOW()
                        WHERE record_id = %s
                        AND review_token IS NULL
                    """, (
                        review_token,
                        record_id
                    ))
        
                conn.commit()
        
        tracked_review_url = (
            f"{public_base_url}/review/{review_token}"
        )
        if not customer_phone:
            with get_db_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        UPDATE review_requests
                        SET
                            status = 'cancelled',
                            cancelled_reason = 'no_phone',
                            updated_at = NOW()
                        WHERE record_id = %s
                        AND status = 'sending'
                    """, (record_id,))

                conn.commit()

            failed_count += 1
            continue

        message_body = (
            "Hi, thank you for choosing Tampa VIP Pool Services!\n\n"
            "We would appreciate your honest feedback on Google:\n\n"
            f"{tracked_review_url}\n\n"
            "If anything needs our attention, please reply to this message. "
            "Reply STOP to opt out."
        )

        try:
            sid = send_sms(
                customer_phone,
                message_body,
                "google_review",
                record_id
            )

            with get_db_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        UPDATE review_requests
                        SET
                            status = 'first_sent',
                            first_message_sid = %s,
                            first_sent_at = NOW(),
                            updated_at = NOW()
                        WHERE record_id = %s
                        AND status = 'sending'
                    """, (
                        sid,
                        record_id
                    ))

                conn.commit()

            sent_count += 1

            print(
                f"Google review SMS sent for record "
                f"{record_id}. Twilio SID: {sid}"
            )

        except Exception as e:
            print(
                f"Google review SMS failed for record "
                f"{record_id}: {e}"
            )

            with get_db_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        UPDATE review_requests
                        SET
                            status = 'send_failed',
                            cancelled_reason = %s,
                            updated_at = NOW()
                        WHERE record_id = %s
                        AND status = 'sending'
                    """, (
                        str(e)[:500],
                        record_id
                    ))

                conn.commit()

            failed_count += 1

    return (
        f"Review processing complete. "
        f"{sent_count} messages sent. "
        f"{failed_count} requests failed or were cancelled."
    )


@app.route("/test-review-sms", methods=["GET"])
def test_review_sms():
    provided_secret = request.headers.get("X-Process-Secret")
    expected_secret = os.environ.get("PROCESS_SECRET")

    if not expected_secret or provided_secret != expected_secret:
        return "Unauthorized", 401

    test_number = os.environ.get("TEST_PHONE_NUMBER")
    review_url = os.environ.get("GOOGLE_REVIEW_URL")

    if not test_number:
        return "TEST_PHONE_NUMBER is missing.", 500

    if not review_url:
        return "GOOGLE_REVIEW_URL is missing.", 500

    message_body = (
        "Hi, thank you for choosing Tampa VIP Pool Services!\n\n"
        "We would appreciate your honest feedback on Google:\n\n"
        f"{review_url}\n\n"
        "If anything needs our attention, please reply to this message. "
        "Reply STOP to opt out."
    )

    sid = send_sms(
        test_number,
        message_body,
        "google_review_test"
    )

    return f"Review test SMS sent! Message ID: {sid}"


@app.route("/test-sms", methods=["GET"])
def test_sms():
    provided_secret = request.headers.get(
        "X-Process-Secret"
    )
    expected_secret = os.environ.get(
        "PROCESS_SECRET"
    )

    if (
        not expected_secret
        or provided_secret != expected_secret
    ):
        return "Unauthorized", 401

    test_number = os.environ.get(
        "TEST_PHONE_NUMBER"
    )

    if not test_number:
        return "TEST_PHONE_NUMBER is missing.", 500

    sid = send_sms(
        test_number,
        "Test successful! PoolBrain SMS Automation "
        "is connected to Twilio.",
        "general_test"
    )

    return f"SMS sent! Message ID: {sid}"
