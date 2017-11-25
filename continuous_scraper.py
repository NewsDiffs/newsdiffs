#!/usr/bin/python

from datetime import datetime, timedelta
import logging
import signal
import subprocess
import time
import os
import sys

from dateutil.tz import tzlocal
from pid import PidFile


class IsoDateTimeFormatter(logging.Formatter):
    def formatTime(self, record, datefmt=None):
        ct = datetime.fromtimestamp(record.created, tzlocal())
        return ct.strftime('%Y-%m-%dT%H:%M:%S.%f%z')


run_dir = os.environ.get('CONTINUOUS_SCRAPER_RUN_DIR', os.getcwd())
cwd = os.environ.get('EB_CONFIG_APP_CURRENT', os.getcwd())
command_parts = [
    sys.executable,
    'django_project/manage.py',
    'scrape',
]

max_repeat_seconds = 2 * 60 * 60
min_repeat_seconds = 5 * 60
next_repeat_seconds = -1
logger = logging.getLogger(__name__)
logger.setLevel(os.environ.get('CONSOLE_LOG_LEVEL', 'INFO'))
handler = logging.StreamHandler()
handler.setFormatter(IsoDateTimeFormatter('%(asctime)s [%(name)s] %(levelname)s: %(message)s'))
logger.addHandler(handler)


def interrupt_handler(signum, frame):
    # Logging may not be safe within a signal handler, so print
    print('Received signal %s.  Stopping child.' % (signum,))
    end_child_process()
    sys.exit(1)
    

signal.signal(signal.SIGINT, interrupt_handler)
signal.signal(signal.SIGTERM, interrupt_handler)


def poll_until(poll, timeout):
    limit = time.time() + timeout
    while time.time() < limit and poll() is None:
        time.sleep(1)
    return poll()


def end_child_process():
    if child_process:
        try:
            child_process.terminate()
            time.sleep(5)
            child_process.kill()
        except OSError as ex:
            logger.exception(ex)
            pass


logger.debug('Creating PID file for continuous scraping')
with PidFile('scraper.pid', run_dir) as pid_file:
    logger.info('Starting continuous scraping')
    while True:
        current_seconds = time.time()
        if current_seconds < next_repeat_seconds:
            wait_seconds = next_repeat_seconds - current_seconds
            logger.info('Waiting %s before next run',
                        timedelta(seconds=wait_seconds))
            time.sleep(wait_seconds)
        next_repeat_seconds = current_seconds + min_repeat_seconds

        logger.debug('Opening scraper child process')
        child_process = subprocess.Popen(command_parts, cwd=cwd)

        if poll_until(child_process.poll, max_repeat_seconds) is None:
            logger.error('Scraper child process has exceeded the time limit.  '
                         'Stopping it.')
            end_child_process()
        else:
            logger.debug('Scraper child process completed within time limit')
