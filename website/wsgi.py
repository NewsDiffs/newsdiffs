#!/usr/bin/env python
"""
WSGI config for eb_django project.

It exposes the WSGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/1.11/howto/deployment/wsgi/
"""

import logging.config

from django.core.wsgi import get_wsgi_application

import settings
import util.env

# util may depend upon logging, so set it up first
logging.config.dictConfig(settings.LOGGING)

logger = logging.getLogger(__name__)

util.env.configure_env()

logger.debug('Creating Django WSGI application')

application = get_wsgi_application()
