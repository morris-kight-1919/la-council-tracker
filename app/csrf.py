"""Minimal CSRF protection -- no Flask-WTF dependency. Uses Flask's own
signed session (via SECRET_KEY) to hold a per-session token; every
state-changing form includes it as a hidden field, checked on submit with
a constant-time comparison. This is the smallest thing that actually
protects the state-changing routes the spec requires CSRF on, without
adding a package for it.
"""
import hmac
import secrets

from flask import abort, request, session


def get_token():
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_urlsafe(32)
    return session["csrf_token"]


def check_token():
    submitted = request.form.get("csrf_token", "")
    expected = session.get("csrf_token", "")
    if not expected or not hmac.compare_digest(submitted, expected):
        abort(400, description="Missing or invalid CSRF token. Reload the page and try again.")


def init_app(app):
    app.jinja_env.globals["csrf_token"] = get_token

    @app.before_request
    def _csrf_protect():
        # The /api/* blueprint is a JSON REST API meant for a separate local
        # service (Budibase) to call directly, not a browser form submission
        # against this app's own session -- it has no session cookie to
        # forge a request with and sends a JSON body, not request.form, so
        # the session-token model this file implements doesn't apply to it.
        # Every other POST (the HTML forms) is still checked exactly as before.
        if request.method in ("POST", "DELETE", "PUT", "PATCH") and not request.path.startswith("/api/"):
            check_token()
