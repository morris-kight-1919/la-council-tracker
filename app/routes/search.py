from flask import Blueprint, current_app, render_template, request

from .. import db as app_db
from ..services import live

bp = Blueprint("search", __name__)


@bp.route("/search")
def search():
    query = request.args.get("q", "").strip()
    if current_app.config["SEED_MODE"] or not query:
        return render_template("search_results.html", query=query, results=[], member_matches=[])
    conn = app_db.get_db()
    results = live.search(conn, query)
    member_matches = live.search_members(conn, query, limit=5)
    return render_template("search_results.html", query=query, results=results, member_matches=member_matches)
