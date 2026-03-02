from flask import Flask, request, jsonify
from flask_cors import CORS
import os, uuid, smtplib, stripe
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import psycopg2
from psycopg2.extras import RealDictCursor

app = Flask(__name__)
CORS(app, origins=["https://duskz.shop", "https://www.duskz.shop"])

# ─── STRIPE CONFIG ────────────────────────────────────────────────────────────
STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET")

if not STRIPE_SECRET_KEY or not STRIPE_WEBHOOK_SECRET:
    raise RuntimeError("Stripe env vars missing")

stripe.api_key = STRIPE_SECRET_KEY

# ─── DATABASE (POSTGRES) ──────────────────────────────────────────────────────
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

# ─── SMTP (OPTIONAL) ──────────────────────────────────────────────────────────
SMTP_HOST = os.environ.get("SMTP_HOST", "mail.duskz.shop")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASS = os.environ.get("SMTP_PASS", "")
FROM_EMAIL = f"Duskz <{SMTP_USER}>" if SMTP_USER else "Duskz <noreply@duskz.shop>"
SMTP_ENABLED = bool(SMTP_USER and SMTP_PASS)

if not SMTP_ENABLED:
    print("[INFO] Email disabled")

def send_key_email(to_email, name, product, key):
    if not SMTP_ENABLED:
        print(f"[EMAIL] skipped for {to_email}")
        return
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = "Your Duskz License Key"
        msg["From"] = FROM_EMAIL
        msg["To"] = to_email

        html = f"""
        <html><body style="font-family:Arial;background:#080808;color:#fff">
        <h2>DUSKZ</h2>
        <p>Hey {name},</p>
        <p>Your license key:</p>
        <pre style="background:#111;padding:12px;border-radius:6px">{key}</pre>
        </body></html>
        """
        msg.attach(MIMEText(html, "html"))

        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as s:
            s.starttls()
            s.login(SMTP_USER, SMTP_PASS)
            s.sendmail(FROM_EMAIL, to_email, msg.as_string())
    except Exception as e:
        print("[EMAIL ERROR]", e)

# ─── PRODUCTS ────────────────────────────────────────────────────────────────
PRODUCT_PRICES = {
    "standard": 799,
    "premium": 1099,
    "crosshair": 199,
}
ALLOWED_PRODUCTS = set(PRODUCT_PRICES.keys())

# ─── KEY ROUTES ───────────────────────────────────────────────────────────────
@app.route("/keys", methods=["GET"])
def get_keys():
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM keys ORDER BY created DESC")
            return jsonify(cur.fetchall())

@app.route("/keys", methods=["POST"])
def create_key():
    data = request.json or {}
    key = data.get("key")
    type_ = data.get("type", "standard")

    if not key or type_ not in ALLOWED_PRODUCTS:
        return jsonify({"error": "invalid"}), 400

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO keys (id, key, type, status, created)
                VALUES (%s,%s,%s,%s,%s)
                ON CONFLICT (key) DO NOTHING
            """, (uuid.uuid4(), key, type_, "unused", datetime.utcnow()))
        conn.commit()

    return jsonify({"success": True})

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

@app.route("/create-payment-intent", methods=["POST"])
def create_payment_intent():
    data = request.json
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
        name = pi["metadata"]["customer_name"]
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
                    return jsonify({"status": "ok"})

                cur.execute("""
                    UPDATE keys
                    SET status='sold', email=%s, sold_at=%s
                    WHERE id=%s
                """, (email, datetime.utcnow(), k["id"]))
            conn.commit()

        send_key_email(email, name, product, k["key"])

    return jsonify({"status": "ok"})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
