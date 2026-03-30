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

SMTP_HOST     = os.environ.get("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT     = int(os.environ.get("SMTP_PORT", 587))
SMTP_USER     = os.environ.get("SMTP_USER")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD")
FROM_EMAIL    = os.environ.get("FROM_EMAIL", SMTP_USER)

# ─────────────────────────────────────
# PRODUCT CATALOG
# ─────────────────────────────────────
PRODUCTS = {
    "standard":  {"name": "Duskz Standard",       "amount": 799,  "type": "standard"},
    "premium":   {"name": "Duskz Premium",         "amount": 1099, "type": "premium"},
    "crosshair": {"name": "Custom Crosshair ZX",   "amount": 199,  "type": "crosshair"},
    "roblox":    {"name": "Roblox 200 Robux Alt",  "amount": 299,  "type": "roblox"},
    "roblox1k":  {"name": "Roblox 1K Robux Alt",   "amount": 1250, "type": "roblox1k"},
}

# ─────────────────────────────────────
# DB HELPERS
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
                    buyer_name TEXT,
                    payment_intent_id TEXT,
                    created_at TIMESTAMP NOT NULL
                )
            """)
            cur.execute("""
                ALTER TABLE accounts ADD COLUMN IF NOT EXISTS buyer_name TEXT
            """)
        conn.commit()

init_db()

# ─────────────────────────────────────
# EMAIL HELPER
# ─────────────────────────────────────
def send_email(to_email, subject, html_body):
    if not SMTP_USER or not SMTP_PASSWORD:
        print(f"[EMAIL] SMTP not configured — would have sent to {to_email}: {subject}")
        return False
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"]    = FROM_EMAIL
        msg["To"]      = to_email
        msg.attach(MIMEText(html_body, "html"))
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.ehlo()
            server.starttls()
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.sendmail(FROM_EMAIL, to_email, msg.as_string())
        print(f"[EMAIL] Sent '{subject}' to {to_email}")
        return True
    except Exception as e:
        print(f"[EMAIL] Failed to send to {to_email}: {e}")
        return False

def send_roblox_account_email(to_email, buyer_name, account_str, robux_amount="200"):
    parts    = account_str.split(":", 1)
    username = parts[0] if len(parts) > 0 else "N/A"
    password = parts[1] if len(parts) > 1 else "N/A"
    subject  = f"Your Duskz Roblox {robux_amount} Robux Account — Order Confirmed"
    html     = f"""
    <div style="background:#070709;color:#d8d8e0;font-family:sans-serif;padding:40px;max-width:520px;margin:auto;border-radius:12px;border:1px solid rgba(255,255,255,0.08)">
      <h2 style="color:#ffffff;letter-spacing:-1px;margin-bottom:6px">Your order is ready ✓</h2>
      <p style="color:#7a7a8a;font-size:14px;margin-bottom:28px">Hi {buyer_name}, here are your account details.</p>
      <div style="background:#111116;border:1px solid rgba(255,255,255,0.1);border-radius:8px;padding:20px;margin-bottom:20px">
        <p style="font-size:12px;color:#4a4a58;text-transform:uppercase;letter-spacing:2px;margin-bottom:12px">Roblox Account — {robux_amount} Robux</p>
        <p style="margin-bottom:8px"><span style="color:#7a7a8a;font-size:13px">Username: </span><strong style="color:#ffffff;font-size:15px">{username}</strong></p>
        <p><span style="color:#7a7a8a;font-size:13px">Password: </span><strong style="color:#ffffff;font-size:15px">{password}</strong></p>
      </div>
      <p style="color:#4a4a58;font-size:12px;line-height:1.6">
        ⚠️ Change the password immediately after logging in.<br>
        Need help? Open a ticket on our <a href="https://discord.gg/JEU9XcAdRs" style="color:#7c5cfc">Discord server</a>.
      </p>
      <p style="color:#2a2a38;font-size:11px;margin-top:24px">© 2025 Duskz · Not affiliated with Roblox Corporation</p>
    </div>
    """
    return send_email(to_email, subject, html)

def send_crosshair_email(to_email, buyer_name, license_key):
    subject = "Your Duskz Crosshair ZX License — Order Confirmed"
    html    = f"""
    <div style="background:#070709;color:#d8d8e0;font-family:sans-serif;padding:40px;max-width:520px;margin:auto;border-radius:12px;border:1px solid rgba(255,255,255,0.08)">
      <h2 style="color:#ffffff;letter-spacing:-1px;margin-bottom:6px">Your order is ready ✓</h2>
      <p style="color:#7a7a8a;font-size:14px;margin-bottom:28px">Hi {buyer_name}, here is your license key.</p>
      <div style="background:#111116;border:1px solid rgba(255,255,255,0.1);border-radius:8px;padding:20px;margin-bottom:20px">
        <p style="font-size:12px;color:#4a4a58;text-transform:uppercase;letter-spacing:2px;margin-bottom:12px">Custom Crosshair ZX</p>
        <p style="font-family:monospace;font-size:18px;color:#a87cff;letter-spacing:2px">{license_key}</p>
      </div>
      <p style="color:#4a4a58;font-size:12px;line-height:1.6">
        Need help? Open a ticket on our <a href="https://discord.gg/JEU9XcAdRs" style="color:#7c5cfc">Discord server</a>.
      </p>
    </div>
    """
    return send_email(to_email, subject, html)

# ─────────────────────────────────────
# STOCK ENDPOINT
# ─────────────────────────────────────
@app.route("/stock", methods=["GET"])
def get_stock():
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*) AS cnt FROM accounts WHERE claimed = FALSE AND status = 'unclaimed' AND type = 'roblox'"
                )
                row = cur.fetchone()
                roblox_count = row["cnt"] if row else 0

                cur.execute(
                    "SELECT COUNT(*) AS cnt FROM accounts WHERE claimed = FALSE AND status = 'unclaimed' AND type = 'roblox1k'"
                )
                row = cur.fetchone()
                roblox1k_count = row["cnt"] if row else 0

        return jsonify({"roblox": roblox_count, "roblox1k": roblox1k_count})
    except Exception as e:
        print(f"[STOCK] Error: {e}")
        return jsonify({"roblox": 0, "roblox1k": 0})

# ─────────────────────────────────────
# CREATE PAYMENT INTENT
# ─────────────────────────────────────
@app.route("/create-payment-intent", methods=["POST"])
def create_payment_intent():
    data    = request.json or {}
    product = data.get("product", "standard")
    email   = data.get("email", "").strip()
    name    = data.get("name", "").strip()

    if product not in PRODUCTS:
        return jsonify({"error": "Invalid product"}), 400
    if not email or "@" not in email:
        return jsonify({"error": "Valid email required"}), 400

    # Pre-flight stock check for account products
    if product in ("roblox", "roblox1k"):
        acc_type = PRODUCTS[product]["type"]
        try:
            with get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT id FROM accounts WHERE claimed = FALSE AND status = 'unclaimed' AND type = %s LIMIT 1",
                        (acc_type,)
                    )
                    if not cur.fetchone():
                        return jsonify({"error": "Out of stock — check back soon!"}), 400
        except Exception as e:
            print(f"[STOCK CHECK] Error: {e}")

    p = PRODUCTS[product]
    try:
        intent = stripe.PaymentIntent.create(
            amount=p["amount"],
            currency="usd",
            receipt_email=email,
            metadata={
                "product":  product,
                "key_type": p["type"],
                "email":    email,
                "name":     name,
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
    payload    = request.get_data()
    sig_header = request.headers.get("Stripe-Signature")

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, STRIPE_WEBHOOK_SECRET)
    except Exception:
        return jsonify({"error": "Webhook verification failed"}), 400

    if event["type"] == "payment_intent.succeeded":
        pi      = event["data"]["object"]
        pi_id   = pi["id"]
        meta    = pi.get("metadata", {})
        email   = meta.get("email")
        name    = meta.get("name", "Customer")
        product = meta.get("product")

        if not email or not product:
            print(f"[WEBHOOK] Missing metadata on PaymentIntent {pi_id}")
            return jsonify({"received": True})

        # ── ROBLOX ACCOUNT DELIVERY (200 or 1K) ───────────────
        if product in ("roblox", "roblox1k"):
            acc_type     = PRODUCTS[product]["type"]
            robux_label  = "1,000" if product == "roblox1k" else "200"
            try:
                with get_db() as conn:
                    with conn.cursor() as cur:
                        cur.execute("""
                            UPDATE accounts
                            SET claimed = TRUE,
                                status = 'claimed',
                                buyer_email = %s,
                                buyer_name = %s,
                                payment_intent_id = %s
                            WHERE id = (
                                SELECT id FROM accounts
                                WHERE claimed = FALSE AND status = 'unclaimed' AND type = %s
                                ORDER BY created_at ASC
                                LIMIT 1
                                FOR UPDATE SKIP LOCKED
                            )
                            RETURNING account
                        """, (email, name, pi_id, acc_type))
                        row = cur.fetchone()
                    conn.commit()

                if row:
                    send_roblox_account_email(email, name, row["account"], robux_label)
                    print(f"[WEBHOOK] Roblox {robux_label} Robux account delivered to {email}")
                else:
                    print(f"[WEBHOOK] WARNING: No {acc_type} accounts left for {email} (PI: {pi_id})")
                    send_email(
                        email,
                        "Duskz — Action Required on Your Order",
                        f"""<div style="font-family:sans-serif;padding:32px;background:#070709;color:#d8d8e0;border-radius:12px">
                        <h2 style="color:#fff">We're sorting your order</h2>
                        <p style="color:#7a7a8a">Hi {name}, we received your payment but ran out of stock at the same moment.
                        We'll get your account to you within 24 hours or issue a full refund. Please open a ticket on
                        <a href="https://discord.gg/JEU9XcAdRs" style="color:#7c5cfc">Discord</a> with your order ID: <strong style="color:#fff">{pi_id}</strong></p>
                        </div>"""
                    )
            except Exception as e:
                print(f"[WEBHOOK] Roblox delivery error for {email}: {e}")

        # ── CROSSHAIR / SOFTWARE KEY DELIVERY ─────────────────
        elif product in ("standard", "premium", "crosshair"):
            try:
                with get_db() as conn:
                    with conn.cursor() as cur:
                        key_type = PRODUCTS[product]["type"]
                        cur.execute("""
                            UPDATE keys
                            SET status = 'used',
                                email = %s,
                                buyer_name = %s,
                                payment_intent_id = %s,
                                sold_at = %s
                            WHERE id = (
                                SELECT id FROM keys
                                WHERE status = 'unused' AND type = %s
                                ORDER BY created ASC
                                LIMIT 1
                                FOR UPDATE SKIP LOCKED
                            )
                            RETURNING key
                        """, (email, name, pi_id, datetime.utcnow(), key_type))
                        row = cur.fetchone()
                    conn.commit()

                if row:
                    send_crosshair_email(email, name, row["key"])
                    print(f"[WEBHOOK] {product} key delivered to {email}")
                else:
                    print(f"[WEBHOOK] WARNING: No {product} keys in stock for {email} (PI: {pi_id})")
            except Exception as e:
                print(f"[WEBHOOK] Key delivery error for {email}: {e}")

    return jsonify({"received": True})

# ─────────────────────────────────────
# VALIDATE KEY
# ─────────────────────────────────────
@app.route("/validate", methods=["POST"])
def validate_key():
    data = request.json or {}
    key  = data.get("key")
    hwid = data.get("hwid")

    if not key or not hwid:
        return jsonify({"valid": False})

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM keys WHERE key=%s", (key,))
            record = cur.fetchone()

    if not record:
        return jsonify({"valid": False})

    if record["status"] == "banned":
        return jsonify({"valid": False, "reason": "banned"})

    if record["hwid"] is None:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE keys SET hwid=%s, status='used' WHERE key=%s",
                    (hwid, key),
                )
            conn.commit()
        return jsonify({"valid": True})

    if record["hwid"] != hwid:
        return jsonify({"valid": False, "reason": "hwid_mismatch"})

    return jsonify({"valid": True})

# ─────────────────────────────────────
# ADMIN — KEYS
# ─────────────────────────────────────
@app.route("/keys", methods=["GET"])
def list_keys():
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM keys ORDER BY created DESC")
            return jsonify(cur.fetchall())

@app.route("/keys", methods=["POST"])
def create_key():
    data     = request.json or {}
    key_str  = data.get("key")
    key_type = data.get("type", "standard")
    if not key_str:
        return jsonify({"error": "key required"}), 400
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                new_id = str(uuid.uuid4())
                cur.execute(
                    "INSERT INTO keys (id, key, type, status, created) VALUES (%s, %s, %s, 'unused', %s) RETURNING *",
                    (new_id, key_str, key_type, datetime.utcnow())
                )
                row = cur.fetchone()
            conn.commit()
        return jsonify(row), 201
    except psycopg2.errors.UniqueViolation:
        return jsonify({"error": "Key already exists"}), 409

@app.route("/keys/<path:key_str>", methods=["DELETE"])
def delete_key(key_str):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM keys WHERE key=%s RETURNING id", (key_str,))
            if not cur.fetchone():
                return jsonify({"error": "Key not found"}), 404
        conn.commit()
    return jsonify({"deleted": True})

@app.route("/keys/<path:key_str>/reset-hwid", methods=["POST"])
def reset_hwid(key_str):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE keys SET hwid=NULL, status='unused' WHERE key=%s RETURNING *",
                (key_str,)
            )
            row = cur.fetchone()
            if not row:
                return jsonify({"error": "Key not found"}), 404
        conn.commit()
    return jsonify(row)

@app.route("/keys/<path:key_str>/ban", methods=["POST"])
def ban_key(key_str):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE keys SET status='banned' WHERE key=%s RETURNING *",
                (key_str,)
            )
            row = cur.fetchone()
            if not row:
                return jsonify({"error": "Key not found"}), 404
        conn.commit()
    return jsonify(row)

@app.route("/keys/<path:key_str>/unban", methods=["POST"])
def unban_key(key_str):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE keys SET status='unused' WHERE key=%s RETURNING *",
                (key_str,)
            )
            row = cur.fetchone()
            if not row:
                return jsonify({"error": "Key not found"}), 404
        conn.commit()
    return jsonify(row)

# ─────────────────────────────────────
# ADMIN — ACCOUNTS
# ─────────────────────────────────────
@app.route("/admin/accounts", methods=["GET"])
def list_accounts():
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM accounts ORDER BY created_at DESC")
            return jsonify(cur.fetchall())

@app.route("/admin/accounts/add", methods=["POST"])
def add_accounts():
    data     = request.json or {}
    lines    = data.get("accounts", [])
    acc_type = data.get("type", "roblox")  # pass "roblox1k" for 1K accounts
    if not lines:
        return jsonify({"error": "No accounts provided"}), 400
    added = 0
    with get_db() as conn:
        with conn.cursor() as cur:
            for line in lines:
                line = line.strip()
                if not line or ":" not in line:
                    continue
                cur.execute(
                    "INSERT INTO accounts (id, account, type, claimed, status, created_at) VALUES (%s, %s, %s, FALSE, 'unclaimed', %s)",
                    (str(uuid.uuid4()), line, acc_type, datetime.utcnow())
                )
                added += 1
        conn.commit()
    return jsonify({"added": added})

@app.route("/admin/accounts/<acc_id>", methods=["DELETE"])
def delete_account(acc_id):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM accounts WHERE id=%s RETURNING id", (acc_id,))
            if not cur.fetchone():
                return jsonify({"error": "Account not found"}), 404
        conn.commit()
    return jsonify({"deleted": True})

# ─────────────────────────────────────
# ORDER LOOKUP
# ─────────────────────────────────────
@app.route("/order/<path:pi_id>", methods=["GET"])
def get_order(pi_id):
    pi_id = pi_id.strip()
    if not pi_id:
        return jsonify({"error": "Missing payment_intent"}), 400

    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT account AS key, buyer_email AS email, type FROM accounts WHERE payment_intent_id=%s AND claimed=TRUE LIMIT 1",
                    (pi_id,)
                )
                row = cur.fetchone()

                if not row:
                    cur.execute(
                        "SELECT key, email, type FROM keys WHERE payment_intent_id=%s LIMIT 1",
                        (pi_id,)
                    )
                    row = cur.fetchone()

        if row:
            return jsonify({"key": row["key"], "email": row["email"], "type": row["type"]})

        return jsonify({"status": "pending"}), 202

    except Exception as e:
        print(f"[ORDER] DB error: {e}")
        return jsonify({"error": "Database error"}), 500


@app.route("/order-details", methods=["GET"])
def order_details_legacy():
    pi_id = request.args.get("payment_intent", "").strip()
    if not pi_id:
        return jsonify({"error": "Missing payment_intent"}), 400
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT account, type FROM accounts WHERE payment_intent_id=%s AND claimed=TRUE",
                (pi_id,)
            )
            row = cur.fetchone()
            if row:
                parts = row["account"].split(":", 1)
                return jsonify({
                    "product": row["type"],
                    "username": parts[0],
                    "password": parts[1] if len(parts) > 1 else ""
                })
            cur.execute(
                "SELECT key, type FROM keys WHERE payment_intent_id=%s",
                (pi_id,)
            )
            row = cur.fetchone()
            if row:
                return jsonify({"product": row["type"], "key": row["key"]})
    return jsonify({"error": "Order not found"}), 404

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
