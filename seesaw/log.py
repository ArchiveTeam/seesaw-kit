import collections
import logging


LOG_FORMAT = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'


class LogFilter(object):
    def filter(self, record):
        if not record.name:
            return True
        if 'seesaw' in record.name or 'root' in record.name:
            return True


class InternalTempLogHandler(logging.Handler):
    """Logging handler that keeps recent lines to be retrieved later"""

    def __init__(self, *args, **kwargs):
        logging.Handler.__init__(self, *args, **kwargs)
        self.records = collections.deque((), 500)

    def emit(self, record):
        self.records.append(record)

    def get_str_list(self):
        return list(self.format(record) for record in self.records)
