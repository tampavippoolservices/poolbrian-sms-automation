from flask import Flask, request, jsonify
from twilio.rest import Client
from urllib.request import Request, urlopen
from urllib.parse import urlencode
from datetime import datetime
from zoneinfo import ZoneInfo
import psycopg
import json
import os

app = Flask(__name__)

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
                CREATE TABLE IF NOT EXISTS automation_state (
                    key TEXT PRIMARY KEY,
                    value TEXT
                )
            """)

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


def send_sms(to_number, message_body):
    account_sid = os.environ.get("TWILIO_ACCOUNT_SID")
    auth_token = os.environ.get("TWILIO_AUTH_TOKEN")
    twilio_number = os.environ.get("TWILIO_PHONE_NUMBER")

    client = Client(account_sid, auth_token)

    message = client.messages.create(
        body=message_body,
        from_=twilio_number,
        to=to_number
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
            customer_id = job.get("CustomerId")
            job_id = job.get("JobID")

            alert_categories = job.get("AlertCategories", [])

            for category in alert_categories:
                custom_alerts = category.get("CustomAlert", [])

                for alert in custom_alerts:
                    alert_name = (
                        alert.get("AlertName")
                        or alert.get("type")
                        or ""
                    )

                    print(f"Alert detected: {alert_name}")

                    alert_text = alert_name.lower()

                    if (
                        "water" in alert_text
                        and "level" in alert_text
                        and "low" in alert_text
                    ):
                        print("WATER LEVEL LOW DETECTED")
                        print(f"CustomerId: {customer_id}")
                        print(f"JobID: {job_id}")
                        print(f"AlertId: {alert.get('alertId')}")

    return jsonify({
        "success": True,
        "message": "Webhook received"
    }), 200


@app.route("/process-completed-services", methods=["GET"])
def process_completed_services():
    provided_secret = request.args.get("secret")
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
            message_body
        )

        save_processed_job(
            record_id,
            customer_id,
            "text_sent",
            sid
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
