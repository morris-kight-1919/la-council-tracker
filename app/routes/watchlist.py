import watch as pipeline_watch  # scripts/watch.py
from flask import Blueprint, current_app, flash, redirect, render_template, request, url_for

from .. import db as app_db
from ..services import live, seed

bp = Blueprint("watchlist", __name__)


@bp.route("/watchlist")
def watchlist():
    if current_app.config["SEED_MODE"]:
        files = seed.get_watchlist()
    else:
        files = live.get_watchlist_rows(app_db.get_db())
    return render_template("watchlist.html", files=files)


@bp.route("/watchlist/add", methods=["POST"])
def add():
    """Reuses scripts/watch.py's add() as-is -- the same function the CLI
    and the Budibase-era /api/watchlist route call -- so there's one
    implementation of "add a file to the watchlist", not several."""
    if current_app.config["SEED_MODE"]:
        flash("Demo mode (SEED_MODE=1) — nothing was saved. Set SEED_MODE=0 to use the real Watchlist.", "info")
        return redirect(url_for("watchlist.watchlist"))

    token = (request.form.get("cf") or "").strip()
    cf = pipeline_watch.parse_cf(token) if token else None
    if not cf:
        flash(f"Couldn't find a council file number in {token!r}. Try e.g. 26-0386 or a lms.lacity.gov/files/… link.", "error")
        return redirect(url_for("watchlist.watchlist"))

    ok = pipeline_watch.add(cf)
    if not ok:
        flash(f"{cf} not found — check the number. Nothing added.", "error")
    else:
        flash(f"Added {cf} to the watchlist.", "success")
    return redirect(url_for("watchlist.watchlist"))


@bp.route("/watchlist/<cf>/remove", methods=["POST"])
def remove(cf):
    if current_app.config["SEED_MODE"]:
        flash("Demo mode (SEED_MODE=1) — nothing was saved. Set SEED_MODE=0 to use the real Watchlist.", "info")
        return redirect(url_for("watchlist.watchlist"))

    removed = pipeline_watch.remove(cf.upper())
    flash(f"Stopped watching {cf.upper()}." if removed else f"{cf.upper()} wasn't being watched.",
          "success" if removed else "error")
    return redirect(url_for("watchlist.watchlist"))
