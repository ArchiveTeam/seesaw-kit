# encoding=utf8
'''Tests for the warrior runner script.'''
from __future__ import unicode_literals

import json
import logging
import logging.handlers
import os
import os.path
import shutil
import sys
import tempfile

from tornado.httpserver import HTTPServer
from tornado.ioloop import IOLoop
from tornado.testing import bind_unused_port
from tornado.web import Application, RequestHandler

import seesaw
from seesaw.log import LogFilter
from seesaw.script.run_warrior import main, setup_logging
from seesaw.six import StringIO
from tests.test_base import BaseTestCase
from seesaw.web import SeesawConnection


class SetupLoggingTest(BaseTestCase):
    def setUp(self):
        super(SetupLoggingTest, self).setUp()

        self.temp_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.temp_dir, True)

        root_logger = logging.getLogger()
        self.original_handlers = list(root_logger.handlers)
        self.original_level = root_logger.level
        self.addCleanup(self.restore_root_logger)

    def restore_root_logger(self):
        root_logger = logging.getLogger()
        for handler in list(root_logger.handlers):
            if handler not in self.original_handlers:
                handler.close()
                root_logger.removeHandler(handler)
        root_logger.setLevel(self.original_level)

    def added_handlers(self):
        return [handler for handler in logging.getLogger().handlers
                if handler not in self.original_handlers]

    def test_writes_a_warrior_log_into_the_data_directory(self):
        setup_logging(self.temp_dir)

        logging.getLogger('seesaw.example').warning('a message')

        path = os.path.join(self.temp_dir, 'warrior.log')
        self.assertTrue(os.path.exists(path))

        with open(path) as file_obj:
            self.assertIn('a message', file_obj.read())

    def test_installs_a_rotating_file_handler(self):
        setup_logging(self.temp_dir)

        handlers = self.added_handlers()

        self.assertTrue(any(
            isinstance(handler, logging.handlers.TimedRotatingFileHandler)
            for handler in handlers))

    def test_every_handler_gets_the_seesaw_filter(self):
        setup_logging(self.temp_dir)

        for handler in logging.getLogger().handlers:
            self.assertTrue(any(isinstance(log_filter, LogFilter)
                                for log_filter in handler.filters))

    def test_the_file_handler_uses_the_seesaw_log_format(self):
        setup_logging(self.temp_dir)

        logging.getLogger('seesaw.example').warning('a message')

        with open(os.path.join(self.temp_dir, 'warrior.log')) as file_obj:
            line = file_obj.readline()

        # "<time> - <name> - <level> - <message>"
        self.assertIn(' - seesaw.example - WARNING - a message', line)

    def test_the_filter_keeps_other_libraries_out_of_the_log(self):
        setup_logging(self.temp_dir)

        logging.getLogger('tornado.access').warning('noise from tornado')
        logging.getLogger('seesaw.example').warning('signal from seesaw')

        with open(os.path.join(self.temp_dir, 'warrior.log')) as file_obj:
            contents = file_obj.read()

        self.assertIn('signal from seesaw', contents)
        self.assertNotIn('noise from tornado', contents)


class RunnerTypeTest(BaseTestCase):
    def test_importing_the_script_marks_the_runner_as_a_warrior(self):
        # Importing run_warrior sets seesaw.runner_type, which ends up in
        # the user agent the tracker sees.
        self.assertEqual('Warrior', seesaw.runner_type)


class RegisterHandler(RequestHandler):
    def post(self):
        self.finish(json.dumps({'warrior_id': 'the-warrior-id'}))


class UpdateHandler(RequestHandler):
    def initialize(self, state):
        self.state = state

    def post(self):
        self.state['updates'] += 1
        self.finish(json.dumps({
            # A newer release makes the warrior reboot, which stops the IO
            # loop and so returns from main().
            'warrior': {'seesaw_version': '999.0'},
            'broadcast_message': 'hello everyone',
            'projects': [],
        }))


class MainTest(BaseTestCase):
    '''Runs the warrior entry point for real against a local Warrior HQ.'''

    def setUp(self):
        super(MainTest, self).setUp()

        self.temp_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.temp_dir, True)
        self.projects_dir = os.path.join(self.temp_dir, 'projects')
        self.data_dir = os.path.join(self.temp_dir, 'data')
        os.makedirs(self.projects_dir)
        os.makedirs(self.data_dir)

        self.state = {'updates': 0}
        sock, hq_port = bind_unused_port()
        hq = HTTPServer(Application([
            (r'/api/register.json', RegisterHandler),
            (r'/api/update.json', UpdateHandler, {'state': self.state}),
        ]))
        hq.add_sockets([sock])
        self.addCleanup(hq.stop)
        self.hq_url = 'http://localhost:%d/' % hq_port

        sock, self.port = bind_unused_port()
        sock.close()

        self.addCleanup(setattr, sys, 'argv', sys.argv)
        self.addCleanup(self.clean_up_after_main)

        root_logger = logging.getLogger()
        self.original_handlers = list(root_logger.handlers)
        self.original_level = root_logger.level

    def clean_up_after_main(self):
        # main() leaves a warrior, its polling timers and a web server
        # behind; none of them are handed back to the caller, so unpick
        # what would otherwise leak into later tests.
        SeesawConnection.warrior = None
        io_loop = IOLoop.instance()
        io_loop._stopped = False
        io_loop._running = False
        io_loop._callbacks.clear()
        del io_loop._timeouts[:]

        root_logger = logging.getLogger()
        for handler in list(root_logger.handlers):
            if handler not in self.original_handlers:
                handler.close()
                root_logger.removeHandler(handler)
        root_logger.setLevel(self.original_level)

    def run_main(self, *arguments):
        sys.argv = ['run-warrior',
                    '--projects-dir', self.projects_dir,
                    '--data-dir', self.data_dir,
                    '--warrior-hq', self.hq_url,
                    '--address', 'localhost',
                    '--port', str(self.port)] + list(arguments)

        original_stdout = sys.stdout
        sys.stdout = StringIO()
        try:
            main()
            return sys.stdout.getvalue()
        finally:
            sys.stdout = original_stdout

    def test_starts_up_and_contacts_hq(self):
        text = self.run_main()

        self.assertIn('ArchiveTeam Seesaw kit', text)
        self.assertIn(seesaw.__version__, text)
        self.assertIn('Starting the web interface on localhost:%d' % self.port,
                      text)
        self.assertEqual(1, self.state['updates'])

    def test_writes_its_log_into_the_data_directory(self):
        self.run_main()

        self.assertTrue(os.path.exists(
            os.path.join(self.data_dir, 'warrior.log')))

    def test_registers_itself_and_remembers_the_id(self):
        self.run_main()

        with open(os.path.join(self.projects_dir, 'config.json')) as file_obj:
            self.assertEqual('the-warrior-id',
                             json.load(file_obj)['warrior_id'])

    def test_the_warrior_build_option_is_reported_to_the_tracker(self):
        original_build = seesaw.warrior_build
        self.addCleanup(setattr, seesaw, 'warrior_build', original_build)

        self.run_main('--warrior-build', 'test-build-1')

        self.assertEqual('test-build-1', seesaw.warrior_build)

    def test_the_required_options_are_enforced(self):
        sys.argv = ['run-warrior']

        original_stderr = sys.stderr
        sys.stderr = StringIO()
        try:
            self.assertRaises(SystemExit, main)
        finally:
            sys.stderr = original_stderr
