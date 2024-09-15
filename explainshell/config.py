import os

_curr_dir = os.path.dirname(os.path.dirname(__file__))

MAN_PAGE_DIR = os.path.join(_curr_dir, "manpages")
CLASSIFIER_CUTOFF = 0.7
TOOLS_DIR = os.path.join(_curr_dir, "tools")

MAN2HTML = os.path.join(TOOLS_DIR, "w3mman2html.cgi")

# host to pass into Flask's app.run.
HOST_IP = os.getenv("HOST_IP", "")
MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost")
DEBUG = True
