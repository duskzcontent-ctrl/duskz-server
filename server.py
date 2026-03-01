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

# SMTP — optional, emails disabled if not configured
SMTP_HOST    = os.environ.get("SMTP_HOST", "mail.duskz.shop")
SMTP_PORT    = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER    = os.environ.get("SMTP_USER", "")
SMTP_PASS    = os.environ.get("SMTP_PASS", "")
FROM_EMAIL   = f"Duskz <{SMTP_USER}>" if SMTP_USER else "Duskz <noreply@duskz.shop>"
SMTP_ENABLED = bool(SMTP_USER and SMTP_PASS)

if not SMTP_ENABLED:
    print("[WARNING] SMTP_USER/SMTP_PASS not set — email delivery disabled until configured")

# ─── SERVER-SIDE PRICE TABLE — client never sets price ────────────────────────
PRODUCT_PRICES = {
    "standard":  799,
    "premium":   1099,
    "crosshair": 199,
}
ALLOWED_PRODUCTS = set(PRODUCT_PRICES.keys())

DB_FILE = "keys.json"

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
        print(f"[EMAIL] SKIPPED (SMTP not configured) — key {key_str} sold to {to_email}")
        return False
    try:
        product_name = "Duskz Premium" if product_type == "premium" else "Duskz Standard"
        safe_name    = str(customer_name)[:100].replace("<", "&lt;").replace(">", "&gt;")
        safe_key     = str(key_str).replace("<", "&lt;").replace(">", "&gt;")
        safe_product = product_name.replace("<", "&lt;").replace(">", "&gt;")

        msg            = MIMEMultipart("alternative")
        msg["Subject"] = f"Your {safe_product} License Key - Duskz"
        msg["From"]    = FROM_EMAIL
        msg["To"]      = to_email

        html = f"""<!DOCTYPE html>
<html>
<head>
<style>
  body {{ font-family: Arial, sans-serif; background: #080808; color: #f0f0f0; margin: 0; padding: 0; }}
  .container {{ max-width: 600px; margin: 0 auto; }}
  .header {{ background: #111; padding: 40px; text-align: center; border-bottom: 1px solid rgba(255,255,255,0.1); }}
  .header h1 {{ color: #fff; font-size: 26px; margin: 0; letter-spacing: 3px; }}
  .body {{ background: #111; padding: 40px; }}
  .key-box {{ background: #080808; border: 1px solid rgba(255,255,255,0.2); border-radius: 12px; padding: 24px; text-align: center; margin: 24px 0; }}
  .key-label {{ color: #666; font-size: 12px; margin-bottom: 10px; letter-spacing: 2px; text-transform: uppercase; }}
  .key-value {{ color: #fff; font-size: 18px; font-weight: bold; letter-spacing: 2px; word-break: break-all; }}
  .steps {{ background: #161616; border-radius: 12px; padding: 24px; margin: 24px 0; }}
  .steps h3 {{ color: #fff; margin-top: 0; }}
  .step {{ padding: 8px 0; border-bottom: 1px solid rgba(255,255,255,0.05); color: #888; }}
  .step:last-child {{ border-bottom: none; }}
  .btn {{ display: inline-block; background: #fff; color: #000; padding: 14px 32px; border-radius: 8px; text-decoration: none; font-weight: bold; margin: 10px 5px; }}
  .footer {{ background: #080808; padding: 24px; text-align: center; color: #555; font-size: 13px; }}
</style>
</head>
<body>
<div class="container">
  <div class="header"><h1>DUSKZ</h1></div>
  <div class="body">
    <p>Hey {safe_name},</p>
    <p>Thank you for purchasing <strong>{safe_product}</strong>. Here is your license key:</p>
    <div class="key-box">
      <div class="key-label">Your License Key</div>
      <div class="key-value">{safe_key}</div>
    </div>
    <div class="steps">
      <h3>Next Steps</h3>
      <div class="step">1. Download Duskz from our Discord</div>
      <div class="step">2. Run Duskz.exe as Administrator</div>
      <div class="step">3. Enter your license key above</div>
      <div class="step">4. Enjoy!</div>
    </div>
    <div style="text-align:center;margin-top:30px;">
      <a href="https://discord.gg/u85uVGhMBF" class="btn">Join Discord</a>
    </div>
    <p style="color:#555;font-size:12px;margin-top:30px;">Keep this email safe — your license key is tied to your HWID.</p>
  </div>
  <div class="footer">
    <p>Duskz &mdash; Premium Roblox External</p>
    <p>Questions? Join our <a href="https://discord.gg/u85uVGhMBF" style="color:#aaa;">Discord</a></p>
  </div>
</div>
</body>
</html>"""

        msg.attach(MIMEText(html, "html"))
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.starttls()
            server.login(SMTP_USER, SMTP_PASS)
            server.sendmail(FROM_EMAIL, to_email, msg.as_string())

        print(f"[EMAIL] Key sent to {to_email}")
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
        "id":      str(uuid.uuid4()),
        "key":     key_str,
        "type":    key_type,
        "status":  "unused",
        "hwid":    None,
        "email":   None,
        "created": datetime.utcnow().isoformat()
    }
    keys.insert(0, new_key)
    save_keys(keys)
    return jsonify(new_key), 201

@app.route("/keys/<key_str>", methods=["DELETE"])
def delete_key(key_str):
    keys     = load_keys()
    new_keys = [k for k in keys if k["key"] != key_str]
    if len(new_keys) == len(keys):
        return jsonify({"error": "Key not found"}), 404
    save_keys(new_keys)
    return jsonify({"success": True})

@app.route("/keys/<key_str>/ban", methods=["POST"])
def ban_key(key_str):
    keys = load_keys()
    for k in keys:
        if k["key"] == key_str:
            k["status"] = "banned"
            save_keys(keys)
            return jsonify(k)
    return jsonify({"error": "Key not found"}), 404

@app.route("/keys/<key_str>/unban", methods=["POST"])
def unban_key(key_str):
    keys = load_keys()
    for k in keys:
        if k["key"] == key_str:
            k["status"] = "used" if k["hwid"] else "unused"
            save_keys(keys)
            return jsonify(k)
    return jsonify({"error": "Key not found"}), 404

@app.route("/keys/<key_str>/reset-hwid", methods=["POST"])
def reset_hwid(key_str):
    keys = load_keys()
    for k in keys:
        if k["key"] == key_str:
            if k["status"] == "banned":
                return jsonify({"error": "Cannot reset banned key"}), 400
            k["hwid"]   = None
            k["status"] = "unused"
            save_keys(keys)
            return jsonify(k)
    return jsonify({"error": "Key not found"}), 404

# ─── VALIDATE (C++ loader) ────────────────────────────────────────────────────
@app.route("/validate", methods=["POST"])
def validate_key():
    data    = request.json or {}
    key_str = str(data.get("key", "")).strip()
    hwid    = str(data.get("hwid", "")).strip()
    if not key_str or not hwid:
        return jsonify({"valid": False, "reason": "Missing key or hwid"}), 400
    keys = load_keys()
    for k in keys:
        if k["key"] == key_str:
            if k["status"] == "banned":
                return jsonify({"valid": False, "reason": "Key is banned"})
            if k["hwid"] and k["hwid"] != hwid:
                return jsonify({"valid": False, "reason": "HWID mismatch"})
            if not k["hwid"]:
                k["hwid"]   = hwid
                k["status"] = "used"
                save_keys(keys)
            return jsonify({"valid": True, "type": k["type"]})
    return jsonify({"valid": False, "reason": "Key not found"})

# ─── STOCK ────────────────────────────────────────────────────────────────────
@app.route("/stock", methods=["GET"])
def get_stock():
    keys = load_keys()
    return jsonify({
        "standard":  sum(1 for k in keys if k["type"] == "standard"  and k["status"] == "unused"),
        "premium":   sum(1 for k in keys if k["type"] == "premium"   and k["status"] == "unused"),
        "crosshair": sum(1 for k in keys if k["type"] == "crosshair" and k["status"] == "unused"),
    })

# ─── STRIPE: Create Payment Intent ───────────────────────────────────────────
@app.route("/create-payment-intent", methods=["POST"])
def create_payment_intent():
    try:
        data    = request.json or {}
        product = str(data.get("product", "")).strip()
        email   = str(data.get("email",   "")).strip()
        name    = str(data.get("name",    "Customer")).strip()[:100]

        if product not in ALLOWED_PRODUCTS:
            return jsonify({"error": "Invalid product"}), 400
        if not email or "@" not in email or len(email) > 254:
            return jsonify({"error": "Invalid email address"}), 400

        amount    = PRODUCT_PRICES[product]
        keys      = load_keys()
        available = [k for k in keys if k["type"] == product and k["status"] == "unused"]
        if not available:
            return jsonify({"error": f"No {product} keys in stock"}), 400

        intent = stripe.PaymentIntent.create(
            amount=amount,
            currency="usd",
            receipt_email=email,
            metadata={"product": product, "customer_name": name, "customer_email": email}
        )
        return jsonify({"clientSecret": intent.client_secret})

    except stripe.error.StripeError as e:
        print(f"[STRIPE ERROR] {e}")
        return jsonify({"error": "Payment provider error. Please try again."}), 502
    except Exception as e:
        print(f"[ERROR] {e}")
        return jsonify({"error": "Internal server error"}), 500

# ─── STRIPE: Webhook ─────────────────────────────────────────────────────────
@app.route("/stripe-webhook", methods=["POST"])
def stripe_webhook():
    payload    = request.data
    sig_header = request.headers.get("Stripe-Signature", "")
    try:
        event = stripe.Webhook.construct_event(payload, sig_header, STRIPE_WEBHOOK_SECRET)
    except stripe.error.SignatureVerificationError:
        print("[WEBHOOK] Invalid signature — rejected")
        return jsonify({"error": "Invalid signature"}), 400
    except Exception as e:
        print(f"[WEBHOOK ERROR] {e}")
        return jsonify({"error": str(e)}), 400

    if event["type"] == "payment_intent.succeeded":
        pi      = event["data"]["object"]
        email   = pi.get("receipt_email") or pi["metadata"].get("customer_email", "")
        product = pi["metadata"].get("product", "standard")
        name    = pi["metadata"].get("customer_name", "Customer")

        if product not in ALLOWED_PRODUCTS:
            print(f"[WEBHOOK] Invalid product: {product}")
            return jsonify({"status": "ok"})

        keys      = load_keys()
        available = [k for k in keys if k["type"] == product and k["status"] == "unused"]
        if not available:
            print(f"[WEBHOOK] CRITICAL: No {product} keys for {email} — manual fulfillment needed!")
            return jsonify({"status": "ok"})

        chosen = available[0]
        for k in keys:
            if k["id"] == chosen["id"]:
                k["status"]  = "sold"
                k["email"]   = email
                k["sold_at"] = datetime.utcnow().isoformat()
                break

        save_keys(keys)
        print(f"[WEBHOOK] Key {chosen['key']} sold to {email}")
        send_key_email(email, name, product, chosen["key"])

    return jsonify({"status": "ok"})

# ─── GET KEY FOR SUCCESS PAGE ─────────────────────────────────────────────────
@app.route("/get-purchased-key", methods=["POST"])
def get_purchased_key():
    data              = request.json or {}
    payment_intent_id = str(data.get("payment_intent_id", "")).strip()
    if not payment_intent_id or not payment_intent_id.startswith("pi_"):
        return jsonify({"error": "Invalid payment intent ID"}), 400
    try:
        pi      = stripe.PaymentIntent.retrieve(payment_intent_id)
        email   = pi.get("receipt_email") or pi.metadata.get("customer_email", "")
        product = pi.metadata.get("product", "standard")
        keys    = load_keys()
        for k in keys:
            if k.get("email") == email and k["type"] == product and k["status"] == "sold":
                return jsonify({"key": k["key"], "type": k["type"]})
        return jsonify({"error": "Key not found"}), 404
    except stripe.error.StripeError as e:
        print(f"[STRIPE ERROR] {e}")
        return jsonify({"error": "Could not verify payment"}), 502
    except Exception as e:
        print(f"[ERROR] {e}")
        return jsonify({"error": "Internal server error"}), 500

# ─── RUN ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("\n  DUSKZ Key Server v2.0")
    print("  Running on http://localhost:5000\n")
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)
