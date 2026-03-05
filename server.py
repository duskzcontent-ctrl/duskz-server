from flask import Flask, request, jsonify
from flask_cors import CORS
import os, uuid, stripe, smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
import psycopg2
from psycopg2.extras import RealDictCursor

app = Flask(__name__)
CORS(app)

DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL not set")

stripe.api_key = os.environ.get("STRIPE_SECRET_KEY")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET")

# ─────────────────────────────────────
# PRODUCT CATALOG
# ─────────────────────────────────────
PRODUCTS = {
    "standard":  {"name": "Duskz Standard", "amount": 799, "type": "standard"},
    "premium":   {"name": "Duskz Premium", "amount": 1099, "type": "premium"},
    "crosshair": {"name": "Custom Crosshair ZX", "amount": 199, "type": "crosshair"},
    "roblox":    {"name": "Roblox Alt Account", "amount": 299, "type": "roblox"},
}

# ─────────────────────────────────────
# DB
# ─────────────────────────────────────
def get_db():
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)

def init_db():
    with get_db() as conn:
        with conn.cursor() as cur:

            cur.execute("""
                CREATE TABLE IF NOT EXISTS keys (
                    id UUID PRIMARY KEY,
                    key TEXT UNIQUE NOT NULL,
                    type TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'unused',
                    hwid TEXT,
                    email TEXT,
                    buyer_name TEXT,
                    payment_intent_id TEXT,
                    created TIMESTAMP NOT NULL,
                    sold_at TIMESTAMP
                )
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS accounts (
                    id UUID PRIMARY KEY,
                    account TEXT NOT NULL,
                    type TEXT NOT NULL DEFAULT 'roblox',
                    claimed BOOLEAN NOT NULL DEFAULT FALSE,
                    status TEXT NOT NULL DEFAULT 'unclaimed',
                    buyer_email TEXT,
                    payment_intent_id TEXT,
                    created_at TIMESTAMP NOT NULL
                )
            """)

        conn.commit()

init_db()

# ─────────────────────────────────────
# CREATE PAYMENT INTENT
# ─────────────────────────────────────
@app.route("/create-payment-intent", methods=["POST"])
def create_payment_intent():

    data = request.json or {}
    product = data.get("product", "standard")
    email = data.get("email", "").strip()
    name = data.get("name", "").strip()

    if product not in PRODUCTS:
        return jsonify({"error": "Invalid product"}), 400

    if not email or "@" not in email:
        return jsonify({"error": "Valid email required"}), 400

    p = PRODUCTS[product]

    try:
        intent = stripe.PaymentIntent.create(
            amount=p["amount"],
            currency="usd",
            receipt_email=email,
            metadata={
                "product": product,
                "key_type": p["type"],
                "email": email,
                "name": name,
            },
            automatic_payment_methods={"enabled": True},
        )

        return jsonify(clientSecret=intent.client_secret)

    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ─────────────────────────────────────
# STRIPE WEBHOOK
# ─────────────────────────────────────
@app.route("/webhook", methods=["POST"])
def stripe_webhook():

    payload = request.get_data()
    sig_header = request.headers.get("Stripe-Signature")

    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, STRIPE_WEBHOOK_SECRET
        )
    except Exception:
        return jsonify({"error": "Webhook verification failed"}), 400

    if event["type"] == "payment_intent.succeeded":

        pi = event["data"]["object"]
        meta = pi.get("metadata", {})

        email = meta.get("email")
        name = meta.get("name", "Customer")
        product_slug = meta.get("product")

        if product_slug == "roblox":
            # Roblox account delivery logic would run here
            pass

    return jsonify({"received": True})


# ─────────────────────────────────────
# VALIDATE KEY
# ─────────────────────────────────────
@app.route("/validate", methods=["POST"])
def validate_key():

    data = request.json or {}
    key = data.get("key")
    hwid = data.get("hwid")

    if not key or not hwid:
        return jsonify({"valid": False})

    with get_db() as conn:
        with conn.cursor() as cur:

            cur.execute("SELECT * FROM keys WHERE key=%s", (key,))
            record = cur.fetchone()

    if not record:
        return jsonify({"valid": False})

    if record["hwid"] is None:

        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE keys SET hwid=%s, status='active' WHERE key=%s",
                    (hwid, key),
                )
            conn.commit()

        return jsonify({"valid": True})

    if record["hwid"] != hwid:
        return jsonify({"valid": False})

    return jsonify({"valid": True})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
