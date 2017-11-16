import logging
import os
import sys

from app_logging import logger, formatter


def configure_env():
    os.environ['DJANGO_SETTINGS_MODULE'] = 'website.settings'

    project_dir = get_project_dir()
    logger.info('Appending {0} to sys.path'.format(project_dir))
    sys.path.append(project_dir)


def get_project_dir():
    app_dir = os.path.dirname(os.path.realpath(__file__))
    project_dir = os.path.abspath(os.path.join(app_dir, os.pardir))
    return project_dir


def prepend_project_dir(path):
    project_dir = get_project_dir()
    return os.path.abspath(os.path.join(project_dir, path))


def log_to_file(file_path, log_level):
    file_handler = logging.FileHandler(file_path, mode='w')
    file_handler.setLevel(log_level)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
