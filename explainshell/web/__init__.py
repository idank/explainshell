import logging
import os
import subprocess

from flask import Flask, current_app, g, jsonify
from explainshell import config, store

logger = logging.getLogger(__name__)


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


def get_distros() -> list[tuple[str, str]]:
    """Return the (distro, release) pairs snapshotted at app startup.

    The DB is read-only and baked into the Docker image in prod, so the
    snapshot is valid for the lifetime of the process. Rebuilding the
    DB in dev requires a server restart to pick up new distros.
    """
    return current_app.config["STARTUP_DISTROS"]


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
    # is exposed on /health for admin/smoke-test use but isn't part of
    # the validator — it would churn caches on routine DB refreshes
    # whose parsed rows for a given manpage are unchanged.
    project_root = os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    )
    git_sha = _get_git_sha(project_root)
    logger.info("git sha: %s", git_sha)
    # Truncate the hex prefix to 8 chars but preserve any suffix (e.g.
    # '-dirty') so a dirty deploy's ETag still differs from a clean one.
    hex_part, sep, tail = git_sha.partition("-")
    app.config["APP_VERSION"] = hex_part[:8] + (sep + tail if sep else "")

    # Snapshot the distro list at startup. Used by get_distros() and
    # /health — both are served from memory, no per-request DB work.
    # The DB is read-only and baked into the Docker image, so distros
    # only change when a new process boots.
    startup_distros: list[tuple[str, str]] = []
    db_path = app.config.get("DB_PATH")
    if db_path and os.path.isfile(db_path):
        boot_store = store.Store(db_path, read_only=True)
        try:
            startup_distros = list(boot_store.distros())
        finally:
            boot_store.close()
    app.config["STARTUP_DISTROS"] = startup_distros

    health_body = {
        "db_sha256": db_sha256,
        "app_version": app.config["APP_VERSION"],
        "distros": [{"distro": d, "release": r} for d, r in startup_distros],
    }

    @app.route("/health")
    def health():
        return jsonify(health_body)

    return app
