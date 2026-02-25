from flask import Flask
from explainshell import config

app = Flask(__name__)
app.config.from_object(config)

# Import routes after app creation to avoid circular imports.
from explainshell.web import views as views  # noqa: E402,F401
