import os

from flask import Flask

from .config import Config


def create_app():
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_object(Config)
    os.makedirs(app.instance_path, exist_ok=True)

    from . import auth
    auth.init_app(app)

    from . import routes
    routes.register(app)

    from . import db
    app.teardown_appcontext(db.close_db)

    from . import csrf
    csrf.init_app(app)

    return app
