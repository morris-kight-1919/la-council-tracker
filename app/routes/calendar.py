from flask import Blueprint, current_app, render_template

from .. import db as app_db
from ..services import live

bp = Blueprint("calendar", __name__)


@bp.route("/calendar")
def calendar():
    if current_app.config["SEED_MODE"]:
        events = []
    else:
        events = live.get_calendar_events(app_db.get_db())
    return render_template("calendar.html", events=events)
