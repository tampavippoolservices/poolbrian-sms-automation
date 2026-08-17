from flask import Flask, request, jsonify
from twilio.rest import Client
from urllib.request import Request, urlopen
from urllib.parse import urlencode
from datetime import datetime
from zoneinfo import ZoneInfo
import json
import os

app = Flask(__name__)

POOLBRAIN_BASE_URL = "https://prodapi.poolbrain.com"


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

    with urlopen(req, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


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
            if isinstance(value, dict) and "CustomerName" in value:
                return value

    return None


@app.route("/", methods=["GET"])
def home():
    return "PoolBrain SMS Automation is running!"


@app.route("/webhook", methods=["POST"])
def poolbrain_webhook():
    data = request.get_json(silent=True) or {}

    print("PoolBrain webhook received:")
    print(data)

    event = data.get("event")

    if event == "customer.created":
        test_number = os.environ.get("TEST_PHONE_NUMBER")

        if test_number:
            sid = send_sms(
                test_number,
                "PoolBrain test successful! A customer-created webhook triggered this SMS automatically."
            )

            print("Automatic test SMS sent:")
            print(sid)

    return jsonify({
        "success": True,
        "message": "Webhook received"
    }), 200


@app.route("/test-sms", methods=["GET"])
def test_sms():
    test_number = os.environ.get("TEST_PHONE_NUMBER")

    sid = send_sms(
        test_number,
        "Test successful! PoolBrain SMS Automation is connected to Twilio."
    )

    return f"SMS sent! Message ID: {sid}"


@app.route("/test-completed-service", methods=["GET"])
def test_completed_service():
    test_number = os.environ.get("TEST_PHONE_NUMBER")

    if not test_number:
        return "TEST_PHONE_NUMBER is missing", 500

    today = datetime.now(
        ZoneInfo("America/New_York")
    ).strftime("%Y-%m-%d")

    jobs_result = poolbrain_get(
        "/v2/route_stops_job_list",
        {
            "fromDate": today,
            "toDate": today
        }
    )

    jobs = jobs_result.get("data", [])

    completed_jobs = [
        job for job in jobs
        if job.get("JobStatus") == "Completed"
    ]

    if not completed_jobs:
        return f"No completed PoolBrain jobs found for {today}."

    job = completed_jobs[0]

    customer_id = job.get("CustomerId")

    if not customer_id:
        return "Completed job does not contain CustomerId.", 500

    customer = get_customer(customer_id)

    if not customer:
        return "Customer could not be found in PoolBrain.", 500

    customer_name = customer.get("CustomerName", "Customer")
    customer_phone = customer.get("Phone")

    if not customer_phone:
        return "Customer was found, but no phone number is stored.", 500

    message_body = (
        f"TEST MODE: PoolBrain found a completed service for "
        f"{customer_name}. In production, the service-completed "
        f"SMS would now be sent to this customer."
    )

    sid = send_sms(test_number, message_body)

    return (
        f"Success! Completed service found. "
        f"Customer lookup worked. Test SMS sent to your test phone. "
        f"Message ID: {sid}"
    )


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
