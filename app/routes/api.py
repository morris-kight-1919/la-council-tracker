"""JSON API for the Budibase GUI -- purely additive, reuses the same
app/services/live.py functions the HTML routes already call, and the same
scripts/watch.py add()/remove() the CLI already calls. Nothing here
duplicates query or write logic; it's a thin JSON wrapper so the real
database stays SQLite (corpus.db) and Budibase talks to it over REST
instead of needing a native SQLite datasource (which it doesn't have).

Every route lives under /api so it can't collide with the existing HTML
routes, and every response is JSON -- this blueprint renders no templates.
"""
import watch as pipeline_watch  # scripts/watch.py
from flask import Blueprint, current_app, jsonify, request

from .. import db as app_db
from ..services import live, seed

bp = Blueprint("api", __name__, url_prefix="/api")


def _jsonable(obj):
    """live.py mixes plain dicts/lists with raw sqlite3.Row objects (the HTML
    routes consume Rows directly via Jinja's attribute access, which is fine
    for templates but not for json.dumps). Recursively convert anything
    Row-shaped to a plain dict so every /api/* response is safely
    serializable, regardless of which service function it came from."""
    if isinstance(obj, dict):
        return {k: _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonable(v) for v in obj]
    if hasattr(obj, "keys") and not isinstance(obj, (str, bytes)):
        # sqlite3.Row supports mapping-style access and keys()
        return {k: _jsonable(obj[k]) for k in obj.keys()}
    return obj


def _seed_or(conn_fn, live_fn, seed_fn):
    if current_app.config["SEED_MODE"]:
        return seed_fn()
    return live_fn(conn_fn())


@bp.route("/watchlist", methods=["GET"])
def watchlist_list():
    """Screen 1: Files I'm Watching."""
    rows = _seed_or(app_db.get_db, live.get_watchlist_rows, seed.get_watchlist)
    return jsonify(_jsonable(rows))


@bp.route("/watchlist", methods=["POST"])
def watchlist_add():
    """Screen 2: Add a File. Body: {"cf": "<council file number or URL>"}.
    Wraps scripts/watch.py's add() as-is (shared with the CLI) rather than
    reimplementing it -- that function does real network I/O (fetches the
    live record, downloads any new documents) and writes watchlist.json
    itself, so this route just calls it and reports what's there after."""
    if current_app.config["SEED_MODE"]:
        return jsonify({"error": "SEED_MODE is on; no writes in demo mode"}), 400
    body = request.get_json(silent=True) or {}
    token = (body.get("cf") or "").strip()
    if not token:
        return jsonify({"error": "missing 'cf' (a council file number or viewrecord URL)"}), 400
    cf = pipeline_watch.parse_cf(token)
    if not cf:
        return jsonify({"error": f"couldn't parse a council file number out of {token!r}"}), 400
    ok = pipeline_watch.add(cf)
    if not ok:
        return jsonify({"error": f"{cf} not found -- check the number/URL. Nothing added."}), 404
    wl = pipeline_watch.load_watchlist()
    return jsonify({"cf": cf, "entry": wl.get(cf)}), 201


@bp.route("/watchlist/<cf>", methods=["DELETE"])
def watchlist_remove(cf):
    if current_app.config["SEED_MODE"]:
        return jsonify({"error": "SEED_MODE is on; no writes in demo mode"}), 400
    removed = pipeline_watch.remove(cf.upper())
    if not removed:
        return jsonify({"error": f"{cf} wasn't being watched"}), 404
    return jsonify({"removed": cf.upper()})


@bp.route("/calendar", methods=["GET"])
def calendar_events():
    """Screen 3: Calendar of committee/commission meetings on watched files."""
    if current_app.config["SEED_MODE"]:
        return jsonify([])
    events = live.get_calendar_events(app_db.get_db())
    return jsonify(_jsonable(events))


@bp.route("/search", methods=["GET"])
def search():
    """Screen 4: Search across ITA/LMS notification emails + documents.
    ?q=<query>"""
    query = request.args.get("q", "").strip()
    if current_app.config["SEED_MODE"] or not query:
        return jsonify({"query": query, "results": [], "member_matches": []})
    conn = app_db.get_db()
    results = live.search(conn, query)
    member_matches = live.search_members(conn, query, limit=5)
    return jsonify(_jsonable({"query": query, "results": results, "member_matches": member_matches}))


@bp.route("/council_files/<cf>", methods=["GET"])
def council_file_detail(cf):
    """Detail view for a single watched file -- timeline, documents, comments."""
    if current_app.config["SEED_MODE"]:
        return jsonify({"error": "SEED_MODE is on"}), 400
    conn = app_db.get_db()
    cf = cf.upper()
    record = live.get_council_file(conn, cf)
    if record is None:
        return jsonify({"error": f"{cf} not found"}), 404
    return jsonify(_jsonable({
        "council_file": record,
        "timeline": live.get_timeline(conn, cf),
        "documents": live.get_documents(conn, cf),
        "comments": live.get_comments(conn, cf),
    }))
