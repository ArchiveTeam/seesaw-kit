import logging
import tornado.ioloop
import unittest

from seesaw.runner import Runner


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

        # A failed item normally sits out a 10 second cooldown before the
        # runner recycles it. Tests assert on what happens after the
        # cooldown, not on its length, so skip the wait.
        self._original_failed_item_delay = Runner.FAILED_ITEM_DELAY
        Runner.FAILED_ITEM_DELAY = 0

    def assertIOLoopOK(self):
        value = self.io_loop_error
        self.io_loop_error = None
        self.assertFalse(value)

    def tearDown(self):
        Runner.FAILED_ITEM_DELAY = self._original_failed_item_delay
        assert not self.io_loop_error
