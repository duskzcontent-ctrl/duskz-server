from flask import Flask, request, jsonify
from flask_cors import CORS
import json, os, uuid, smtplib, stripe
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

app = Flask(__name__)
CORS(app)

# ─── CONFIG ──────────────────────────────────────────────────
STRIPE_SECRET_KEY     = os.environ.get("STRIPE_SECRET_KEY", "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")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")  # Set this after creating webhook in Stripe dashboard
SMTP_HOST     = os.environ.get("SMTP_HOST", "mail.duskz.shop")
SMTP_PORT     = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER     = os.environ.get("SMTP_USER", "noreply@duskz.shop")
SMTP_PASS     = os.environ.get("SMTP_PASS", "YOUR_EMAIL_PASSWORD")
FROM_EMAIL    = "Duskz <noreply@duskz.shop>"

stripe.api_key = STRIPE_SECRET_KEY

DB_FILE = "keys.json"

# ─── DB ──────────────────────────────────────────────────────
def load_keys():
    if not os.path.exists(DB_FILE):
        return []
    with open(DB_FILE, "r") as f:
        return json.load(f)

def save_keys(keys):
    with open(DB_FILE, "w") as f:
        json.dump(keys, f, indent=2)

# ─── EMAIL ───────────────────────────────────────────────────
def send_key_email(to_email, customer_name, product_type, key_str):
    try:
        product_name = "Duskz Premium" if product_type == "premium" else "Duskz Standard"
        price = "$10.99" if product_type == "premium" else "$7.99"

        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"Your {product_name} License Key - Duskz"
        msg["From"] = FROM_EMAIL
        msg["To"] = to_email

        html = f"""
        <!DOCTYPE html>
        <html>
        <head>
        <style>
            body {{ font-family: 'Arial', sans-serif; background: #0a0a0f; color: #e8e8f0; margin: 0; padding: 0; }}
            .container {{ max-width: 600px; margin: 0 auto; }}
            .header {{ background: linear-gradient(135deg, #00ff88, #00d4ff); padding: 40px; text-align: center; }}
            .header h1 {{ color: #0a0a0f; font-size: 28px; margin: 0; }}
            .body {{ background: #12121a; padding: 40px; }}
            .key-box {{ background: #0a0a0f; border: 2px solid #00ff88; border-radius: 12px; padding: 24px; text-align: center; margin: 24px 0; }}
            .key-label {{ color: #8a8a9f; font-size: 13px; margin-bottom: 10px; }}
            .key-value {{ color: #00ff88; font-size: 20px; font-weight: bold; letter-spacing: 2px; word-break: break-all; }}
            .steps {{ background: #1a1a24; border-radius: 12px; padding: 24px; margin: 24px 0; }}
            .steps h3 {{ color: #00d4ff; margin-top: 0; }}
            .step {{ padding: 8px 0; border-bottom: 1px solid rgba(0,255,136,0.1); color: #8a8a9f; }}
            .step:last-child {{ border-bottom: none; }}
            .step span {{ color: #00ff88; margin-right: 10px; }}
            .btn {{ display: inline-block; background: linear-gradient(135deg, #00ff88, #00d4ff); color: #0a0a0f; padding: 14px 32px; border-radius: 8px; text-decoration: none; font-weight: bold; margin: 10px 5px; }}
            .footer {{ background: #0a0a0f; padding: 24px; text-align: center; color: #8a8a9f; font-size: 13px; }}
        </style>
        </head>
        <body>
        <div class="container">
            <div class="header">
                <h1>⚡ Payment Successful!</h1>
            </div>
            <div class="body">
                <p>Hey {customer_name},</p>
                <p>Thank you for purchasing <strong style="color:#00ff88">{product_name}</strong>! Here is your license key:</p>

                <div class="key-box">
                    <div class="key-label">YOUR LICENSE KEY</div>
                    <div class="key-value">{key_str}</div>
                </div>

                <div class="steps">
                    <h3>📥 Next Steps</h3>
                    <div class="step"><span>1.</span> Download Duskz from our Discord</div>
                    <div class="step"><span>2.</span> Run Duskz.exe as Administrator</div>
                    <div class="step"><span>3.</span> Enter your license key above</div>
                    <div class="step"><span>4.</span> Enjoy!</div>
                </div>

                <div style="text-align:center; margin-top: 30px;">
                    <a href="https://discord.gg/u85uVGhMBF" class="btn">Join Discord</a>
                    <a href="https://duskz.shop/installation-guide.html" class="btn">Installation Guide</a>
                </div>

                <p style="color:#8a8a9f; font-size:13px; margin-top:30px;">
                    Keep this email safe — your license key is tied to your HWID and cannot be recovered if lost.
                </p>
            </div>
            <div class="footer">
                <p>Duskz — Premium Roblox External</p>
                <p>Questions? Join our <a href="https://discord.gg/u85uVGhMBF" style="color:#00ff88;">Discord</a></p>
            </div>
        </div>
        </body>
        </html>
        """

        msg.attach(MIMEText(html, "html"))

        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.starttls()
            server.login(SMTP_USER, SMTP_PASS)
            server.sendmail(FROM_EMAIL, to_email, msg.as_string())

        print(f"[EMAIL] Sent key to {to_email}")
        return True
    except Exception as e:
        print(f"[EMAIL ERROR] {e}")
        return False

# ─── KEY ROUTES ──────────────────────────────────────────────
@app.route("/keys", methods=["GET"])
def get_keys():
    return jsonify(load_keys())

@app.route("/keys", methods=["POST"])
def create_key():
    data = request.json
    keys = load_keys()
    key_str = data.get("key")
    key_type = data.get("type", "standard")
    if not key_str:
        return jsonify({"error": "No key provided"}), 400
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

@app.route("/keys/<key_str>", methods=["DELETE"])
def delete_key(key_str):
    keys = load_keys()
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
            k["hwid"] = None
            k["status"] = "unused"
            save_keys(keys)
            return jsonify(k)
    return jsonify({"error": "Key not found"}), 404

# ─── VALIDATE (C++ loader) ───────────────────────────────────
@app.route("/validate", methods=["POST"])
def validate_key():
    data = request.json
    key_str = data.get("key")
    hwid = data.get("hwid")
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
                k["hwid"] = hwid
                k["status"] = "used"
                save_keys(keys)
            return jsonify({"valid": True, "type": k["type"]})
    return jsonify({"valid": False, "reason": "Key not found"})

# ─── STOCK ───────────────────────────────────────────────────
@app.route("/stock", methods=["GET"])
def get_stock():
    keys = load_keys()
    standard_unused = sum(1 for k in keys if k["type"] == "standard" and k["status"] == "unused")
    premium_unused  = sum(1 for k in keys if k["type"] == "premium"  and k["status"] == "unused")
    return jsonify({"standard": standard_unused, "premium": premium_unused})

# ─── STRIPE: Create Payment Intent ───────────────────────────
@app.route("/create-payment-intent", methods=["POST"])
def create_payment_intent():
    try:
        data = request.json
        product   = data.get("product", "standard")
        email     = data.get("email", "")
        name      = data.get("name", "Customer")

        prices = {"standard": 799, "premium": 1099}
        amount = prices.get(product, 799)

        # Check stock
        keys = load_keys()
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
    except Exception as e:
        return jsonify({"error": str(e)}), 400

# ─── STRIPE: Webhook ─────────────────────────────────────────
@app.route("/stripe-webhook", methods=["POST"])
def stripe_webhook():
    payload = request.data
    sig_header = request.headers.get("Stripe-Signature", "")

    try:
        if STRIPE_WEBHOOK_SECRET:
            event = stripe.Webhook.construct_event(payload, sig_header, STRIPE_WEBHOOK_SECRET)
        else:
            event = json.loads(payload)
    except Exception as e:
        print(f"[WEBHOOK ERROR] {e}")
        return jsonify({"error": str(e)}), 400

    if event["type"] == "payment_intent.succeeded":
        pi = event["data"]["object"]
        email    = pi.get("receipt_email") or pi["metadata"].get("customer_email", "")
        product  = pi["metadata"].get("product", "standard")
        name     = pi["metadata"].get("customer_name", "Customer")

        keys = load_keys()
        available = [k for k in keys if k["type"] == product and k["status"] == "unused"]

        if not available:
            print(f"[WEBHOOK] ERROR: No {product} keys available!")
            return jsonify({"error": "No keys available"}), 500

        # Grab first available key
        chosen = available[0]
        for k in keys:
            if k["id"] == chosen["id"]:
                k["status"] = "sold"
                k["email"] = email
                k["sold_at"] = datetime.utcnow().isoformat()
                break

        save_keys(keys)
        print(f"[WEBHOOK] Key {chosen['key']} sold to {email}")

        # Send email
        send_key_email(email, name, product, chosen["key"])

    return jsonify({"status": "ok"})

# ─── GET KEY FOR SUCCESS PAGE ─────────────────────────────────
@app.route("/get-purchased-key", methods=["POST"])
def get_purchased_key():
    data = request.json
    payment_intent_id = data.get("payment_intent_id")
    if not payment_intent_id:
        return jsonify({"error": "Missing payment_intent_id"}), 400
    try:
        pi = stripe.PaymentIntent.retrieve(payment_intent_id)
        email   = pi.get("receipt_email") or pi.metadata.get("customer_email", "")
        product = pi.metadata.get("product", "standard")
        keys = load_keys()
        for k in keys:
            if k.get("email") == email and k["type"] == product and k["status"] == "sold":
                return jsonify({"key": k["key"], "type": k["type"]})
        return jsonify({"error": "Key not found"}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 400

# ─── RUN ─────────────────────────────────────────────────────
if __name__ == "__main__":
    print("\n  DUSKZ Key Server v2.0")
    print("  Running on http://localhost:5000\n")
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)
