from flask import Flask, request, jsonify
from twilio.rest import Client
import os

app = Flask(__name__)


@app.route("/", methods=["GET"])
def home():
    return "PoolBrain SMS Automation is running!"


@app.route("/webhook", methods=["POST"])
def poolbrain_webhook():
    data = request.get_json(silent=True)

    print("PoolBrain webhook received:")
    print(data)

    return jsonify({
        "success": True,
        "message": "Webhook received"
    }), 200


@app.route("/test-sms", methods=["GET"])
def test_sms():
    account_sid = os.environ.get("TWILIO_ACCOUNT_SID")
    auth_token = os.environ.get("TWILIO_AUTH_TOKEN")
    twilio_number = os.environ.get("TWILIO_PHONE_NUMBER")
    test_number = os.environ.get("TEST_PHONE_NUMBER")

    if not all([account_sid, auth_token, twilio_number, test_number]):
        return "Missing Twilio settings", 500

    client = Client(account_sid, auth_token)

    message = client.messages.create(
        body="Test successful! PoolBrain SMS Automation is connected to Twilio.",
        from_=twilio_number,
        to=test_number
    )

    return f"SMS sent! Message ID: {message.sid}"


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
