import logging
import tornado.ioloop
import unittest
import sys


class BaseTestCase(unittest.TestCase):
    def setUp(self):
        self.io_loop_error = None

        def periodic_callback_run_monkey_patch(self1):
            if not self1._running:
                return
            try:
                self1.callback()
            except:
                logging.exception('Periodic Callback')
                self.io_loop_error = True

            self1._schedule_next()

        tornado.ioloop.PeriodicCallback._run = \
            periodic_callback_run_monkey_patch

    def assertIOLoopOK(self):
        value = self.io_loop_error
        self.io_loop_error = None
        self.assertFalse(value)

    def tearDown(self):
        assert not self.io_loop_error
