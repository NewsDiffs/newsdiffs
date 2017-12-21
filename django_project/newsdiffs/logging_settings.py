import os
import sys

from util.Bag import Bag


# https://django-doc-test1.readthedocs.io/en/stable-1.5.x/topics/logging.html
formatters = Bag(verbose='verbose')
handlers = Bag(console_stdout='console_stdout', console_stderr='console_stderr',
               file='file')
boto_log_level = os.environ.get('BOTO_LOG_LEVEL', 'WARN')
LOGGING = {
    'version': 1,
    'disable_existing_loggers': True,
    'formatters': {
        formatters.verbose: {
            '()': 'util.logging_util.IsoDateTimeFormatter',
            'format': '%(asctime)s [%(name)s] %(levelname)s: %(message)s',
        },
    },
    'handlers': {
        'null': {
            'class': 'django.utils.log.NullHandler',
        },
        handlers.console_stdout: {
            'level': os.environ.get('CONSOLE_LOG_LEVEL', 'ERROR'),
            'formatter': 'verbose',
            'class': 'logging.StreamHandler',
            'stream': sys.stdout,
        },
        handlers.console_stderr: {
            'level': 'ERROR',
            'formatter': 'verbose',
            'class': 'logging.StreamHandler',
            'stream': sys.stderr,
        },
    },
    'root': {
        'level': 'DEBUG',
        'handlers': [
            handlers.console_stdout,
            handlers.console_stderr,
        ],
    },
    'loggers': {
        'django.request': {
            # Django's DEFAULT_LOGGING sets propagate: False
            # I'm not sure why this is necessary since we should have prevented
            # Django from configuring any logging with LOGGING_CONFIG=None above
            'propagate': True,
        },
        'django.db': {
            'level': os.environ.get('DJANGO_DB_LOG_LEVEL', 'WARN'),
        },
        'boto3': {
            'level': boto_log_level,
        },
        'botocore': {
            'level': boto_log_level,
        },
        's3transfer': {
            'level': boto_log_level,
        }
    },
}

if os.environ.get('LOG_FILE_PATH', None):
    # Log to the file in prod
    LOGGING['handlers'][handlers.file] = {
        'level': os.environ['LOG_FILE_LOG_LEVEL'],
        'class': 'logging.handlers.TimedRotatingFileHandler',
        'filename': os.environ['LOG_FILE_PATH'],
        'when': 'midnight',
        'utc': True,
        'formatter': formatters.verbose,
    }
    LOGGING['root']['handlers'] += [handlers.file]
