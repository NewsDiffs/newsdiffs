#!/usr/bin/env python

if __name__ == "__main__":
    import logging.config
    import sys

    from django.core.management import execute_from_command_line

    from website import logging_settings

    # util may depend upon logging, so set it up first
    logging.config.dictConfig(logging_settings.LOGGING)

    from website.util import env_util
    env_util.configure_env()

    execute_from_command_line(sys.argv)
