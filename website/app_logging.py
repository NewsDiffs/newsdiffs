import logging

# The logger that the application should use
logger = logging.getLogger(__name__)

# This formatter is like the default but uses a period rather than a comma
# to separate the milliseconds
class MyFormatter(logging.Formatter):
    def formatTime(self, record, datefmt=None):
        return logging.Formatter.formatTime(self, record, datefmt).replace(',', '.')

formatter = MyFormatter('%(asctime)s:%(levelname)s:%(message)s')
