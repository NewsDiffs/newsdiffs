# https://django-doc-test1.readthedocs.io/en/stable-1.5.x/ref/settings.html

import logging
import os

from util import path_util

DEBUG = os.environ.get('DJANGO_DEBUG', None) == 'True'
TEMPLATE_DEBUG = DEBUG

ADMINS = ()
MANAGERS = ADMINS
SERVER_EMAIL = "noreply@newsdiffs.org"

DATABASES = {
    'default': {
        'ENGINE': os.environ.get('DB_ENGINE', None),
        'HOST': os.environ.get('DB_HOST', None),
        'NAME': os.environ.get('DB_NAME', None),
        'USER': os.environ.get('DB_USER', None),
        'PASSWORD': os.environ.get('DB_PASSWORD', None),
    }
}

ALLOWED_HOSTS = [
    'newsdiffs-dev.us-east-1.elasticbeanstalk.com',
    'newsdiffs.us-east-1.elasticbeanstalk.com',
    '.newsdiffs.org',
]

# Local time zone for this installation. Choices can be found here:
# http://en.wikipedia.org/wiki/List_of_tz_zones_by_name
# although not all choices may be available on all operating systems.
# If running in a Windows environment this must be set to the same as your
# system time zone.
TIME_ZONE = 'America/New_York'

DATETIME_FORMAT = 'F j, Y, g:i a'

# Language code for this installation. All choices can be found here:
# http://www.i18nguy.com/unicode/language-identifiers.html
LANGUAGE_CODE = 'en-us'

SITE_ID = 1

# If you set this to False, Django will make some optimizations so as not
# to load the internationalization machinery.
USE_I18N = True

# Make this unique, and don't share it with anybody.
SECRET_KEY = os.environ.get('DJANGO_SECRET_KEY', None)
if not SECRET_KEY:
    message = 'DJANGO_SECRET_KEY is missing'
    if DEBUG:
        logging.warn(message)
    else:
        raise Exception(message)

# List of callables that know how to import templates from various sources.
TEMPLATE_LOADERS = (
    'django.template.loaders.filesystem.Loader',
    'django.template.loaders.app_directories.Loader',
    # 'django.template.loaders.filesystem.load_template_source',
    # 'django.template.loaders.app_directories.load_template_source',
    # 'django.template.loaders.eggs.load_template_source',
)

MIDDLEWARE_CLASSES = (
    'django.middleware.common.CommonMiddleware',
)

ROOT_URLCONF = 'newsdiffs.urls'

TEMPLATE_DIRS = (
    # Put strings here, like "/home/html/django_templates" or "C:/www/django/templates".
    # Always use forward slashes, even on Windows.
    # Don't forget to use absolute paths, not relative paths.
)

INSTALLED_APPS = (
    'django.contrib.staticfiles',
    'south',
    'frontend',
    'scraper',
)

STATIC_URL = '/static/'

STATICFILES_DIRS = (
    path_util.prepend_project_dir(os.path.pardir, 'static'),
)

CACHES = {}
if DEBUG:
    CACHES['default'] = {
        'BACKEND': 'django.core.cache.backends.dummy.DummyCache',
    }
else:
    CACHES['default'] = {
        'BACKEND': 'django.core.cache.backends.db.DatabaseCache',
        'LOCATION': 'cache_table',
    }

# Tell Django not to configure logging.  We'll do it manually based upon
# logging_settings
LOGGING_CONFIG = None
