#!/usr/bin/python

import subprocess
import time
import os
import sys

from pidlock import LockFile

LOCK_FILE = '/tmp/newsdiffs_loop.lock'

COMMAND = 'python manage.py scraper'.split()
CWD = '/mit/newsdiffs/web_scripts/website'
OUTPUT_FILE = '/tmp/newsdiffs_output'

all_files = [OUTPUT_FILE, OUTPUT_FILE+'.err',
             '/tmp/newsdiffs_logging', '/tmp/newsdiffs_logging_errs']

ENV = dict(PYTHONPATH='/mit/newsdiffs/web_scripts/python')

max_time = 120 * 60
min_time = 5 * 60

def wait_for(f, timeout):
    limit = time.time() + timeout
    while time.time() < limit and f() is None:
        time.sleep(1)
    return f() is not None

nextt = -1

lock = LockFile(LOCK_FILE)
if not lock.try_acquire(2 * max_time):
    sys.exit()

while True:
    os.utime(LOCK_FILE, None)
    print 'running again', time.ctime()

    # run at most once every min_time seconds
    curt = time.time()
    if curt < nextt:
        time.sleep(nextt - curt)
    nextt = curt + min_time

    for fname in all_files:
        try:
            os.rename(fname, fname+'.bak')
        except OSError:
            pass
    f = open(OUTPUT_FILE, 'w')
    f2 = open(OUTPUT_FILE+'.err', 'w')
    p = subprocess.Popen(COMMAND, stdout=f, stderr=f2, cwd=CWD, env=ENV)

    if not wait_for(p.poll, max_time):
        print 'Killing process!'
        try:
            p.terminate()
            time.sleep(5)
            p.kill()
        except OSError:
            pass
    f.close()
