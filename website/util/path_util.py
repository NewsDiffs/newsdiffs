import os


def get_project_dir():
    util_dir = os.path.dirname(os.path.realpath(__file__))
    project_dir = os.path.abspath(os.path.join(util_dir, os.pardir))
    return project_dir


def prepend_project_dir(*paths):
    project_dir = get_project_dir()
    return os.path.abspath(os.path.join(project_dir, *paths))
