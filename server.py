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
                    created TIMESTAMP NOT NULL,
                    sold_at TIMESTAMP
                )
            """)
            # Add new columns if upgrading from old schema
            cur.execute("""
                ALTER TABLE keys ADD COLUMN IF NOT EXISTS email TEXT;
            """)
            cur.execute("""
                ALTER TABLE keys ADD COLUMN IF NOT EXISTS sold_at TIMESTAMP;
            """)
        conn.commit()

init_db()

# ─────────────────────────────────────
# HELPERS
# ─────────────────────────────────────
def claim_unused_key(key_type="standard"):
    """Atomically grab the oldest unused key of a given type and mark it sold."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE keys
                SET status = 'sold', sold_at = %s
                WHERE key = (
                    SELECT key FROM keys
                    WHERE status = 'unused' AND type = %s
                    ORDER BY created ASC
                    LIMIT 1
                    FOR UPDATE SKIP LOCKED
                )
                RETURNING key
            """, (datetime.utcnow(), key_type))
            row = cur.fetchone()
        conn.commit()
    return row["key"] if row else None


def claim_and_tag_key(key_type, customer_email):
    """Claim a key and store the buyer's email on it."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE keys
                SET status = 'sold', sold_at = %s, email = %s
                WHERE key = (
                    SELECT key FROM keys
                    WHERE status = 'unused' AND type = %s
                    ORDER BY created ASC
                    LIMIT 1
                    FOR UPDATE SKIP LOCKED
                )
                RETURNING key
            """, (datetime.utcnow(), customer_email, key_type))
            row = cur.fetchone()
        conn.commit()
    return row["key"] if row else None


def send_key_email(to_email, key, key_type="standard"):
    """Send the license key to the buyer via Gmail SMTP."""
    from_addr = os.environ.get("EMAIL_FROM")
    user = os.environ.get("EMAIL_USER")
    password = os.environ.get("EMAIL_PASS")

    msg = MIMEMultipart("alternative")
    msg["Subject"] = "Your License Key - Thank You!"
    msg["From"] = from_addr
    msg["To"] = to_email

    plain = f"""Thank you for your purchase!

Your {key_type.capitalize()} License Key:

  {key}

────────────────────────────────
HOW TO ACTIVATE:
1. Open the loader
2. Enter your license key
3. Your HWID will be bound on first use

IMPORTANT: Keep this key safe. It is locked to your hardware once activated.
If you need your HWID reset, contact support.
────────────────────────────────

Thanks for your support!
"""

    html = f"""
<html><body style="font-family:Arial,sans-serif;max-width:600px;margin:auto;padding:20px;">
  <h2 style="color:#1a1a1a;">Your License Key</h2>
  <p>Thank you for your purchase! Here is your <strong>{key_type.capitalize()}</strong> license key:</p>
  <div style="background:#f4f4f4;border:1px solid #ddd;border-radius:6px;padding:16px;font-size:18px;font-family:monospace;letter-spacing:2px;text-align:center;">
    {key}
  </div>
  <h3>How to Activate</h3>
  <ol>
    <li>Open the loader</li>
    <li>Enter your license key above</li>
    <li>Your hardware ID (HWID) will be bound on first use</li>
  </ol>
  <p style="color:#888;font-size:12px;">Keep this key safe — it is locked to your hardware once activated. Need a HWID reset? Contact support.</p>
</body></html>
"""

    msg.attach(MIMEText(plain, "plain"))
    msg.attach(MIMEText(html, "html"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(user, password)
        server.sendmail(from_addr, to_email, msg.as_string())


def alert_low_stock(key_type):
    """Send yourself an email when stock is running low."""
    admin_email = os.environ.get("ADMIN_EMAIL") or os.environ.get("EMAIL_FROM")
    if not admin_email:
        return
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) as cnt FROM keys WHERE status='unused' AND type=%s", (key_type,))
                row = cur.fetchone()
                count = row["cnt"]

        if count <= 5:
            send_key_email.__func__ if hasattr(send_key_email, '__func__') else None
            from_addr = os.environ.get("EMAIL_FROM")
            user = os.environ.get("EMAIL_USER")
            password = os.environ.get("EMAIL_PASS")
            msg = MIMEText(f"WARNING: Only {count} unused '{key_type}' keys remaining in stock. Add more soon!")
            msg["Subject"] = f"[KEY SYSTEM] Low stock alert: {key_type}"
            msg["From"] = from_addr
            msg["To"] = admin_email
            with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
                server.login(user, password)
                server.sendmail(from_addr, admin_email, msg.as_string())
    except Exception as e:
        print(f"Low stock alert failed: {e}")


# ─────────────────────────────────────
# STRIPE - CREATE PAYMENT INTENT
# ─────────────────────────────────────
PRICES = {
    "standard": 1000,   # $10.00 — edit these
    "premium":  2500,   # $25.00
    "lifetime": 5000,   # $50.00
}

@app.route("/create-payment-intent", methods=["POST"])
def create_payment_intent():
    data = request.json or {}
    key_type = data.get("key_type", "standard")
    amount = PRICES.get(key_type, 1000)

    try:
        intent = stripe.PaymentIntent.create(
            amount=amount,
            currency="usd",
            metadata={"key_type": key_type},
            automatic_payment_methods={"enabled": True},
        )
        return jsonify(clientSecret=intent.client_secret, amount=amount)
    except Exception as e:
        return jsonify(error=str(e)), 500


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
    except ValueError:
        return jsonify(error="Invalid payload"), 400
    except stripe.error.SignatureVerificationError:
        return jsonify(error="Invalid signature"), 400

    if event["type"] == "payment_intent.succeeded":
        pi = event["data"]["object"]
        customer_email = pi.get("receipt_email")
        key_type = pi.get("metadata", {}).get("key_type", "standard")

        if not customer_email:
            print(f"WARNING: No receipt_email on payment_intent {pi['id']}")
            return jsonify(received=True), 200

        key = claim_and_tag_key(key_type, customer_email)

        if key:
            try:
                send_key_email(customer_email, key, key_type)
                alert_low_stock(key_type)
                print(f"Key {key} sold and emailed to {customer_email}")
            except Exception as e:
                print(f"Email failed for {customer_email}: {e}")
                # Key is already claimed — log it so you can resend manually
        else:
            print(f"CRITICAL: No unused '{key_type}' keys available! Payment {pi['id']} from {customer_email}")
            # TODO: refund or alert yourself — add Stripe refund logic here if needed

    return jsonify(received=True), 200


# ─────────────────────────────────────
# GET ALL KEYS
# ─────────────────────────────────────
@app.route("/keys", methods=["GET"])
def get_keys():
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM keys ORDER BY created DESC")
            return jsonify(cur.fetchall())


# ─────────────────────────────────────
# CREATE KEY
# ─────────────────────────────────────
@app.route("/keys", methods=["POST"])
def create_key():
    data = request.json or {}
    key = data.get("key")
    type_ = data.get("type", "standard")

    if not key:
        return jsonify({"error": "Key required"}), 400

    record = {
        "id": str(uuid.uuid4()),
        "key": key,
        "type": type_,
        "status": "unused",
        "hwid": None,
        "email": None,
        "created": datetime.utcnow(),
        "sold_at": None,
    }

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO keys (id, key, type, status, hwid, email, created, sold_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (key) DO NOTHING
                RETURNING *
            """, (
                record["id"], record["key"], record["type"],
                record["status"], record["hwid"], record["email"],
                record["created"], record["sold_at"]
            ))
            inserted = cur.fetchone()
        conn.commit()

    if not inserted:
        return jsonify({"error": "Key already exists"}), 400

    return jsonify(inserted)


# ─────────────────────────────────────
# BULK CREATE KEYS
# ─────────────────────────────────────
@app.route("/keys/bulk", methods=["POST"])
def bulk_create_keys():
    """Generate N random keys at once. POST { count: 10, type: 'standard' }"""
    data = request.json or {}
    count = min(int(data.get("count", 1)), 100)  # cap at 100
    type_ = data.get("type", "standard")

    created = []
    with get_db() as conn:
        with conn.cursor() as cur:
            for _ in range(count):
                new_key = str(uuid.uuid4()).replace("-", "").upper()[:24]
                new_key = f"{new_key[:6]}-{new_key[6:12]}-{new_key[12:18]}-{new_key[18:24]}"
                try:
                    cur.execute("""
                        INSERT INTO keys (id, key, type, status, hwid, email, created, sold_at)
                        VALUES (%s,%s,%s,'unused',NULL,NULL,%s,NULL)
                        ON CONFLICT (key) DO NOTHING
                        RETURNING *
                    """, (str(uuid.uuid4()), new_key, type_, datetime.utcnow()))
                    row = cur.fetchone()
                    if row:
                        created.append(row)
                except Exception:
                    pass
        conn.commit()

    return jsonify({"created": len(created), "keys": created})


# ─────────────────────────────────────
# STOCK COUNT
# ─────────────────────────────────────
@app.route("/keys/stock", methods=["GET"])
def stock_count():
    """Returns unused key counts per type."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT type, COUNT(*) as available
                FROM keys WHERE status='unused'
                GROUP BY type
            """)
            return jsonify(cur.fetchall())


# ─────────────────────────────────────
# RESET HWID
# ─────────────────────────────────────
@app.route("/keys/<key>/reset-hwid", methods=["POST"])
def reset_hwid(key):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE keys SET hwid=NULL, status='sold'
                WHERE key=%s RETURNING *
            """, (key,))
            updated = cur.fetchone()
        conn.commit()

    if not updated:
        return jsonify({"error": "Key not found"}), 404
    return jsonify(updated)


# ─────────────────────────────────────
# BAN
# ─────────────────────────────────────
@app.route("/keys/<key>/ban", methods=["POST"])
def ban_key(key):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE keys SET status='banned'
                WHERE key=%s RETURNING *
            """, (key,))
            updated = cur.fetchone()
        conn.commit()

    if not updated:
        return jsonify({"error": "Key not found"}), 404
    return jsonify(updated)


# ─────────────────────────────────────
# UNBAN
# ─────────────────────────────────────
@app.route("/keys/<key>/unban", methods=["POST"])
def unban_key(key):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE keys SET status='unused'
                WHERE key=%s RETURNING *
            """, (key,))
            updated = cur.fetchone()
        conn.commit()

    if not updated:
        return jsonify({"error": "Key not found"}), 404
    return jsonify(updated)


# ─────────────────────────────────────
# DELETE
# ─────────────────────────────────────
@app.route("/keys/<key>", methods=["DELETE"])
def delete_key(key):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                DELETE FROM keys WHERE key=%s RETURNING *
            """, (key,))
            deleted = cur.fetchone()
        conn.commit()

    if not deleted:
        return jsonify({"error": "Key not found"}), 404
    return jsonify({"success": True})


# ─────────────────────────────────────
# VALIDATE KEY (called by C++ loader)
# ─────────────────────────────────────
@app.route("/validate", methods=["POST"])
def validate_key():
    data = request.json or {}
    key = data.get("key")
    hwid = data.get("hwid")

    if not key or not hwid:
        return jsonify({"valid": False, "error": "Missing key or hwid"}), 400

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM keys WHERE key=%s", (key,))
            record = cur.fetchone()

    if not record:
        return jsonify({"valid": False, "error": "Key not found"})

    if record["status"] == "banned":
        return jsonify({"valid": False, "error": "Key is banned"})

    # Key was sold but never activated — bind HWID now
    if record["status"] == "sold" and record["hwid"] is None:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE keys SET hwid=%s, status='active' WHERE key=%s",
                    (hwid, key)
                )
            conn.commit()
        return jsonify({"valid": True, "type": record["type"]})

    # Legacy: unused key (manually created, not sold via Stripe)
    if record["status"] == "unused" and record["hwid"] is None:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE keys SET hwid=%s, status='active' WHERE key=%s",
                    (hwid, key)
                )
            conn.commit()
        return jsonify({"valid": True, "type": record["type"]})

    if record["status"] == "active":
        if record["hwid"] != hwid:
            return jsonify({"valid": False, "error": "HWID mismatch"})
        return jsonify({"valid": True, "type": record["type"]})

    return jsonify({"valid": False, "error": "Key not eligible for activation"})


# ─────────────────────────────────────
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
