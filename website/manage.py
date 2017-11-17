#!/usr/bin/env python

if __name__ == "__main__":
    import logging.config
    import sys

    from django.core.management import execute_from_command_line

    import settings
    import util.env

    # util may depend upon logging, so set it up first
    logging.config.dictConfig(settings.LOGGING)

    util.env.configure_env()

    execute_from_command_line(sys.argv)
