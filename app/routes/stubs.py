"""Genuinely-unbuilt pages -- a Council-Files browse/search-all page, and
Settings. Rendered explicitly as "coming soon" rather than 404ing."""
from flask import Blueprint, render_template

bp = Blueprint("stubs", __name__)

PAGES = {
    "council_files": ("Council Files", "Search and browse every Council File the pipeline knows about. "
                                        "For now, see Watchlist for files you're actively tracking."),
    "settings": ("Settings", "Local preferences for this console."),
}


@bp.route("/council-files")
def council_files_list():
    title, description = PAGES["council_files"]
    return render_template("stub.html", page_title=title, page_description=description)


@bp.route("/settings")
def settings():
    title, description = PAGES["settings"]
    return render_template("stub.html", page_title=title, page_description=description)
