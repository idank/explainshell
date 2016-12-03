import os

_currdir = os.path.dirname(os.path.dirname(__file__))

MANPAGEDIR = os.path.join(_currdir, 'manpages')
CLASSIFIER_CUTOFF = 0.7
TOOLSDIR = os.path.join(_currdir, 'tools')

MAN2HTML = os.path.join(TOOLSDIR, 'w3mman2html.cgi')

HOST_IP = False
MONGO_URI = os.getenv('MONGO_URI', 'mongodb://localhost')
DEBUG = True

LOGGING_DICT = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'standard': {
            'format': '%(asctime)s [%(levelname)s] %(name)s: %(message)s'
        },
    },
    'handlers': {
        'console': {
            'level' : 'INFO',
            'class' : 'logging.StreamHandler',
            'formatter': 'standard',
        },
        'file': {
            'class': 'logging.FileHandler',
            'level': 'INFO',
            'formatter': 'standard',
            'filename': 'application.log',
            'mode': 'a',
        },
    },
    'loggers': {
        'explainshell': {
            'handlers': ['console'],
            'level': 'INFO',
            'propagate': False
        }
    }
}

# load additional config from an out-of-tree file
if os.getenv('ADDITIONAL_CONFIG'):
    execfile(os.getenv('ADDITIONAL_CONFIG'))
