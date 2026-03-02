from flask import Flask, request, jsonify
from flask_cors import CORS
import os, uuid, smtplib, stripe
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import psycopg2
from psycopg2.extras import RealDictCursor

app = Flask(__name__)

# ✅ Allow Netlify + domain
CORS(app, origins=[
    "https://tranquil-crostata-149f15.netlify.app",
    "https://duskz.shop",
    "https://www.duskz.shop"
])

# ─────────────────────────────────────
# STRIPE
# ─────────────────────────────────────
STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET")

if not STRIPE_SECRET_KEY or not STRIPE_WEBHOOK_SECRET:
    raise RuntimeError("Stripe env vars missing")

stripe.api_key = STRIPE_SECRET_KEY

# ─────────────────────────────────────
# DATABASE
# ─────────────────────────────────────
DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL not set")

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
                    status TEXT NOT NULL,
                    hwid TEXT,
                    email TEXT,
                    created TIMESTAMP,
                    sold_at TIMESTAMP
                )
            """)
        conn.commit()

init_db()

# ─────────────────────────────────────
# PRODUCTS
# ─────────────────────────────────────
PRODUCT_PRICES = {
    "standard": 799,
    "premium": 1099,
    "crosshair": 199,
}
ALLOWED_PRODUCTS = set(PRODUCT_PRICES.keys())

# ─────────────────────────────────────
# HEALTH
# ─────────────────────────────────────
@app.route("/")
def home():
    return {"status": "ok"}

# ─────────────────────────────────────
# BULK KEY GENERATOR (FIXED)
# ─────────────────────────────────────
@app.route("/keys/generate", methods=["POST"])
def generate_keys():
    data = request.json or {}
    amount = int(data.get("amount", 1))
    type_ = data.get("type", "standard")

    if type_ not in ALLOWED_PRODUCTS:
        return jsonify({"error": "invalid type"}), 400

    generated = []

    with get_db() as conn:
        with conn.cursor() as cur:
            for _ in range(amount):
                new_key = str(uuid.uuid4()).upper()
                cur.execute("""
                    INSERT INTO keys (id, key, type, status, created)
                    VALUES (%s,%s,%s,%s,%s)
                """, (
                    uuid.uuid4(),
                    new_key,
                    type_,
                    "unused",
                    datetime.utcnow()
                ))
                generated.append(new_key)
        conn.commit()

    return jsonify({
        "success": True,
        "generated": generated,
        "count": len(generated)
    })

# ─────────────────────────────────────
# LIST KEYS
# ─────────────────────────────────────
@app.route("/keys", methods=["GET"])
def list_keys():
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM keys ORDER BY created DESC")
            return jsonify(cur.fetchall())

# ─────────────────────────────────────
# VALIDATE KEY
# ─────────────────────────────────────
@app.route("/validate", methods=["POST"])
def validate():
    data = request.json or {}
    key = data.get("key")
    hwid = data.get("hwid")

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM keys WHERE key=%s", (key,))
            k = cur.fetchone()

            if not k or k["status"] == "banned":
                return jsonify({"valid": False})

            if k["hwid"] and k["hwid"] != hwid:
                return jsonify({"valid": False})

            if not k["hwid"]:
                cur.execute(
                    "UPDATE keys SET hwid=%s, status='used' WHERE key=%s",
                    (hwid, key)
                )
                conn.commit()

            return jsonify({"valid": True, "type": k["type"]})

# ─────────────────────────────────────
# STRIPE PAYMENT INTENT
# ─────────────────────────────────────
@app.route("/create-payment-intent", methods=["POST"])
def create_payment_intent():
    data = request.json or {}
    product = data.get("product")
    email = data.get("email")
    name = data.get("name", "Customer")

    if product not in ALLOWED_PRODUCTS:
        return jsonify({"error": "invalid product"}), 400

    intent = stripe.PaymentIntent.create(
        amount=PRODUCT_PRICES[product],
        currency="usd",
        receipt_email=email,
        metadata={
            "product": product,
            "customer_name": name,
            "customer_email": email
        }
    )

    return jsonify({"clientSecret": intent.client_secret})

# ─────────────────────────────────────
# STRIPE WEBHOOK
# ─────────────────────────────────────
@app.route("/stripe-webhook", methods=["POST"])
def stripe_webhook():
    payload = request.data
    sig = request.headers.get("Stripe-Signature")

    event = stripe.Webhook.construct_event(
        payload, sig, STRIPE_WEBHOOK_SECRET
    )

    if event["type"] == "payment_intent.succeeded":
        pi = event["data"]["object"]
        email = pi["metadata"]["customer_email"]
        product = pi["metadata"]["product"]

        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT * FROM keys
                    WHERE type=%s AND status='unused'
                    LIMIT 1
                """, (product,))
                k = cur.fetchone()

                if not k:
                    return jsonify({"status": "no_keys_available"})

                cur.execute("""
                    UPDATE keys
                    SET status='sold', email=%s, sold_at=%s
                    WHERE id=%s
                """, (email, datetime.utcnow(), k["id"]))
            conn.commit()

    return jsonify({"status": "ok"})

# ─────────────────────────────────────
# START
# ─────────────────────────────────────
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
