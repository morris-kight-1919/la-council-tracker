from flask import Blueprint, current_app, render_template

from .. import db as app_db
from ..services import live

bp = Blueprint("activity", __name__)


@bp.route("/activity")
def activity():
    if current_app.config["SEED_MODE"]:
        run_log_entries, decisions = [], []
    else:
        run_log_entries = live.get_activity_log(limit=60)
        decisions = live.get_review_decisions(app_db.get_db())
    return render_template("activity.html", run_log_entries=run_log_entries, decisions=decisions)
