import logging.config

from explainshell import config
from explainshell.web import app


logging.config.dictConfig(config.LOGGING_DICT)

if __name__ == '__main__':
    if len(config.HOST_IP) > 1:
        app.run(debug=config.DEBUG, host=config.HOST_IP)
    else:
        app.run(debug=config.DEBUG)
