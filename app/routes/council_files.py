from flask import Blueprint, abort, current_app, flash, redirect, render_template, url_for

import watch as pipeline_watch  # scripts/watch.py

from .. import db as app_db
from ..services import live, seed

bp = Blueprint("council_files", __name__)


@bp.route("/council-files/<cfnumber>")
def detail(cfnumber):
    if current_app.config["SEED_MODE"]:
        record = seed.get_council_file(cfnumber)
        if record is None:
            abort(404)
        return render_template(
            "council_file_detail.html",
            record=record,
            timeline=seed.get_timeline(cfnumber),
            documents=seed.get_documents(cfnumber),
            comments=seed.get_comments(cfnumber),
        )

    conn = app_db.get_db()
    record = live.get_council_file(conn, cfnumber)
    if record is None:
        abort(404)
    return render_template(
        "council_file_detail.html",
        record=record,
        timeline=live.get_timeline(conn, cfnumber),
        documents=live.get_documents(conn, cfnumber),
        comments=live.get_comments(conn, cfnumber),
    )


@bp.route("/council-files/<cfnumber>/watch", methods=["POST"])
def toggle_watch(cfnumber):
    if current_app.config["SEED_MODE"]:
        flash("Demo mode (SEED_MODE=1) — nothing was saved.", "info")
        return redirect(url_for("council_files.detail", cfnumber=cfnumber))

    watchlist = pipeline_watch.load_watchlist()
    if cfnumber in watchlist:
        pipeline_watch.remove(cfnumber)
        flash(f"Stopped watching CF {cfnumber}. Its documents and text stay in the corpus.", "success")
    else:
        # Fetches the record, archives its documents, and saves a baseline
        # snapshot -- can take a few seconds for a file with many documents.
        ok = pipeline_watch.add(cfnumber)
        if ok:
            flash(f"Now watching CF {cfnumber}.", "success")
        else:
            flash(f"Couldn't find CF {cfnumber} — check the number.", "error")
    return redirect(url_for("council_files.detail", cfnumber=cfnumber))
