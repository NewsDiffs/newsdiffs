import os

from website.util.Bag import Bag

# https://django-doc-test1.readthedocs.io/en/stable-1.5.x/topics/logging.html
formatters = Bag(verbose='verbose')
handlers = Bag(console='console', file='file')
LOGGING = {
    'version': 1,
    'disable_existing_loggers': True,
    'formatters': {
        formatters.verbose: {
            'format':
                '%(asctime)s.%(msecs)03d [%(name)s] %(levelname)s: %(message)s',
            'datefmt': '%Y-%m-%d %H:%M:%S',
        },
    },
    'handlers': {
        'null': {
            'class': 'django.utils.log.NullHandler',
        },
        handlers.console: {
            'level': os.environ.get('CONSOLE_LOG_LEVEL', 'ERROR'),
            'class': 'logging.StreamHandler',
            'formatter': 'verbose',
        },
    },
    'root': {
        'level': 'DEBUG',
        'handlers': [handlers.console],
    },
    'loggers': {
        'django.request': {
            # Django's DEFAULT_LOGGING sets propagate: False
            # I'm not sure why this is necessary since we should have prevented
            # Django from configuring any logging with LOGGING_CONFIG=None above
            'propagate': True,
        },
    },
}
if os.environ.get('LOG_FILE_PATH', None):
    # Log to the file in prod
    LOGGING['handlers'][handlers.file] = {
        'level': os.environ['LOG_FILE_LOG_LEVEL'],
        'class': 'logging.handlers.RotatingFileHandler',
        'filename': os.environ['LOG_FILE_PATH'],
        'maxBytes': os.environ.get('LOG_FILE_MAX_BYTES', 32 * 1024 * 1024),
        'backupCount': 1,
        'formatter': formatters.verbose,
    }
    LOGGING['root']['handlers'] += [handlers.file]
