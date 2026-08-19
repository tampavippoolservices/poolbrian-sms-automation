from flask import Flask, request, jsonify, render_template_string, Response
from twilio.rest import Client
from urllib.request import Request, urlopen
from urllib.parse import urlencode
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import psycopg
import json
import os

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

POOLBRAIN_BASE_URL = "https://prodapi.poolbrain.com"


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

        conn.commit()


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
    customer_phone
):
    scheduled_for = (
        datetime.now(ZoneInfo("America/New_York"))
        + timedelta(hours=3)
    )

    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT 1
                FROM review_requests
                WHERE customer_id = %s
                AND status IN (
                    'queued',
                    'first_sent',
                    'reminder_sent',
                    'completed'
                )
                AND created_at >= NOW() - INTERVAL '120 days'
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
                    customer_phone,
                    status,
                    scheduled_for
                )
                VALUES (%s, %s, %s, 'queued', %s)
                ON CONFLICT (record_id) DO NOTHING
            """, (
                record_id,
                customer_id,
                customer_phone,
                scheduled_for
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


def send_sms(to_number, message_body, message_type="general"):
    account_sid = os.environ.get("TWILIO_ACCOUNT_SID")
    auth_token = os.environ.get("TWILIO_AUTH_TOKEN")
    twilio_number = os.environ.get("TWILIO_PHONE_NUMBER")
    status_callback = os.environ.get("TWILIO_STATUS_CALLBACK_URL")

    client = Client(account_sid, auth_token)

    callback_url = status_callback

    if status_callback:
        separator = "&" if "?" in status_callback else "?"
        callback_url = (
            status_callback
            + separator
            + urlencode({"message_type": message_type})
        )

    message = client.messages.create(
        body=message_body,
        from_=twilio_number,
        to=to_number,
        status_callback=callback_url
    )

    return message.sid


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


def get_completed_jobs_today():
    today = datetime.now(
        ZoneInfo("America/New_York")
    ).strftime("%Y-%m-%d")

    result = poolbrain_get(
        "/v2/route_stops_job_list",
        {
            "fromDate": today,
            "toDate": today
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
@app.route("/twilio-status", methods=["POST"])
def twilio_status():
    message_sid = request.form.get("MessageSid")
    message_status = request.form.get("MessageStatus")
    error_code = request.form.get("ErrorCode")
    destination_number = request.form.get("To")
    message_type = request.args.get("message_type", "general")

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
                WHERE updated_at::date =
                    (NOW() AT TIME ZONE 'America/New_York')::date
            """)
            total_today = cur.fetchone()[0]

            cur.execute("""
                SELECT COUNT(*)
                FROM sms_delivery_status
                WHERE message_status = 'delivered'
                AND updated_at::date =
                    (NOW() AT TIME ZONE 'America/New_York')::date
            """)
            delivered_today = cur.fetchone()[0]

            cur.execute("""
                SELECT COUNT(*)
                FROM sms_delivery_status
                WHERE message_status IN ('failed', 'undelivered')
                AND updated_at::date =
                    (NOW() AT TIME ZONE 'America/New_York')::date
            """)
            failed_today = cur.fetchone()[0]

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
        </body>
        </html>
    """,
        total_today=total_today,
        delivered_today=delivered_today,
        failed_today=failed_today,
        recent_rows=recent_rows,
        inbound_rows=inbound_rows
    )


@app.route("/", methods=["GET"])
def home():
    return "PoolBrain SMS Automation is running!"


@app.route("/webhook", methods=["POST"])
def poolbrain_webhook():
    data = request.get_json(silent=True) or {}

    print("PoolBrain webhook received:")
    print(data)

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
                        alert_id = alert.get("alertId")

                        print("WATER LEVEL LOW DETECTED")
                        print(f"CustomerId: {customer_id}")
                        print(f"JobID: {job_id}")
                        print(f"AlertId: {alert_id}")

                        init_db()

                        if alert_already_processed(alert_id):
                            print(f"Water Level Low alert {alert_id} already processed")
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
                            print(f"No phone number for customer {customer_id}")
                            continue

                        message_body = (
                            f"Hi {customer_name}, your pool technician noticed that "
                            f"your pool water level is low. Please add water to the "
                            f"normal operating level when convenient. "
                            f"Tampa VIP Pool Services"
                        )

                        sid = send_sms(
                            customer_phone,
                            message_body,
                            "water_level_low"
                        )

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

    completed_jobs = get_completed_jobs_today()

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

        if job_already_processed(record_id):
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

        sid = send_sms(
    customer_phone,
    message_body,
    "completed_service"
        )

        save_processed_job(
            record_id,
            customer_id,
            "text_sent",
            sid
        )
        
        queue_review_request(
            record_id,
            customer_id,
            customer_phone
        )
        
        sent_count += 1

    return (
        f"Processing complete. "
        f"{sent_count} new service texts sent. "
        f"{skipped_count} jobs were already processed."
    )


@app.route("/test-sms", methods=["GET"])
def test_sms():
    test_number = os.environ.get(
        "TEST_PHONE_NUMBER"
    )

    sid = send_sms(
        test_number,
        "Test successful! PoolBrain SMS Automation "
        "is connected to Twilio."
    )

    return f"SMS sent! Message ID: {sid}"


if __name__ == "__main__":
    init_db()

    port = int(
        os.environ.get("PORT", 5000)
    )

    app.run(
        host="0.0.0.0",
        port=port
    )
