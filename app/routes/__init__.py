from flask import render_template


def register(app):
    from . import activity, api, calendar, council_files, inbox, members, rules, search, sources, stubs, today, watchlist

    app.register_blueprint(today.bp)
    app.register_blueprint(api.bp)
    app.register_blueprint(inbox.bp)
    app.register_blueprint(watchlist.bp)
    app.register_blueprint(council_files.bp)
    app.register_blueprint(calendar.bp)
    app.register_blueprint(sources.bp)
    app.register_blueprint(rules.bp)
    app.register_blueprint(activity.bp)
    app.register_blueprint(search.bp)
    app.register_blueprint(members.bp)
    app.register_blueprint(stubs.bp)

    @app.errorhandler(404)
    def not_found(_e):
        return render_template("404.html"), 404
