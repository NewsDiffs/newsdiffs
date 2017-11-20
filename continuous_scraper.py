#!/usr/bin/python

import datetime
import logging
import subprocess
import time
import os

from pid import PidFile

run_dir = os.environ['CONTINUOUS_SCRAPER_RUN_DIR']
cwd = os.environ['EB_CONFIG_APP_CURRENT']
command_parts = '/opt/python/run/venv/bin/python manage.py scrape'.split()

max_repeat_seconds = 2 * 60 * 60
min_repeat_seconds = 5 * 60
next_repeat_seconds = -1
logger = logging.getLogger(__name__)


def wait_for(f, timeout):
    limit = time.time() + timeout
    while time.time() < limit and f() is None:
        time.sleep(1)
    return f() is not None


with PidFile('scraper.pid', run_dir) as pid_file:
    while True:
        # run at most once every min_time seconds
        current_seconds = time.time()
        if current_seconds < next_repeat_seconds:
            wait_seconds = next_repeat_seconds - current_seconds
            logger.info('Waiting for %s seconds before next run',
                        datetime.timedelta(seconds=wait_seconds))
            time.sleep(wait_seconds)
        next_repeat_seconds = current_seconds + min_repeat_seconds

        logger.info('Beginning scraper')
        p = subprocess.Popen(command_parts, cwd=cwd)

        if not wait_for(p.poll, max_repeat_seconds):
            logger.error('Killing scraper')
            try:
                p.terminate()
                time.sleep(5)
                p.kill()
            except OSError:
                pass
