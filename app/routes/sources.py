from flask import Blueprint, current_app, render_template

from .. import db as app_db
from ..services import live, seed

bp = Blueprint("sources", __name__)


@bp.route("/sources")
def sources():
    if current_app.config["SEED_MODE"]:
        return render_template("sources.html", sources=seed.get_sources(),
                                total_docs=None, total_emails=None, shown=None)

    conn = app_db.get_db()
    items = live.get_sources(conn)
    total_docs, total_emails = live.source_counts(conn)
    return render_template("sources.html", sources=items,
                            total_docs=total_docs, total_emails=total_emails, shown=len(items))
