from datetime import datetime
import logging

from dateutil.tz import tzlocal

# If the datetime has (non-UTC) time zone information, we can use this format
DATETIME_FORMAT_ISO_8601_WITH_TZ_OFFSET = '%Y-%m-%dT%H:%M:%S.%f%z'
# If the datetime is UTC, we can use this format.
DATETIME_FORMAT_ISO_8601_UTC = '%Y-%m-%dT%H:%M:%S.%fZ'


class IsoDateTimeFormatter(logging.Formatter):
    def formatTime(self, record, datefmt=None):
        ct = datetime.fromtimestamp(record.created, tzlocal())
        return ct.strftime(DATETIME_FORMAT_ISO_8601_WITH_TZ_OFFSET)
