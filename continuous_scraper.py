#!/usr/bin/python

import logging
import subprocess
import time
import os

from pid import PidFile

lock_file_path = os.environ['CONTINUOUS_SCRAPER_LOCK_FILE_PATH']

COMMAND = 'python manage.py scraper'.split()
CWD = '/opt/python/current/app'
OUTPUT_FILE = '/opt/python/log/continuous_scraper.out'
ERROR_FILE = '/opt/python/log/continuous_scraper.err'

max_time = 120 * 60
min_time = 5 * 60
next_time = -1
logger = logging.getLogger(__name__)


def wait_for(f, timeout):
    limit = time.time() + timeout
    while time.time() < limit and f() is None:
        time.sleep(1)
    return f() is not None


with PidFile(lock_file_path) as pid_file, \
        open(OUTPUT_FILE, 'w') as out_file, \
        open(ERROR_FILE, 'w') as error_file:
    while True:
        # run at most once every min_time seconds
        curt = time.time()
        if curt < next_time:
            time.sleep(next_time - curt)
        next_time = curt + min_time

        logger.info('Beginning scraper')
        p = subprocess.Popen(COMMAND, stdout=out_file, stderr=error_file, cwd=CWD)

        if not wait_for(p.poll, max_time):
            logger.error('Killing process!')
            try:
                p.terminate()
                time.sleep(5)
                p.kill()
            except OSError:
                pass
