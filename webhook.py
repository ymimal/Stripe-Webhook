import os
import stripe
import datetime
from flask import Flask, request, jsonify
from pymongo import MongoClient

app = Flask(__name__)

STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "")
MONGO_DB = os.getenv("MONGO_DB", "")

stripe.api_key = STRIPE_SECRET_KEY

# MongoDB Connection
mongo_client = None
premium_db = None

if MONGO_DB:
    try:
        mongo_client = MongoClient(MONGO_DB)
        premium_db = mongo_client.premium.premium_db
        print("✅ MongoDB connected successfully")
    except Exception as e:
        print(f"❌ MongoDB Connection Failed: {e}")
else:
    print("⚠️ MONGO_DB environment variable is not set")


@app.route("/")
def home():
    return "Stripe Webhook is Running ✅"


@app.route("/webhook/stripe", methods=["POST"])
def stripe_webhook():
    payload = request.get_data(as_text=True)
    sig_header = request.headers.get("Stripe-Signature")

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, STRIPE_WEBHOOK_SECRET)
    except ValueError:
        return jsonify({"error": "Invalid payload"}), 400
    except stripe.error.SignatureVerificationError:
        return jsonify({"error": "Invalid signature"}), 400

    if event["type"] == "checkout.session.completed":
        session = event["data"]["object"]
        handle_payment_success(session)

    elif event["type"] == "invoice.payment_succeeded":
        invoice = event["data"]["object"]
        handle_renewal(invoice)

    return jsonify({"status": "success"}), 200


def handle_payment_success(session):
    if premium_db is None:
        print("⚠️ MongoDB not connected. Skipping payment save.")
        return
    try:
        metadata = getattr(session, "metadata", {}) or {}
        user_id = int(metadata.get("user_id"))
        plan = metadata.get("plan", "monthly")
        payment_type = metadata.get("type", "subscription")

        days = 36500 if payment_type == "lifetime" else 365
        expire_date = datetime.datetime.utcnow() + datetime.timedelta(days=days)

        premium_db.update_one(
            {"_id": user_id},
            {"$set": {
                "expire_date": expire_date,
                "plan": plan,
                "payment_type": payment_type,
                "stripe_customer_id": getattr(session, "customer", None)
            }},
            upsert=True
        )
        print(f"✅ Payment Success → User: {user_id} | Plan: {plan}")
    except Exception as e:
        print(f"Error in handle_payment_success: {e}")


def handle_renewal(invoice):
    if premium_db is None:
        return
    try:
        subscription_id = getattr(invoice, "subscription", None)
        if not subscription_id:
            return

        subscription = stripe.Subscription.retrieve(subscription_id)
        customer_id = getattr(subscription, "customer", None)

        user = premium_db.find_one({"stripe_customer_id": customer_id})
        if user:
            new_expiry = user.get("expire_date", datetime.datetime.utcnow()) + datetime.timedelta(days=30)
            premium_db.update_one({"_id": user["_id"]}, {"$set": {"expire_date": new_expiry}})
            print(f"🔄 Subscription renewed for user {user['_id']}")
    except Exception as e:
        print(f"Error in handle_renewal: {e}")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port)
