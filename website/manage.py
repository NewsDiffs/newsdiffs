#!/usr/bin/env python

if __name__ == "__main__":
    import os
    import sys

    from django.core.management import execute_from_command_line

    import util

    # util may depend upon logging, so set it up first
    if 'LOG_FILE_PATH' in os.environ:
        util.log_to_file(os.environ['LOG_FILE_PATH'], os.environ['LOG_LEVEL'])
    util.configure_env()

    execute_from_command_line(sys.argv)
