import logging

logger = logging.getLogger(__name__)


class LogExceptionMiddleware(object):
    def process_exception(self, request, exception):
        logger.exception(exception)
        return None
