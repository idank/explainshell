import logging
import os
import time

from flask import Flask, current_app, g
from explainshell import config, store
from explainshell.logger.logging_interceptor import InterceptHandler

# Cache distros() result; refreshed at most every 5 minutes.
_distros_cache = None
_distros_cache_time = 0
_DISTROS_TTL = 300


def _parse_debug(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    return value.lower() not in ("0", "false", "no")


def _configure_web_logging(log_level: str) -> None:
    app_logger = logging.getLogger("explainshell")
    if not any(
        isinstance(handler, InterceptHandler) for handler in app_logger.handlers
    ):
        app_logger.addHandler(InterceptHandler())

    level = getattr(logging, log_level.upper(), logging.INFO)
    app_logger.setLevel(level)
    app_logger.propagate = False


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


def create_app(
    db_path: str | None = None,
    debug: bool | None = None,
    log_level: str | None = None,
):
    """Application factory."""
    _configure_web_logging(log_level or os.getenv("LOG_LEVEL", "INFO"))

    app = Flask(__name__)
    app.config.from_object(config)

    app.debug = (
        debug if debug is not None else _parse_debug(os.getenv("DEBUG"), config.DEBUG)
    )

    db_path = db_path or os.getenv("DB_PATH") or config.DB_PATH
    if db_path:
        app.config["DB_PATH"] = db_path

    from explainshell.web.views import bp, debug_bp

    app.register_blueprint(bp)
    if app.debug:
        app.register_blueprint(debug_bp)

    @app.teardown_appcontext
    def close_store(exc: BaseException | None) -> None:
        s = g.pop("store", None)
        if s is not None:
            s.close()

    return app
