from flask import Flask, request, jsonify
from flask_cors import CORS
import json, os, uuid, smtplib, stripe
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

app = Flask(__name__)

CORS(app, origins=["https://duskz.shop", "https://www.duskz.shop"])

# ─── CONFIG ───────────────────────────────────────────────────────────────────
# STRIPE — required
STRIPE_SECRET_KEY     = os.environ.get("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")

if not STRIPE_SECRET_KEY:
    raise RuntimeError("STRIPE_SECRET_KEY env var is not set. Add it in Railway → Variables.")
if not STRIPE_WEBHOOK_SECRET:
    raise RuntimeError("STRIPE_WEBHOOK_SECRET env var is not set. Add it in Railway → Variables.")

stripe.api_key = STRIPE_SECRET_KEY

# SMTP — optional
SMTP_HOST    = os.environ.get("SMTP_HOST", "mail.duskz.shop")
SMTP_PORT    = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER    = os.environ.get("SMTP_USER", "")
SMTP_PASS    = os.environ.get("SMTP_PASS", "")
FROM_EMAIL   = f"Duskz <{SMTP_USER}>" if SMTP_USER else "Duskz <noreply@duskz.shop>"
SMTP_ENABLED = bool(SMTP_USER and SMTP_PASS)

if not SMTP_ENABLED:
    print("[INFO] Email disabled")

# ─── SERVER-SIDE PRICE TABLE ───────────────────────────────────────────────────
PRODUCT_PRICES = {
    "standard":  799,
    "premium":   1099,
    "crosshair": 199,
}
ALLOWED_PRODUCTS = set(PRODUCT_PRICES.keys())

# ✅ FIXED: persistent storage on Railway
DB_FILE = "/data/keys.json"

# ─── DB ───────────────────────────────────────────────────────────────────────
def load_keys():
    if not os.path.exists(DB_FILE):
        return []
    with open(DB_FILE, "r") as f:
        return json.load(f)

def save_keys(keys):
    with open(DB_FILE, "w") as f:
        json.dump(keys, f, indent=2)

# ─── EMAIL ────────────────────────────────────────────────────────────────────
def send_key_email(to_email, customer_name, product_type, key_str):
    if not SMTP_ENABLED:
        print(f"[EMAIL] SKIPPED — key {key_str} sold to {to_email}")
        return False
    try:
        product_name = "Duskz Premium" if product_type == "premium" else "Duskz Standard"
        safe_name    = str(customer_name)[:100].replace("<", "&lt;").replace(">", "&gt;")
        safe_key     = str(key_str).replace("<", "&lt;").replace(">", "&gt;")

        msg            = MIMEMultipart("alternative")
        msg["Subject"] = f"Your {product_name} License Key - Duskz"
        msg["From"]    = FROM_EMAIL
        msg["To"]      = to_email

        html = f"""
        <html>
        <body style="background:#080808;color:#f0f0f0;font-family:Arial">
          <h2>DUSKZ</h2>
          <p>Hey {safe_name},</p>
          <p>Your license key:</p>
          <pre style="background:#111;padding:15px;border-radius:8px">{safe_key}</pre>
          <p>Join Discord for download.</p>
        </body>
        </html>
        """

        msg.attach(MIMEText(html, "html"))

        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.starttls()
            server.login(SMTP_USER, SMTP_PASS)
            server.sendmail(FROM_EMAIL, to_email, msg.as_string())

        print(f"[EMAIL] Sent to {to_email}")
        return True
    except Exception as e:
        print(f"[EMAIL ERROR] {e}")
        return False

# ─── KEY ROUTES ───────────────────────────────────────────────────────────────
@app.route("/keys", methods=["GET"])
def get_keys():
    return jsonify(load_keys())

@app.route("/keys", methods=["POST"])
def create_key():
    data     = request.json or {}
    key_str  = str(data.get("key", "")).strip()
    key_type = data.get("type", "standard")

    if not key_str:
        return jsonify({"error": "No key provided"}), 400
    if key_type not in ALLOWED_PRODUCTS:
        return jsonify({"error": "Invalid product type"}), 400

    keys = load_keys()
    if any(k["key"] == key_str for k in keys):
        return jsonify({"error": "Key already exists"}), 409

    new_key = {
        "id": str(uuid.uuid4()),
        "key": key_str,
        "type": key_type,
        "status": "unused",
        "hwid": None,
        "email": None,
        "created": datetime.utcnow().isoformat()
    }

    keys.insert(0, new_key)
    save_keys(keys)
    return jsonify(new_key), 201

# ─── VALIDATE ─────────────────────────────────────────────────────────────────
@app.route("/validate", methods=["POST"])
def validate_key():
    data    = request.json or {}
    key_str = str(data.get("key", "")).strip()
    hwid    = str(data.get("hwid", "")).strip()

    if not key_str or not hwid:
        return jsonify({"valid": False}), 400

    keys = load_keys()
    for k in keys:
        if k["key"] == key_str:
            if k["status"] == "banned":
                return jsonify({"valid": False})
            if k["hwid"] and k["hwid"] != hwid:
                return jsonify({"valid": False})
            if not k["hwid"]:
                k["hwid"] = hwid
                k["status"] = "used"
                save_keys(keys)
            return jsonify({"valid": True, "type": k["type"]})

    return jsonify({"valid": False})

# ─── STRIPE CREATE INTENT ─────────────────────────────────────────────────────
@app.route("/create-payment-intent", methods=["POST"])
def create_payment_intent():
    data    = request.json or {}
    product = data.get("product")
    email   = data.get("email")
    name    = data.get("name", "Customer")

    if product not in ALLOWED_PRODUCTS:
        return jsonify({"error": "Invalid product"}), 400

    intent = stripe.PaymentIntent.create(
        amount=PRODUCT_PRICES[product],
        currency="usd",
        receipt_email=email,
        metadata={"product": product, "customer_name": name}
    )

    return jsonify({"clientSecret": intent.client_secret})

# ─── STRIPE WEBHOOK ───────────────────────────────────────────────────────────
@app.route("/stripe-webhook", methods=["POST"])
def stripe_webhook():
    payload = request.data
    sig = request.headers.get("Stripe-Signature", "")

    event = stripe.Webhook.construct_event(
        payload, sig, STRIPE_WEBHOOK_SECRET
    )

    if event["type"] == "payment_intent.succeeded":
        pi = event["data"]["object"]
        email = pi.get("receipt_email")
        product = pi["metadata"]["product"]

        keys = load_keys()
        for k in keys:
            if k["type"] == product and k["status"] == "unused":
                k["status"] = "sold"
                k["email"] = email
                save_keys(keys)
                send_key_email(email, pi["metadata"]["customer_name"], product, k["key"])
                break

    return jsonify({"status": "ok"})
