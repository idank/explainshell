import os

_curr_dir = os.path.dirname(os.path.dirname(__file__))

# host to pass into Flask's app.run.
HOST_IP = os.getenv("HOST_IP", "")
DB_PATH = os.getenv("DB_PATH", os.path.join(_curr_dir, "explainshell.db"))
DEBUG = True
