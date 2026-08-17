from flask import Flask, request, jsonify
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


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
