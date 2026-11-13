"""Minimal login -- no Flask-Login dependency, same hand-rolled approach as
csrf.py. Stdlib-only PBKDF2 password hashing, Flask's own signed session to
remember who's logged in, and a before_request gate that locks every page
except the login form itself and static assets.

Users live in `auth_users.json` at the project root (gitignored, never
committed) -- not in this file, not in .env. Add or reset one with
`scripts/add_user.py`.
"""
import hashlib
import hmac
import json
import os
import secrets

from flask import Blueprint, current_app, flash, redirect, render_template, request, session, url_for

PBKDF2_ITERATIONS = 200_000

bp = Blueprint("auth", __name__)

# Endpoints reachable without being logged in. Keep this list short and
# explicit -- anything not on it is locked by the before_request hook below.
_EXEMPT_ENDPOINTS = {"auth.login", "static"}


def users_path():
    from .config import Config
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "auth_users.json")


def _load_users():
    path = users_path()
    if not os.path.exists(path):
        return {}
    with open(path) as f:
        return json.load(f)


def _save_users(users):
    path = users_path()
    with open(path, "w") as f:
        json.dump(users, f, indent=2)
    os.chmod(path, 0o600)


def hash_password(password, salt=None):
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), PBKDF2_ITERATIONS)
    return f"{salt}${digest.hex()}"


def verify_password(password, stored):
    salt, _, expected_hex = stored.partition("$")
    if not salt or not expected_hex:
        return False
    candidate = hash_password(password, salt)
    _, _, candidate_hex = candidate.partition("$")
    return hmac.compare_digest(candidate_hex, expected_hex)


def set_user_password(username, password):
    """Used by scripts/add_user.py. Not exposed via any route."""
    users = _load_users()
    users[username] = hash_password(password)
    _save_users(users)


@bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        users = _load_users()
        stored = users.get(username)
        # Constant-shape check even for an unknown username, so the
        # response doesn't reveal which usernames exist by timing/branching.
        ok = verify_password(password, stored) if stored else verify_password(password, hash_password(""))
        if stored and ok:
            session.clear()
            session["user"] = username
            next_url = request.args.get("next")
            return redirect(next_url if next_url and next_url.startswith("/") else url_for("today.today"))
        flash("Wrong username or password.", "error")
    return render_template("login.html")


@bp.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("auth.login"))


def init_app(app):
    app.register_blueprint(bp)

    @app.before_request
    def _require_login():
        if request.endpoint in _EXEMPT_ENDPOINTS or request.endpoint is None:
            return None
        if "user" not in session:
            return redirect(url_for("auth.login", next=request.path))

    @app.context_processor
    def _inject_user():
        return {"current_user": session.get("user")}
