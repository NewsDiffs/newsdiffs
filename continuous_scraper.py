#!/usr/bin/python

from __future__ import print_function

from datetime import timedelta, datetime
import logging
import signal
import subprocess
import time
import os
import sys

from pid import PidFile

# Append the django_project so that we can import from it
this_dir = os.path.dirname(os.path.realpath(__file__))
django_project_dir = os.path.abspath(os.path.join(this_dir, 'django_project'))
sys.path.append(django_project_dir)

from util.logging_util import IsoDateTimeFormatter, DATETIME_FORMAT_ISO_8601_UTC

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
devnull_file = open(os.devnull, 'w')


def configure_logger():
    log_format = '%(asctime)s [%(name)s] %(levelname)s: %(message)s'

    stdout_handler = logging.StreamHandler(stream=sys.stdout)
    stdout_handler.setLevel(os.environ.get('CONSOLE_LOG_LEVEL', 'INFO'))
    stdout_handler.setFormatter(IsoDateTimeFormatter(log_format))

    stderr_handler = logging.StreamHandler(stream=sys.stderr)
    stderr_handler.setLevel('ERROR')
    stderr_handler.setFormatter(IsoDateTimeFormatter(log_format))

    root_logger = logging.getLogger()
    root_logger.setLevel('DEBUG')
    root_logger.addHandler(stdout_handler)
    root_logger.addHandler(stderr_handler)

    return logging.getLogger(__name__)


logger = configure_logger()


def print_message(message, file=sys.stdout):
    now = datetime.utcnow().strftime(DATETIME_FORMAT_ISO_8601_UTC)
    print('%s %s' % (now, message), file=file)


def interrupt_handler(signum, frame):
    # Logging may not be safe within a signal handler, so print
    print_message('%s received signal %s.  Stopping child.' % (__file__, signum,))
    end_child_process(can_log=False)
    sys.exit(1)
    

signal.signal(signal.SIGINT, interrupt_handler)
signal.signal(signal.SIGTERM, interrupt_handler)


def poll_until(poll, timeout):
    limit = time.time() + timeout
    while time.time() < limit and poll() is None:
        time.sleep(1)
    return poll()


def end_child_process(can_log=False):
    if child_process:
        try:
            child_process.terminate()
            time.sleep(5)
            child_process.kill()
        except OSError as ex:
            if can_log:
                logger.exception(ex)
            else:
                print_message(ex, file=sys.stderr)


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
        child_process = subprocess.Popen(command_parts, cwd=cwd,
                                         # Don't combine output from this file
                                         # with the scraper.  It gets noisy.
                                         # The scraper should be configured
                                         # to log to file
                                         stdout=devnull_file,
                                         stderr=subprocess.STDOUT)

        if poll_until(child_process.poll, max_repeat_seconds) is None:
            logger.error('Scraper child process has exceeded the time limit.  '
                         'Stopping it.')
            end_child_process(can_log=True)
        else:
            logger.debug('Scraper child process completed within time limit')
