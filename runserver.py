from explainshell import config
from explainshell.web import app

import logging.config
logging.config.dictConfig(config.LOGGING_DICT)

if __name__ == '__main__':
    app.run(debug=config.DEBUG)
