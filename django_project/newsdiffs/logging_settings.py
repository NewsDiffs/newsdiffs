from datetime import datetime
import logging
import os

from dateutil.tz import tzlocal

from util.Bag import Bag


class IsoDateTimeFormatter(logging.Formatter):
    def formatTime(self, record, datefmt=None):
        ct = datetime.fromtimestamp(record.created, tzlocal())
        return ct.strftime('%Y-%m-%dT%H:%M:%S.%f%z')


# https://django-doc-test1.readthedocs.io/en/stable-1.5.x/topics/logging.html
formatters = Bag(verbose='verbose')
handlers = Bag(console='console', file='file')
boto_log_level = os.environ.get('BOTO_LOG_LEVEL', 'WARN')
LOGGING = {
    'version': 1,
    'disable_existing_loggers': True,
    'formatters': {
        formatters.verbose: {
            '()': 'newsdiffs.logging_settings.IsoDateTimeFormatter',
            'format': '%(asctime)s [%(name)s] %(levelname)s: %(message)s',
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
        'class': 'logging.handlers.RotatingFileHandler',
        'filename': os.environ['LOG_FILE_PATH'],
        'maxBytes': os.environ.get('LOG_FILE_MAX_BYTES', 32 * 1024 * 1024),
        'backupCount': 1,
        'formatter': formatters.verbose,
    }
    LOGGING['root']['handlers'] += [handlers.file]
