from flask import Flask, request, jsonify
from flask_cors import CORS
import os, uuid
from datetime import datetime
import psycopg2
from psycopg2.extras import RealDictCursor

app = Flask(__name__)
CORS(app)

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
                    created TIMESTAMP NOT NULL
                )
            """)
        conn.commit()

init_db()

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
        "created": datetime.utcnow()
    }

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO keys (id, key, type, status, hwid, created)
                VALUES (%s,%s,%s,%s,%s,%s)
                ON CONFLICT (key) DO NOTHING
                RETURNING *
            """, (
                record["id"],
                record["key"],
                record["type"],
                record["status"],
                record["hwid"],
                record["created"]
            ))
            inserted = cur.fetchone()
        conn.commit()

    if not inserted:
        return jsonify({"error": "Key already exists"}), 400

    return jsonify(inserted)

# ─────────────────────────────────────
# RESET HWID
# ─────────────────────────────────────
@app.route("/keys/<key>/reset-hwid", methods=["POST"])
def reset_hwid(key):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE keys
                SET hwid=NULL, status='unused'
                WHERE key=%s
                RETURNING *
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
                UPDATE keys
                SET status='banned'
                WHERE key=%s
                RETURNING *
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
                UPDATE keys
                SET status='unused'
                WHERE key=%s
                RETURNING *
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
                DELETE FROM keys
                WHERE key=%s
                RETURNING *
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

    if record["hwid"] is None:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE keys SET hwid=%s, status='active' WHERE key=%s",
                    (hwid, key)
                )
            conn.commit()
        return jsonify({"valid": True, "type": record["type"]})

    if record["hwid"] != hwid:
        return jsonify({"valid": False, "error": "HWID mismatch"})

    return jsonify({"valid": True, "type": record["type"]})

# ─────────────────────────────────────
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
