"""App configuration and .env loading.

No python-dotenv dependency -- this project has zero third-party
dependencies outside Flask itself by deliberate choice, so .env is read
with the same small hand-rolled parser scripts/email_ingest.py already
uses for the pipeline's own .env.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(HERE, ".."))
SCRIPTS_DIR = os.path.join(PROJECT_ROOT, "scripts")

# Makes the existing pipeline's modules (stages.py, db.py, ...) importable
# without duplicating any of their logic. Set here, at the first thing the
# app factory imports, so import order elsewhere never matters.
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)


def load_env(path=None):
    path = path or os.path.join(PROJECT_ROOT, ".env")
    env = {}
    if os.path.exists(path):
        for line in open(path):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    return env


_ENV = load_env()


def _get(key, default=None):
    return os.environ.get(key) or _ENV.get(key, default)


class Config:
    SECRET_KEY = _get("FLASK_SECRET_KEY", "dev-only-change-me")
    SEED_MODE = _get("SEED_MODE", "0") == "1"
    PORT = int(_get("PORT", "5055"))
    HOST = "127.0.0.1"  # local-only by design -- never 0.0.0.0
    REAL_DB_PATH = os.path.join(PROJECT_ROOT, "corpus.db")
