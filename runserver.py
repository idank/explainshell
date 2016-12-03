from explainshell import config
from explainshell.web import app

import logging.config
logging.config.dictConfig(config.LOGGING_DICT)

if __name__ == '__main__':
    if config.HOST_IP:
        app.run(debug=config.DEBUG, host=config.HOST_IP)
    else:
        app.run(debug=config.DEBUG)
