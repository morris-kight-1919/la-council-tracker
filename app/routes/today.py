import datetime

from flask import Blueprint, current_app, flash, redirect, render_template, request, url_for

from .. import db as app_db
from ..services import live, seed

bp = Blueprint("today", __name__)


def _parse_meeting_date(s):
    try:
        return datetime.datetime.strptime(s, "%b %d, %Y").date()
    except (TypeError, ValueError):
        return None


@bp.route("/")
@bp.route("/today")
def today():
    if current_app.config["SEED_MODE"]:
        inbox_items = seed.get_inbox_items()
        hearings = seed.get_upcoming_hearings()
        deadlines = seed.get_comment_deadlines()
        problems = seed.get_source_problems()
        changes = seed.get_recent_changes()
        needs_review = len(inbox_items)
    else:
        conn = app_db.get_db()
        inbox_items = live.get_inbox_items(conn, limit=25)
        today_date = datetime.date.today()
        hearings = []
        for e in live.get_calendar_events(conn):
            if e["kind"] != "Officially scheduled":
                continue
            d = _parse_meeting_date(e["date"])
            if d and d >= today_date:
                hearings.append({
                    "id": e["id"], "cfnumber": e["cfnumber"], "title": e["detail"] or e["title"],
                    "meeting_title": e["title"], "meeting_date": e["date"],
                    "official_url": e["source_url"],
                })
        deadlines = []  # no comment-period-deadline extraction exists yet -- explicit absence
        problems = [{"script": e["script"], "message": e.get("error", "unknown error"),
                     "timestamp": e["timestamp"]} for e in live.get_recent_errors(hours=48)]
        changes = live.get_recent_changes(conn, hours=24)
        needs_review = live.needs_review_count(conn)

    action_queue = []
    for item in inbox_items[:10]:
        action_queue.append({
            "id": item["id"],
            "kind": "keyword_hit",
            "when": item["received_at"],
            "summary": (f"Possible match: “{item['excerpt_match'] or item['source_title']}” "
                        f"may relate to CF {item['proposed_cfnumber']}.") if item.get("proposed_cfnumber")
                       else f"Possible match: {item['source_title']} (no watched file yet).",
            "detail": item["why_matched"],
            "cfnumber": item.get("proposed_cfnumber"),
            "open_url": f"/council-files/{item['proposed_cfnumber']}" if item.get("proposed_cfnumber") else "/inbox",
        })
    for h in hearings[:10]:
        action_queue.append({
            "id": h.get("id"),
            "kind": "hearing_alert",
            "when": h["meeting_date"],
            "summary": f"CF {h['cfnumber']} is on the agenda for {h['meeting_title']} on {h['meeting_date']}.",
            "detail": None,
            "cfnumber": h["cfnumber"],
            "open_url": f"/council-files/{h['cfnumber']}",
        })
    for c in changes[:10]:
        action_queue.append({
            "id": c.get("id"),
            "kind": "stage_change",
            "when": c["when"],
            "summary": f"CF {c['cfnumber']}: {c['summary']}",
            "detail": None,
            "cfnumber": c["cfnumber"],
            "open_url": f"/council-files/{c['cfnumber']}",
        })
    action_queue.sort(key=lambda a: a["when"] or "", reverse=True)

    return render_template(
        "today.html",
        needs_review_count=needs_review,
        hearings=hearings,
        deadlines=deadlines,
        problems=problems,
        action_queue=action_queue,
    )


@bp.route("/today/<int:outbox_id>/ack", methods=["POST"])
def ack(outbox_id):
    if current_app.config["SEED_MODE"]:
        flash("Demo mode (SEED_MODE=1) — nothing was saved.", "info")
        return redirect(url_for("today.today"))

    decision = request.form.get("decision", "")
    if decision not in ("dismissed", "snoozed"):
        flash("Unrecognized action.", "error")
        return redirect(url_for("today.today"))

    live.write_decision(app_db.get_db(), outbox_id, decision,
                         decided_by=request.form.get("decided_by") or None)
    flash("Dismissed." if decision == "dismissed" else "Snoozed — it'll come back later.", "success")
    return redirect(url_for("today.today"))
