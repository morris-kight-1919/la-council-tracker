"""Database access for the real-data pass (not yet used by any route while
SEED_MODE is on -- routes currently read app/services/seed.py instead).
Reads the pipeline's existing corpus.db directly rather than a second
database, via scripts/db.py's own connect(), so nothing here can drift out
of sync with the real pipeline schema.
"""
from flask import current_app, g

import db as pipeline_db  # scripts/db.py, importable via sys.path (see config.py)


def get_db():
    if "db" not in g:
        path = current_app.config["REAL_DB_PATH"]
        g.db = pipeline_db.connect(path)
    return g.db


def close_db(_exception=None):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()
