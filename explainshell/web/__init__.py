import time

from flask import Flask, current_app
from explainshell import config, store

# Cache distros() result; refreshed at most every 5 minutes.
_distros_cache = None
_distros_cache_time = 0
_DISTROS_TTL = 300


def get_cached_distros():
    global _distros_cache, _distros_cache_time
    now = time.monotonic()
    if _distros_cache is None or now - _distros_cache_time > _DISTROS_TTL:
        _distros_cache = current_app.store.distros()
        _distros_cache_time = now
    return _distros_cache


def create_app(db_path=None):
    """Application factory."""
    app = Flask(__name__)
    app.config.from_object(config)

    db = db_path or config.DB_PATH
    if db:
        app.store = store.Store(db, read_only=True)

    from explainshell.web.views import bp

    app.register_blueprint(bp)

    return app
