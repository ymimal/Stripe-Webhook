import os
import stripe
import datetime
from flask import Flask, request, jsonify
from pymongo import MongoClient

app = Flask(__name__)

# === CONFIGURATION ===
STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "")
MONGO_DB = os.getenv("MONGO_DB", "")

stripe.api_key = STRIPE_SECRET_KEY

# Connect to MongoDB
mongo_client = MongoClient(MONGO_DB)
premium_db = mongo_client.premium.premium_db


@app.route("/")
def home():
    return "Stripe Webhook is running ✅"


@app.route("/webhook/stripe", methods=["POST"])
def stripe_webhook():
    payload = request.get_data(as_text=True)
    sig_header = request.headers.get("Stripe-Signature")

    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, STRIPE_WEBHOOK_SECRET
        )
    except ValueError:
        return jsonify({"error": "Invalid payload"}), 400
    except stripe.error.SignatureVerificationError:
        return jsonify({"error": "Invalid signature"}), 400

    # Handle checkout completion (new subscription or lifetime)
    if event["type"] == "checkout.session.completed":
        session = event["data"]["object"]
        handle_payment_success(session)

    # Handle recurring subscription renewals
    elif event["type"] == "invoice.payment_succeeded":
        invoice = event["data"]["object"]
        if invoice.get("subscription"):
            handle_renewal(invoice)

    return jsonify({"status": "success"}), 200


def handle_payment_success(session):
    try:
        user_id = int(session["metadata"].get("user_id"))
        plan = session["metadata"].get("plan", "monthly")
        payment_type = session["metadata"].get("type", "subscription")

        # Lifetime = very long expiry, else 1 year (will be extended on renewal)
        days = 36500 if payment_type == "lifetime" else 365
        expire_date = datetime.datetime.utcnow() + datetime.timedelta(days=days)

        premium_db.update_one(
            {"_id": user_id},
            {"$set": {
                "expire_date": expire_date,
                "plan": plan,
                "payment_type": payment_type,
                "stripe_customer_id": session.get("customer")
            }},
            upsert=True
        )
        print(f"✅ Payment Success: User {user_id} | Plan: {plan}")
    except Exception as e:
        print(f"Error handling payment: {e}")


def handle_renewal(invoice):
    try:
        subscription = stripe.Subscription.retrieve(invoice["subscription"])
        customer_id = subscription.get("customer")

        user = premium_db.find_one({"stripe_customer_id": customer_id})
        if user:
            new_expiry = user.get("expire_date", datetime.datetime.utcnow()) + datetime.timedelta(days=30)
            premium_db.update_one(
                {"_id": user["_id"]},
                {"$set": {"expire_date": new_expiry}}
            )
            print(f"🔄 Renewed for user {user['_id']}")
    except Exception as e:
        print(f"Renewal error: {e}")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port)
