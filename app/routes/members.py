"""On-demand council-member vote lookup, scoped to watched files only --
not a full LA Council historical archive (explicit user scope, 2026-08-09).
No new sidebar entry by design; reached via the header search box or a
direct /members/<name> link, matching "as I need them."
"""
from flask import Blueprint, abort, render_template, request

from .. import db as app_db
from ..services import live

bp = Blueprint("members", __name__)


@bp.route("/members")
def index():
    query = request.args.get("q", "").strip()
    names = live.search_members(app_db.get_db(), query) if query else []
    return render_template("members.html", query=query, names=names)


@bp.route("/members/<name>")
def detail(name):
    record = live.get_member_record(app_db.get_db(), name)
    if record is None:
        abort(404)
    return render_template("member_detail.html", record=record)
