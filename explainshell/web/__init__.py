import time

from flask import Flask
from explainshell import config, store

app = Flask(__name__)
app.config.from_object(config)

# Shared store instance — sqlite in WAL mode is safe for concurrent readers.
app.store = store.Store(config.DB_PATH)

# Cache distros() result; refreshed at most every 5 minutes.
_distros_cache = None
_distros_cache_time = 0
_DISTROS_TTL = 300


def get_cached_distros():
    global _distros_cache, _distros_cache_time
    now = time.monotonic()
    if _distros_cache is None or now - _distros_cache_time > _DISTROS_TTL:
        _distros_cache = app.store.distros()
        _distros_cache_time = now
    return _distros_cache


# Import routes after app creation to avoid circular imports.
from explainshell.web import views as views  # noqa: E402,F401
