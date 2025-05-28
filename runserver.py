import logging

from explainshell import config
from explainshell.web import app
from explainshell.logger.logger_helper import logger
from explainshell.logger.logging_interceptor import InterceptHandler


if __name__ == '__main__':
    # activate logging and redirect all logs to loguru
    logging.basicConfig(handlers=[InterceptHandler()], level=logging.DEBUG, force=True)

    if len(config.HOST_IP) > 1:
        app.run(debug=config.DEBUG, host=config.HOST_IP)
    else:
        app.run(debug=config.DEBUG)
