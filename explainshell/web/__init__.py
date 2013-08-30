from flask import Flask
app = Flask(__name__)

from explainshell.web import views
from explainshell import store, config

if config.DEBUG:
    from explainshell.web import debugviews

app.config.from_object(config)
