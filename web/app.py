import os
import socket
import threading
import time
import uuid
from collections import deque
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, session, jsonify
from flask_socketio import SocketIO, emit
from werkzeug.middleware.proxy_fix import ProxyFix
from core.client import GhostTCPClient
from core.server import GhostRelayServer
from core.database import (
    init_db,
    create_user,
    authenticate_user,
    get_user_by_id,
    get_user_by_username,
    update_user_profile,
    search_users,
    get_friends,
    is_friend,
    add_friend,
    remove_friend,
    create_anonymous_message,
    get_inbox_data,
    mark_message_read,
    mark_all_read,
    delete_message,
    set_message_reaction,
    report_message,
    ALLOWED_REACTIONS,
)
from ai.privacy_guard import PrivacyGuard

app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
app.secret_key = os.environ.get("GHOSTLINK_SECRET_KEY", os.urandom(24))
app.config["MAX_CONTENT_LENGTH"] = 4 * 1024 * 1024  # 4MB upload limit

AVATAR_DIR = os.path.join(app.root_path, "static", "avatars")
os.makedirs(AVATAR_DIR, exist_ok=True)
ALLOWED_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}

socketio = SocketIO(app, cors_allowed_origins="*", max_http_buffer_size=5 * 1024 * 1024)

init_db()


def ensure_tcp_relay_running():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        if s.connect_ex(("127.0.0.1", 9090)) != 0:
            relay = GhostRelayServer(host="127.0.0.1", port=9090)
            threading.Thread(target=relay.start, daemon=True).start()
            time.sleep(0.2)


ensure_tcp_relay_running()

active_sessions = {}
submission_buckets = {}
RATE_LIMIT_MAX = 8
RATE_LIMIT_WINDOW = 60


def check_rate_limit(bucket_key: str) -> bool:
    now = time.time()
    q = submission_buckets.setdefault(bucket_key, deque())
    while q and now - q[0] > RATE_LIMIT_WINDOW:
        q.popleft()
    if len(q) >= RATE_LIMIT_MAX:
        return False
    q.append(now)
    return True


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            if request.is_json or request.path.startswith("/api/"):
                return jsonify({"ok": False, "error": "Authentication required."}), 401
            return redirect(url_for("auth_page"))
        user = get_user_by_id(session["user_id"])
        if not user:
            session.clear()
            return redirect(url_for("auth_page"))
        return f(user, *args, **kwargs)
    return decorated


def current_user_or_none():
    if "user_id" in session:
        return get_user_by_id(session["user_id"])
    return None


# -------------------- PUBLIC & AUTH ROUTES --------------------

@app.route("/")
def landing():
    user = current_user_or_none()
    featured = search_users("", exclude_user_id=user["id"] if user else None, limit=6)
    return render_template("landing.html", user=user, featured_users=featured)


@app.route("/auth", methods=["GET", "POST"])
def auth_page():
    if current_user_or_none():
        return redirect(url_for("dashboard"))

    error = None
    mode = request.args.get("mode", "login")

    if request.method == "POST":
        action = request.form.get("action", "login")
        mode = action
        username = request.form.get("username", "")
        password = request.form.get("password", "")

        if action == "register":
            display_name = request.form.get("display_name", "")
            ok, result = create_user(username, display_name, password)
            if ok:
                session["user_id"] = result
                return redirect(url_for("dashboard"))
            error = result
        else:
            user = authenticate_user(username, password)
            if user:
                session["user_id"] = user["id"]
                return redirect(url_for("dashboard"))
            error = "Invalid username or password."

    return render_template("auth.html", error=error, mode=mode)


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("landing"))


# -------------------- FIND USERS & FRIENDS --------------------

@app.route("/explore")
def explore_page():
    user = current_user_or_none()
    q = request.args.get("q", "").strip()
    results = search_users(q, exclude_user_id=user["id"] if user else None, limit=30)
    friend_ids = {f["id"] for f in get_friends(user["id"])} if user else set()
    return render_template("explore.html", user=user, results=results, friend_ids=friend_ids, query=q)


@app.route("/api/users/search")
def api_search_users():
    user = current_user_or_none()
    q = request.args.get("q", "").strip()
    results = search_users(q, exclude_user_id=user["id"] if user else None, limit=25)
    friend_ids = {f["id"] for f in get_friends(user["id"])} if user else set()
    for r in results:
        r["is_friend"] = r["id"] in friend_ids
    return jsonify({"ok": True, "users": results})


@app.route("/api/friends/add", methods=["POST"])
@login_required
def api_add_friend(user):
    data = request.get_json(silent=True) or {}
    target = str(data.get("username", "")).strip()
    ok, msg = add_friend(user["id"], target)
    if not ok:
        return jsonify({"ok": False, "error": msg}), 400
    return jsonify({"ok": True, "message": msg, "friends": get_friends(user["id"])})


@app.route("/api/friends/remove", methods=["POST"])
@login_required
def api_remove_friend(user):
    data = request.get_json(silent=True) or {}
    target = str(data.get("username", "")).strip()
    ok, msg = remove_friend(user["id"], target)
    if not ok:
        return jsonify({"ok": False, "error": msg}), 400
    return jsonify({"ok": True, "message": msg, "friends": get_friends(user["id"])})


# -------------------- PERSONAL GHOSTLINK PUBLIC PROFILE --------------------

@app.route("/u/<username>")
def public_profile(username):
    viewer = current_user_or_none()
    profile = get_user_by_username(username)
    if not profile:
        return render_template("public_profile.html", profile=None, viewer=viewer, already_friend=False), 404
    already_friend = is_friend(viewer["id"], profile["id"]) if viewer else False
    return render_template("public_profile.html", profile=profile, viewer=viewer, already_friend=already_friend)


@app.route("/api/u/<username>/send", methods=["POST"])
def submit_anonymous_message(username):
    profile = get_user_by_username(username)
    if not profile:
        return jsonify({"ok": False, "error": "GhostLink user not found."}), 404

    if not check_rate_limit(f"u:{profile['id']}"):
        return jsonify({"ok": False, "error": "Too many messages sent recently. Please wait a minute."}), 429

    data = request.get_json(silent=True) or {}
    content = str(data.get("content", "")).strip()

    if not content:
        return jsonify({"ok": False, "error": "Please write a message before sending."}), 400
    if len(content) > 500:
        return jsonify({"ok": False, "error": "Message must be 500 characters or fewer."}), 400

    safe, warning = PrivacyGuard.inspect(content)
    if not safe:
        return jsonify({"ok": False, "error": warning}), 400

    ok, msg = create_anonymous_message(profile["id"], content)
    if not ok:
        return jsonify({"ok": False, "error": msg}), 400

    return jsonify({"ok": True, "message": "Sent anonymously! Your identity was not recorded."})


# -------------------- AUTHENTICATED DASHBOARD & PROFILE --------------------

@app.route("/dashboard")
@login_required
def dashboard(user):
    inbox = get_inbox_data(user["id"])
    friends = get_friends(user["id"])
    public_url = url_for("public_profile", username=user["username"], _external=True)
    return render_template(
        "dashboard.html",
        user=user,
        messages=inbox["messages"],
        stats=inbox["stats"],
        friends=friends,
        public_url=public_url,
        reactions=sorted(ALLOWED_REACTIONS),
    )


@app.route("/api/messages/<int:message_id>/read", methods=["POST"])
@login_required
def api_mark_read(user, message_id):
    ok = mark_message_read(user["id"], message_id)
    if not ok:
        return jsonify({"ok": False, "error": "Message not found."}), 404
    return jsonify({"ok": True, "stats": get_inbox_data(user["id"])["stats"]})


@app.route("/api/messages/read-all", methods=["POST"])
@login_required
def api_mark_all_read(user):
    mark_all_read(user["id"])
    return jsonify({"ok": True, "stats": get_inbox_data(user["id"])["stats"]})


@app.route("/api/messages/<int:message_id>", methods=["DELETE"])
@login_required
def api_delete_message(user, message_id):
    ok = delete_message(user["id"], message_id)
    if not ok:
        return jsonify({"ok": False, "error": "Message not found or unauthorized."}), 404
    return jsonify({"ok": True, "stats": get_inbox_data(user["id"])["stats"]})


@app.route("/api/messages/<int:message_id>/react", methods=["POST"])
@login_required
def api_react_message(user, message_id):
    data = request.get_json(silent=True) or {}
    emoji = str(data.get("emoji", ""))
    ok, result = set_message_reaction(user["id"], message_id, emoji)
    if not ok:
        return jsonify({"ok": False, "error": result}), 400
    return jsonify({"ok": True, "reaction": result, "stats": get_inbox_data(user["id"])["stats"]})


@app.route("/api/messages/<int:message_id>/report", methods=["POST"])
@login_required
def api_report_message(user, message_id):
    ok = report_message(user["id"], message_id)
    if not ok:
        return jsonify({"ok": False, "error": "Message not found."}), 404
    return jsonify({"ok": True, "stats": get_inbox_data(user["id"])["stats"]})


@app.route("/api/profile", methods=["POST"])
@login_required
def api_update_profile(user):
    data = request.get_json(silent=True) or {}
    ok, msg = update_user_profile(
        user_id=user["id"],
        new_username=str(data.get("username", user["username"])),
        display_name=str(data.get("display_name", user["display_name"])),
        bio=str(data.get("bio", user["bio"])),
        theme=str(data.get("theme", user["theme"])),
    )
    if not ok:
        return jsonify({"ok": False, "error": msg}), 400
    updated = get_user_by_id(user["id"])
    new_url = url_for("public_profile", username=updated["username"], _external=True)
    return jsonify({"ok": True, "message": msg, "user": updated, "public_url": new_url})


@app.route("/api/profile/avatar", methods=["POST"])
@login_required
def api_upload_avatar(user):
    if "avatar" not in request.files:
        return jsonify({"ok": False, "error": "No image file selected."}), 400
    file = request.files["avatar"]
    if not file.filename:
        return jsonify({"ok": False, "error": "Invalid filename."}), 400

    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in ALLOWED_IMAGE_EXTS:
        return jsonify({"ok": False, "error": "Allowed formats: PNG, JPG, WEBP, GIF."}), 400

    if user.get("avatar_url") and user["avatar_url"].startswith("/static/avatars/"):
        old_path = os.path.join(app.root_path, user["avatar_url"].lstrip("/"))
        if os.path.exists(old_path):
            try:
                os.remove(old_path)
            except OSError:
                pass

    filename = f"u{user['id']}_{uuid.uuid4().hex[:10]}{ext}"
    save_path = os.path.join(AVATAR_DIR, filename)
    file.save(save_path)

    avatar_url = f"/static/avatars/{filename}"
    update_user_profile(
        user_id=user["id"],
        new_username=user["username"],
        display_name=user["display_name"],
        bio=user["bio"],
        theme=user["theme"],
        avatar_url=avatar_url,
    )
    return jsonify({"ok": True, "avatar_url": avatar_url, "message": "Profile photo updated!"})


# -------------------- LIVE EPHEMERAL TCP SOCKET ROOM --------------------

@app.route("/live")
def live_room():
    return render_template("index.html")


@socketio.on("join_room_tcp")
def handle_join(data):
    sid = request.sid
    alias = data.get("alias", "anon_ghost")
    room = data.get("room")
    secret = data.get("secret", room)

    if sid in active_sessions:
        active_sessions[sid].disconnect()

    def forward_to_browser(msg_obj):
        socketio.emit("tcp_packet", msg_obj, to=sid)

    client = GhostTCPClient(alias=alias, room=room, secret_key=secret, on_message_callback=forward_to_browser)
    active_sessions[sid] = client


@socketio.on("send_message_tcp")
def handle_message(data):
    sid = request.sid
    client = active_sessions.get(sid)
    if not client:
        return
    result = client.send_chat(data.get("text", ""), ttl=data.get("ttl", 0))
    if result["status"] in ("blocked", "bot"):
        emit("tcp_packet", {
            "type": result["status"],
            "sender": "AI_GUARD" if result["status"] == "blocked" else "GHOSTBOT",
            "text": result["message"],
        })


@socketio.on("send_file_tcp")
def handle_file(data):
    sid = request.sid
    client = active_sessions.get(sid)
    if not client:
        return
    client.send_file(
        filename=data.get("filename", "secret.bin"),
        mime_type=data.get("mime_type", "application/octet-stream"),
        base64_data=data.get("data_url", ""),
        ttl=data.get("ttl", 0),
    )


@socketio.on("typing_tcp")
def handle_typing():
    sid = request.sid
    client = active_sessions.get(sid)
    if client:
        client.send_typing()


@socketio.on("disconnect")
def handle_disconnect():
    sid = request.sid
    if sid in active_sessions:
        active_sessions[sid].disconnect()
        del active_sessions[sid]


if __name__ == "__main__":
    socketio.run(app, host="0.0.0.0", port=5000, debug=False)