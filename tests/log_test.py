import logging
import unittest

from seesaw.log import LOG_FORMAT, InternalTempLogHandler, LogFilter


def make_record(name, message='the message', level=logging.INFO):
    return logging.LogRecord(name, level, '/some/path.py', 42, message, (),
                             None)


class LogFilterTest(unittest.TestCase):
    def setUp(self):
        self.log_filter = LogFilter()

    def test_keeps_records_from_seesaw(self):
        self.assertTrue(self.log_filter.filter(make_record('seesaw.warrior')))

    def test_keeps_records_from_root(self):
        self.assertTrue(self.log_filter.filter(make_record('root')))

    def test_keeps_records_with_an_empty_name(self):
        self.assertTrue(self.log_filter.filter(make_record('')))

    def test_drops_records_from_other_loggers(self):
        self.assertFalse(self.log_filter.filter(make_record('tornado.access')))


class InternalTempLogHandlerTest(unittest.TestCase):
    def setUp(self):
        self.handler = InternalTempLogHandler()
        self.handler.setFormatter(logging.Formatter(LOG_FORMAT))

    def test_starts_empty(self):
        self.assertEqual([], self.handler.get_str_list())

    def test_keeps_emitted_records_in_order(self):
        self.handler.emit(make_record('seesaw', 'first'))
        self.handler.emit(make_record('seesaw', 'second'))

        lines = self.handler.get_str_list()

        self.assertEqual(2, len(lines))
        self.assertTrue(lines[0].endswith('seesaw - INFO - first'))
        self.assertTrue(lines[1].endswith('seesaw - INFO - second'))

    def test_discards_the_oldest_records_beyond_500(self):
        for number in range(600):
            self.handler.emit(make_record('seesaw', 'message %d' % number))

        lines = self.handler.get_str_list()

        self.assertEqual(500, len(lines))
        self.assertTrue(lines[0].endswith('message 100'))
        self.assertTrue(lines[-1].endswith('message 599'))

    def test_works_as_a_real_logging_handler(self):
        logger = logging.getLogger('seesaw.log_test.example')
        logger.propagate = False
        logger.setLevel(logging.DEBUG)
        logger.addHandler(self.handler)

        try:
            logger.info('hello from the logger')
        finally:
            logger.removeHandler(self.handler)

        lines = self.handler.get_str_list()

        self.assertEqual(1, len(lines))
        self.assertIn('hello from the logger', lines[0])
