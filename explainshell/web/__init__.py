import logging
import os
import time

from flask import Flask, current_app, g
from explainshell import config, store

logger = logging.getLogger(__name__)

# Cache distros() result; refreshed at most every 5 minutes.
_distros_cache = None
_distros_cache_time = 0
_DISTROS_TTL = 300


def get_store() -> store.Store:
    """Return a per-request read-only Store, creating one if needed."""
    if "store" not in g:
        g.store = store.Store(current_app.config["DB_PATH"], read_only=True)
    return g.store


def get_cached_distros():
    global _distros_cache, _distros_cache_time
    now = time.monotonic()
    if _distros_cache is None or now - _distros_cache_time > _DISTROS_TTL:
        _distros_cache = get_store().distros()
        _distros_cache_time = now
    return _distros_cache


def create_app(db_path=None):
    """Application factory."""
    app = Flask(__name__)
    app.config.from_object(config)

    db = db_path or config.DB_PATH
    if db:
        app.config["DB_PATH"] = db

    from explainshell.web.views import bp, debug_bp

    app.register_blueprint(bp)
    if config.DEBUG:
        app.register_blueprint(debug_bp)

    @app.teardown_appcontext
    def close_store(exc: BaseException | None) -> None:
        s = g.pop("store", None)
        if s is not None:
            s.close()

    # Read the DB SHA256 once at startup. The file is computed at Docker
    # build time (see Dockerfile); it won't exist in dev unless created
    # manually.
    sha_path = (app.config.get("DB_PATH") or "") + ".sha256"
    db_sha256 = ""
    if os.path.isfile(sha_path):
        with open(sha_path) as f:
            db_sha256 = f.read().strip()
        logger.info("db sha256: %s", db_sha256)

    @app.route("/db")
    def db_info():
        return db_sha256 + "\n", 200, {"Content-Type": "text/plain"}

    return app
