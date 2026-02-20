from flask import Flask
from explainshell import config

app = Flask(__name__)
app.config.from_object(config)

if config.DEBUG:
    from explainshell.web import debug_views as debug_views  # noqa: E402,F401

# Import routes after app creation to avoid circular imports.
from explainshell.web import views as views  # noqa: E402,F401
