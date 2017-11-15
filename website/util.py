import logging
import os
import sys

def configure_env():
    os.environ['DJANGO_SETTINGS_MODULE'] = 'website.settings'

    project_dir = get_project_dir()
    logging.info('Appending {0} to sys.path'.format(project_dir))
    sys.path.append(project_dir)

def get_project_dir():
    app_dir = os.path.dirname(os.path.realpath(__file__))
    project_dir = os.path.abspath(os.path.join(app_dir, os.pardir))
    return project_dir

def prepend_project_dir(path):
    project_dir = get_project_dir()
    return os.path.abspath(os.path.join(project_dir, path))
