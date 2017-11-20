import logging
import re
import os
import sys

import boto3

from website.util import path_util

logger = logging.getLogger(__name__)
s3 = boto3.resource('s3')

sh_setting_re = re.compile(r'export\s+([a-zA-Z0-9_]+)=(.*)')


def configure_env():
    os.environ['DJANGO_SETTINGS_MODULE'] = 'settings'

    project_dir = path_util.get_project_dir()
    logger.info('Appending {0} to sys.path'.format(project_dir))
    sys.path.append(project_dir)

    if 'CONFIG_S3_BUCKET' in os.environ:
        load_s3_env_vars()
    else:
        logger.info('CONFIG_S3_BUCKET not present in environ; skipping S3 '
                    'configuration')


def load_s3_env_vars():
    bucket_name = os.environ['CONFIG_S3_BUCKET']
    key = os.environ['CONFIG_S3_KEY']
    base_name, file_extension = os.path.splitext(key)
    logger.debug('Retrieving env. vars from s3://%s/%s', (bucket_name, key))
    s3_config = read_s3_contents(bucket_name, key)
    if file_extension == '.sh':
        s3_env_vars = read_sh_env_vars(s3_config)
    else:
        raise Exception('Unsupported config file extension: %s' % (file_extension,))

    # Don't log the values!  They are stored in S3 because they are
    # sensitive
    logger.debug('Loading %d env. vars from S3', len(s3_env_vars))
    for (name, val) in s3_env_vars:
        os.environ[name] = val


def read_sh_env_vars(s3_config):
    return re.findall(sh_setting_re, s3_config)


def read_s3_contents(bucket_name, key):
    local_path = os.path.join('/opt/python/current/app', os.path.basename(key))
    try:
        s3.Bucket(bucket_name).download_file(key, local_path)
        with open(local_path, 'r') as s3_config_file:
            return s3_config_file.read()
    finally:
        # Don't leave sensitive file contents on disk
        try:
            os.remove(local_path)
        except OSError as ex:
            # Or use `ex.errno != 2` ?
            if ex.strerror != 'No such file or directory':
                raise
