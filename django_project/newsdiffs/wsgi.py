#!/usr/bin/env python
"""
WSGI config for eb_django project.

It exposes the WSGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/1.11/howto/deployment/wsgi/
"""

import logging.config

from django.core.wsgi import get_wsgi_application

from newsdiffs import logging_settings

# util may depend upon logging, so set it up first
logging.config.dictConfig(logging_settings.LOGGING)

logger = logging.getLogger(__name__)
logger.debug('Creating Django WSGI application')

from util import env_util
try:
    env_util.configure_env()
except Exception as ex:
    logger.exception(ex)

application = get_wsgi_application()
