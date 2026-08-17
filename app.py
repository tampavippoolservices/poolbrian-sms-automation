from flask import Flask, request, jsonify
from twilio.rest import Client
import os

app = Flask(__name__)


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


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
