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
# PRODUCT CATALOG  ← edit prices here
# ─────────────────────────────────────
PRODUCTS = {
    "standard":  {"name": "Duskz Standard",     "amount": 799,  "type": "standard"},
    "premium":   {"name": "Duskz Premium",       "amount": 1099, "type": "premium"},
    "crosshair": {"name": "Custom Crosshair ZX", "amount": 199,  "type": "crosshair"},
    "roblox":    {"name": "Roblox Alt Account",  "amount": 270,  "type": "roblox"},
}

# ─────────────────────────────────────
# DB
# ─────────────────────────────────────
def get_db():
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)

def init_db():
    with get_db() as conn:
        with conn.cursor() as cur:
            # Keys table
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
            for col, definition in [
                ("email",             "TEXT"),
                ("buyer_name",        "TEXT"),
                ("sold_at",           "TIMESTAMP"),
                ("payment_intent_id", "TEXT"),
            ]:
                cur.execute(f"ALTER TABLE keys ADD COLUMN IF NOT EXISTS {col} {definition};")

            # Accounts table
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
# HELPERS
# ─────────────────────────────────────
def claim_key(key_type, customer_email, customer_name, payment_intent_id):
    """Atomically claim the oldest unused key."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE keys
                SET status = 'sold',
                    sold_at = %s,
                    email = %s,
                    buyer_name = %s,
                    payment_intent_id = %s
                WHERE key = (
                    SELECT key FROM keys
                    WHERE status = 'unused' AND type = %s
                    ORDER BY created ASC
                    LIMIT 1
                    FOR UPDATE SKIP LOCKED
                )
                RETURNING key
            """, (datetime.utcnow(), customer_email, customer_name, payment_intent_id, key_type))
            row = cur.fetchone()
        conn.commit()
    return row["key"] if row else None


def claim_account(acc_type, customer_email, payment_intent_id):
    """Atomically claim the oldest unclaimed account."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE accounts
                SET claimed = TRUE,
                    status = 'claimed',
                    buyer_email = %s,
                    payment_intent_id = %s
                WHERE id = (
                    SELECT id FROM accounts
                    WHERE claimed = FALSE AND status = 'unclaimed' AND type = %s
                    ORDER BY created_at ASC
                    LIMIT 1
                    FOR UPDATE SKIP LOCKED
                )
                RETURNING account
            """, (customer_email, payment_intent_id, acc_type))
            row = cur.fetchone()
        conn.commit()
    return row["account"] if row else None


def send_key_email(to_email, buyer_name, key, product_name):
    from_addr = os.environ.get("EMAIL_FROM")
    user      = os.environ.get("EMAIL_USER")
    password  = os.environ.get("EMAIL_PASS")

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"Your {product_name} — Order Confirmed"
    msg["From"]    = from_addr
    msg["To"]      = to_email

    plain = f"""Hey {buyer_name},

Thanks for your purchase!

Your {product_name}:

  {key}

Keep this safe. If you need support, join our Discord.

— Duskz Team
"""

    html = f"""
<html>
<body style="margin:0;padding:0;background:#080808;font-family:'Courier New',monospace;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#080808;padding:40px 20px;">
    <tr><td align="center">
      <table width="520" cellpadding="0" cellspacing="0"
             style="background:#111111;border-radius:12px;border:1px solid rgba(255,255,255,0.1);overflow:hidden;">
        <tr>
          <td style="padding:32px 36px 24px;border-bottom:1px solid rgba(255,255,255,0.07);">
            <div style="font-size:22px;font-weight:900;color:#ffffff;letter-spacing:3px;">⚡ DUSKZ</div>
            <div style="color:#555;font-size:12px;margin-top:4px;">Order Confirmed</div>
          </td>
        </tr>
        <tr>
          <td style="padding:32px 36px;">
            <p style="color:#aaa;font-size:14px;margin:0 0 6px;">Hey {buyer_name},</p>
            <p style="color:#f0f0f0;font-size:16px;font-weight:600;margin:0 0 28px;">Your purchase is confirmed. Here&rsquo;s your order:</p>
            <div style="background:#0d0d0d;border:1px solid rgba(255,255,255,0.12);border-radius:10px;padding:22px;text-align:center;margin-bottom:28px;">
              <div style="color:#666;font-size:11px;letter-spacing:2px;text-transform:uppercase;margin-bottom:10px;">{product_name}</div>
              <div style="color:#ffffff;font-size:19px;font-weight:700;letter-spacing:3px;word-break:break-all;">{key}</div>
            </div>
            <p style="color:#444;font-size:11px;line-height:1.7;margin:0;">
              Need help? Join our Discord and open a support ticket.
            </p>
          </td>
        </tr>
        <tr>
          <td style="padding:20px 36px;border-top:1px solid rgba(255,255,255,0.07);text-align:center;">
            <p style="color:#333;font-size:11px;margin:0;">&copy; Duskz &bull; Secured by Stripe</p>
          </td>
        </tr>
      </table>
    </td></tr>
  </table>
</body>
</html>
"""

    msg.attach(MIMEText(plain, "plain"))
    msg.attach(MIMEText(html, "html"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(user, password)
        server.sendmail(from_addr, to_email, msg.as_string())


def alert_low_stock(item_type, threshold=5):
    admin_email = os.environ.get("ADMIN_EMAIL") or os.environ.get("EMAIL_FROM")
    if not admin_email:
        return
    try:
        # Check keys table
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) as cnt FROM keys WHERE status='unused' AND type=%s", (item_type,))
                key_count = cur.fetchone()["cnt"]
                cur.execute("SELECT COUNT(*) as cnt FROM accounts WHERE claimed=FALSE AND type=%s", (item_type,))
                acc_count = cur.fetchone()["cnt"]

        count = key_count + acc_count
        if count <= threshold:
            from_addr = os.environ.get("EMAIL_FROM")
            user      = os.environ.get("EMAIL_USER")
            password  = os.environ.get("EMAIL_PASS")
            msg = MIMEText(f"Low stock: only {count} '{item_type}' item(s) left. Add more!")
            msg["Subject"] = f"[Duskz] Low stock: {item_type} ({count} left)"
            msg["From"]    = from_addr
            msg["To"]      = admin_email
            with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
                server.login(user, password)
                server.sendmail(from_addr, admin_email, msg.as_string())
    except Exception as e:
        print(f"Low stock alert failed: {e}")


# ─────────────────────────────────────
# STOCK — returns counts for all product types
# GET /stock → { standard: N, premium: N, crosshair: N, roblox: N }
# ─────────────────────────────────────
@app.route("/stock", methods=["GET"])
def stock():
    with get_db() as conn:
        with conn.cursor() as cur:
            # Key stock
            cur.execute("""
                SELECT type, COUNT(*) as count
                FROM keys WHERE status='unused'
                GROUP BY type
            """)
            key_rows = cur.fetchall()

            # Account stock
            cur.execute("""
                SELECT type, COUNT(*) as count
                FROM accounts WHERE claimed=FALSE AND status='unclaimed'
                GROUP BY type
            """)
            acc_rows = cur.fetchall()

    counts = {p: 0 for p in PRODUCTS}
    for row in key_rows:
        if row["type"] in counts:
            counts[row["type"]] += row["count"]
    for row in acc_rows:
        if row["type"] in counts:
            counts[row["type"]] += row["count"]

    return jsonify(counts)


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
    except ValueError:
        return jsonify(error="Invalid payload"), 400
    except stripe.error.SignatureVerificationError:
        return jsonify(error="Invalid signature"), 400

    if event["type"] == "payment_intent.succeeded":
        pi             = event["data"]["object"]
        meta           = pi.get("metadata", {})
        customer_email = meta.get("email") or pi.get("receipt_email", "")
        customer_name  = meta.get("name", "Customer")
        key_type       = meta.get("key_type", "standard")
        product_slug   = meta.get("product", key_type)
        pi_id          = pi["id"]
        product_name   = PRODUCTS.get(product_slug, {}).get("name", "Duskz License")

        if not customer_email:
            print(f"WARNING: No email on payment_intent {pi_id}")
            return jsonify(received=True), 200

        # Idempotency check
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT key FROM keys WHERE payment_intent_id=%s", (pi_id,))
                already_key = cur.fetchone()
                cur.execute("SELECT account FROM accounts WHERE payment_intent_id=%s", (pi_id,))
                already_acc = cur.fetchone()

        if already_key or already_acc:
            print(f"Duplicate webhook {pi_id}, skipping.")
            return jsonify(received=True), 200

        # Roblox accounts come from the accounts table
        if product_slug == "roblox":
            item = claim_account("roblox", customer_email, pi_id)
            if item:
                try:
                    send_key_email(customer_email, customer_name, item, product_name)
                    alert_low_stock("roblox")
                    print(f"[OK] Roblox account sold to {customer_email}")
                except Exception as e:
                    print(f"[EMAIL FAIL] acc={item} to={customer_email} err={e}")
            else:
                print(f"[CRITICAL] No roblox accounts left! PI={pi_id} buyer={customer_email}")
        else:
            # All other products come from the keys table
            key = claim_key(key_type, customer_email, customer_name, pi_id)
            if key:
                try:
                    send_key_email(customer_email, customer_name, key, product_name)
                    alert_low_stock(key_type)
                    print(f"[OK] {key} sold to {customer_email} ({product_name})")
                except Exception as e:
                    print(f"[EMAIL FAIL] key={key} to={customer_email} err={e}")
            else:
                print(f"[CRITICAL] No '{key_type}' keys left! PI={pi_id} buyer={customer_email}")

    return jsonify(received=True), 200


# ─────────────────────────────────────
# ORDER LOOKUP — success.html
# ─────────────────────────────────────
@app.route("/order/<payment_intent_id>", methods=["GET"])
def get_order(payment_intent_id):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT key, type, email FROM keys WHERE payment_intent_id=%s", (payment_intent_id,))
            row = cur.fetchone()
            if not row:
                cur.execute("SELECT account as key, type, buyer_email as email FROM accounts WHERE payment_intent_id=%s", (payment_intent_id,))
                row = cur.fetchone()

    if not row:
        return jsonify({"error": "Order not found"}), 404
    return jsonify({"key": row["key"], "type": row["type"], "email": row["email"]})


# ─────────────────────────────────────
# ADMIN — RESEND KEY EMAIL
# ─────────────────────────────────────
@app.route("/resend-key", methods=["POST"])
def resend_key():
    data  = request.json or {}
    pi_id = data.get("payment_intent_id", "").strip()
    if not pi_id:
        return jsonify({"error": "payment_intent_id required"}), 400

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT key, type, email, buyer_name FROM keys WHERE payment_intent_id=%s", (pi_id,))
            row = cur.fetchone()

    if not row:
        return jsonify({"error": "Order not found"}), 404

    product_name = next((p["name"] for p in PRODUCTS.values() if p["type"] == row["type"]), "Duskz License")

    try:
        send_key_email(row["email"], row["buyer_name"] or "Customer", row["key"], product_name)
        return jsonify({"success": True, "key": row["key"], "sent_to": row["email"]})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ═══════════════════════════════════════
# KEY ROUTES (admin panel)
# ═══════════════════════════════════════

@app.route("/keys", methods=["GET"])
def get_keys():
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM keys ORDER BY created DESC")
            return jsonify(cur.fetchall())


@app.route("/keys", methods=["POST"])
def create_key():
    data  = request.json or {}
    key   = data.get("key")
    type_ = data.get("type", "standard")

    if not key:
        return jsonify({"error": "Key required"}), 400

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO keys (id, key, type, status, hwid, email, buyer_name, payment_intent_id, created, sold_at)
                VALUES (%s,%s,%s,'unused',NULL,NULL,NULL,NULL,%s,NULL)
                ON CONFLICT (key) DO NOTHING
                RETURNING *
            """, (str(uuid.uuid4()), key, type_, datetime.utcnow()))
            inserted = cur.fetchone()
        conn.commit()

    if not inserted:
        return jsonify({"error": "Key already exists"}), 400
    return jsonify(inserted)


@app.route("/keys/bulk", methods=["POST"])
def bulk_create_keys():
    data  = request.json or {}
    count = min(int(data.get("count", 1)), 200)
    type_ = data.get("type", "standard")

    created = []
    with get_db() as conn:
        with conn.cursor() as cur:
            for _ in range(count):
                raw     = uuid.uuid4().hex.upper()
                new_key = f"{raw[0:6]}-{raw[6:12]}-{raw[12:18]}-{raw[18:24]}"
                cur.execute("""
                    INSERT INTO keys (id, key, type, status, hwid, email, buyer_name, payment_intent_id, created, sold_at)
                    VALUES (%s,%s,%s,'unused',NULL,NULL,NULL,NULL,%s,NULL)
                    ON CONFLICT (key) DO NOTHING
                    RETURNING *
                """, (str(uuid.uuid4()), new_key, type_, datetime.utcnow()))
                row = cur.fetchone()
                if row:
                    created.append(row)
        conn.commit()

    return jsonify({"created": len(created), "keys": created})


@app.route("/keys/<key>/reset-hwid", methods=["POST"])
def reset_hwid(key):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE keys SET hwid=NULL, status='sold' WHERE key=%s RETURNING *", (key,))
            updated = cur.fetchone()
        conn.commit()
    if not updated:
        return jsonify({"error": "Key not found"}), 404
    return jsonify(updated)


@app.route("/keys/<key>/ban", methods=["POST"])
def ban_key(key):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE keys SET status='banned' WHERE key=%s RETURNING *", (key,))
            updated = cur.fetchone()
        conn.commit()
    if not updated:
        return jsonify({"error": "Key not found"}), 404
    return jsonify(updated)


@app.route("/keys/<key>/unban", methods=["POST"])
def unban_key(key):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE keys SET status='unused' WHERE key=%s RETURNING *", (key,))
            updated = cur.fetchone()
        conn.commit()
    if not updated:
        return jsonify({"error": "Key not found"}), 404
    return jsonify(updated)


@app.route("/keys/<key>", methods=["DELETE"])
def delete_key(key):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM keys WHERE key=%s RETURNING *", (key,))
            deleted = cur.fetchone()
        conn.commit()
    if not deleted:
        return jsonify({"error": "Key not found"}), 404
    return jsonify({"success": True})


# ═══════════════════════════════════════
# ACCOUNT ROUTES (admin panel — no auth)
# ═══════════════════════════════════════

@app.route("/admin/accounts", methods=["GET"])
def get_accounts():
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM accounts ORDER BY created_at DESC")
            return jsonify(cur.fetchall())


@app.route("/admin/accounts/add", methods=["POST"])
def add_accounts():
    data  = request.json or {}
    lines = data.get("accounts", [])
    type_ = data.get("type", "roblox")
    added = 0
    with get_db() as conn:
        with conn.cursor() as cur:
            for line in lines:
                line = line.strip()
                if not line:
                    continue
                cur.execute("""
                    INSERT INTO accounts (id, account, type, claimed, status, created_at)
                    VALUES (%s, %s, %s, FALSE, 'unclaimed', %s)
                """, (str(uuid.uuid4()), line, type_, datetime.utcnow()))
                added += 1
        conn.commit()
    return jsonify({"added": added})


@app.route("/admin/accounts/<account_id>", methods=["DELETE"])
def delete_account(account_id):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM accounts WHERE id=%s RETURNING *", (account_id,))
            deleted = cur.fetchone()
        conn.commit()
    if not deleted:
        return jsonify({"error": "Account not found"}), 404
    return jsonify({"success": True})


# ═══════════════════════════════════════
# VALIDATE KEY (C++ loader)
# ═══════════════════════════════════════

@app.route("/validate", methods=["POST"])
def validate_key():
    data = request.json or {}
    key  = data.get("key")
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

    if record["status"] in ("sold", "unused") and record["hwid"] is None:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE keys SET hwid=%s, status='active' WHERE key=%s", (hwid, key))
            conn.commit()
        return jsonify({"valid": True, "type": record["type"]})

    if record["status"] == "active":
        if record["hwid"] != hwid:
            return jsonify({"valid": False, "error": "HWID mismatch"})
        return jsonify({"valid": True, "type": record["type"]})

    return jsonify({"valid": False, "error": "Key not eligible"})


# ─────────────────────────────────────
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
