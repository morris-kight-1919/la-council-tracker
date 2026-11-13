from flask import Blueprint, current_app, flash, redirect, render_template, request, url_for

from .. import db as app_db
from ..services import live, seed

bp = Blueprint("inbox", __name__)

VALID_DECISIONS = {"confirmed", "rejected", "cfnumber_overridden", "deferred"}


@bp.route("/inbox")
def inbox():
    if current_app.config["SEED_MODE"]:
        items = seed.get_inbox_items()
    else:
        items = live.get_inbox_items(app_db.get_db())
    return render_template("inbox.html", items=items)


@bp.route("/inbox/<int:outbox_id>/decide", methods=["POST"])
def decide(outbox_id):
    if current_app.config["SEED_MODE"]:
        flash("Demo mode (SEED_MODE=1) — nothing was saved. Set SEED_MODE=0 to use the real Inbox.", "info")
        return redirect(url_for("inbox.inbox"))

    decision = request.form.get("decision", "")
    if decision not in VALID_DECISIONS:
        flash("Unrecognized action.", "error")
        return redirect(url_for("inbox.inbox"))

    cfnumber_override = (request.form.get("cfnumber_override") or "").strip().upper() or None
    note = (request.form.get("note") or "").strip() or None
    decided_by = request.form.get("decided_by") or None

    conn = app_db.get_db()
    live.write_decision(conn, outbox_id, decision, cfnumber_override, note, decided_by)

    labels = {"confirmed": "Confirmed", "rejected": "Marked not relevant",
              "cfnumber_overridden": "Reassigned to a different Council File", "deferred": "Deferred"}
    flash(f"{labels[decision]}.", "success")
    return redirect(url_for("inbox.inbox"))
