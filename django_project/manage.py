#!/usr/bin/env python

if __name__ == "__main__":
    import logging.config
    import sys

    from django.core.management import execute_from_command_line

    from newsdiffs import logging_settings

    # util may depend upon logging, so set it up first
    logging.config.dictConfig(logging_settings.LOGGING)

    logger = logging.getLogger(__name__)
    logger.info('Django manage.py running command: %s', ' '.join(sys.argv))

    from util import env_util
    try:
        env_util.configure_env()
    except Exception as ex:
        logger.exception(ex)

    execute_from_command_line(sys.argv)
