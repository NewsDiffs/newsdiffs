import logging
import os
import sys

import util.path

logger = logging.getLogger(__name__)


def configure_env():
    os.environ['DJANGO_SETTINGS_MODULE'] = 'settings'

    project_dir = util.path.get_project_dir()
    logger.info('Appending {0} to sys.path'.format(project_dir))
    sys.path.append(project_dir)
