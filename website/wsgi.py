#!/usr/bin/env python
"""
WSGI config for eb_django project.

It exposes the WSGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/1.11/howto/deployment/wsgi/
"""

import os

from django.core.wsgi import get_wsgi_application

import util

# util may depend upon logging, so set it up first
util.log_to_file(os.environ['LOG_FILE_PATH'], os.environ['LOG_LEVEL'])
util.configure_env()

application = get_wsgi_application()
