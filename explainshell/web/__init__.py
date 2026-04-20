import logging
import os
import subprocess
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


def _get_git_sha(project_root: str) -> str:
    """Short identifier for the currently-deployed code.

    In production, ``GIT_SHA`` is baked in by the Docker build (see the
    ``ARG GIT_SHA`` in the Dockerfile).  In a source checkout, falls
    back to ``git rev-parse HEAD``, appending ``-dirty`` if the working
    tree has uncommitted changes — matching ``git describe --dirty``.
    Returns ``"local"`` if neither works.
    """
    env_sha = os.environ.get("GIT_SHA", "").strip()
    if env_sha:
        # The -dirty suffix, if any, is added by whoever set GIT_SHA
        # (e.g. `make deploy-local`).  We just pass it through.
        return env_sha
    try:
        rev_parse = subprocess.run(
            ["git", "-C", project_root, "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
        if rev_parse.returncode != 0:
            return "local"
        sha = rev_parse.stdout.strip()
        status = subprocess.run(
            ["git", "-C", project_root, "status", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
        if status.returncode == 0 and status.stdout.strip():
            sha += "-dirty"
        return sha
    except (OSError, subprocess.SubprocessError):
        return "local"


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
    if os.path.isfile(sha_path):
        with open(sha_path) as f:
            db_sha256 = f.read().strip()
        logger.info("db sha256: %s", db_sha256)
    else:
        db_sha256 = "local"
    app.config["DB_SHA256"] = db_sha256

    # APP_VERSION captures the deployed code identity. Combined with
    # per-row parsed_sha256 at serve time, this forms the ETag so
    # caches invalidate on either axis (content or code). The DB sha
    # lives on /db for admin debugging but isn't part of the validator
    # — it would churn caches on routine DB refreshes whose parsed rows
    # for a given manpage are unchanged.
    project_root = os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    )
    git_sha = _get_git_sha(project_root)
    logger.info("git sha: %s", git_sha)
    # Truncate the hex prefix to 8 chars but preserve any suffix (e.g.
    # '-dirty') so a dirty deploy's ETag still differs from a clean one.
    hex_part, sep, tail = git_sha.partition("-")
    app.config["APP_VERSION"] = hex_part[:8] + (sep + tail if sep else "")

    @app.route("/db")
    def db_info():
        return db_sha256 + "\n", 200, {"Content-Type": "text/plain"}

    return app
