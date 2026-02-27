from flask import Flask, request, jsonify
from flask_cors import CORS
import json, os, uuid
from datetime import datetime

app = Flask(__name__)
CORS(app)  # Allow Netlify frontend to call this server

DB_FILE = "keys.json"

# ─── DB HELPERS ──────────────────────────────────────────────
def load_keys():
    if not os.path.exists(DB_FILE):
        return []
    with open(DB_FILE, "r") as f:
        return json.load(f)

def save_keys(keys):
    with open(DB_FILE, "w") as f:
        json.dump(keys, f, indent=2)

# ─── ROUTES ──────────────────────────────────────────────────

# GET all keys
@app.route("/keys", methods=["GET"])
def get_keys():
    return jsonify(load_keys())

# CREATE a key
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
        "created": datetime.utcnow().isoformat()
    }

    keys.insert(0, new_key)
    save_keys(keys)
    return jsonify(new_key), 201

# DELETE a key
@app.route("/keys/<key_str>", methods=["DELETE"])
def delete_key(key_str):
    keys = load_keys()
    new_keys = [k for k in keys if k["key"] != key_str]
    if len(new_keys) == len(keys):
        return jsonify({"error": "Key not found"}), 404
    save_keys(new_keys)
    return jsonify({"success": True})

# BAN a key
@app.route("/keys/<key_str>/ban", methods=["POST"])
def ban_key(key_str):
    keys = load_keys()
    for k in keys:
        if k["key"] == key_str:
            k["status"] = "banned"
            save_keys(keys)
            return jsonify(k)
    return jsonify({"error": "Key not found"}), 404

# UNBAN a key
@app.route("/keys/<key_str>/unban", methods=["POST"])
def unban_key(key_str):
    keys = load_keys()
    for k in keys:
        if k["key"] == key_str:
            k["status"] = "used" if k["hwid"] else "unused"
            save_keys(keys)
            return jsonify(k)
    return jsonify({"error": "Key not found"}), 404

# RESET HWID
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

# VALIDATE key (called by your C++ loader)
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

# ─── RUN ─────────────────────────────────────────────────────
if __name__ == "__main__":
    print("\n  ██████╗ ██╗   ██╗███████╗██╗  ██╗███████╗")
    print("  ██╔══██╗██║   ██║██╔════╝██║ ██╔╝╚══███╔╝")
    print("  ██║  ██║██║   ██║███████╗█████╔╝   ███╔╝ ")
    print("  ██║  ██║██║   ██║╚════██║██╔═██╗  ███╔╝  ")
    print("  ██████╔╝╚██████╔╝███████║██║  ██╗███████╗")
    print("  ╚═════╝  ╚═════╝ ╚══════╝╚═╝  ╚═╝╚══════╝")
    print("\n  Key Management Server v1.0")
    print("  Running on http://localhost:5000\n")
    app.run(host="0.0.0.0", port=5000, debug=False)
