# encoding=utf8
from __future__ import unicode_literals

from seesaw.externalprocess import ExternalProcess
from seesaw.pipeline import Pipeline
from seesaw.runner import SimpleRunner
from seesaw.six import StringIO
from seesaw.test_base import BaseTestCase


class ExternalProcessUser(ExternalProcess):
    def __init__(self, *args, **kwargs):
        ExternalProcess.__init__(self, *args, **kwargs)
        self.output_buffer = StringIO()
        self.return_code = None
        self.exit_count = 0
        self.retry_delay = 0.1

    def on_subprocess_stdout(self, pipe, item, data):
        ExternalProcess.on_subprocess_stdout(self, pipe, item, data)
        self.output_buffer.write(data.decode('utf8', 'replace'))

    def on_subprocess_end(self, item, returncode):
        ExternalProcess.on_subprocess_end(self, item, returncode)
        self.return_code = returncode
        self.exit_count += 1


class ExternalProcessTest(BaseTestCase):
    def test_proc(self):
        external_process = ExternalProcessUser(
            "Echo", ["python", "-c", "print('hello world!')"], max_tries=4)
        pipeline = Pipeline(external_process)
        pipeline.has_failed = None

        def fail_callback(task, item):
            pipeline.has_failed = True

        pipeline.on_fail_item += fail_callback

        runner = SimpleRunner(pipeline, max_items=1)
        runner.start()

        output = external_process.output_buffer.getvalue()
        self.assertFalse(pipeline.has_failed)
        self.assertTrue('hello world!' in output)
        self.assertIOLoopOK()

    def test_proc_fail(self):
        for max_tries in [1, 2, 20]:
            external_process = ExternalProcessUser(
                "Quitter", ["python", "-c", "import sys;sys.exit(33)"],
                max_tries=max_tries)
            pipeline = Pipeline(external_process)
            pipeline.has_failed = None

            def fail_callback(task, item):
                pipeline.has_failed = True

            pipeline.on_fail_item += fail_callback

            runner = SimpleRunner(pipeline, max_items=1)
            runner.start()

            self.assertTrue(pipeline.has_failed)
            self.assertEqual(33, external_process.return_code)
            self.assertEqual(max_tries, external_process.exit_count)
            self.assertIOLoopOK()

    def test_no_such_file(self):
        external_process = ExternalProcessUser(
            "Fake", ["kitteh and doge.avi.exe"])
        pipeline = Pipeline(external_process)
        pipeline.has_failed = None

        def fail_callback(task, item):
            pipeline.has_failed = True

        pipeline.on_fail_item += fail_callback

        runner = SimpleRunner(pipeline, max_items=1)
        runner.start()
        self.assertTrue(pipeline.has_failed)
        self.assertIOLoopOK()

    def test_proc_stdin_error(self):
        external_process = ExternalProcessUser(
            "Echo", ["python", "-c" "print('hello world!')"], max_tries=4)

        external_process.stdin_data = lambda item: 123456

        pipeline = Pipeline(external_process)
        pipeline.has_failed = None

        def fail_callback(task, item):
            pipeline.has_failed = True

        pipeline.on_fail_item += fail_callback

        runner = SimpleRunner(pipeline, max_items=1)
        runner.start()

        self.assertTrue(pipeline.has_failed)
        self.assertIOLoopOK()
        self.assertEqual(4, external_process.exit_count)

    def test_proc_utf8(self):
        external_process = ExternalProcessUser(
            "Echo", ["python", "-c", "print(u'hello world!áßðfáßðf')"],
        )

        pipeline = Pipeline(external_process)
        pipeline.has_failed = None

        def fail_callback(task, item):
            pipeline.has_failed = True

        pipeline.on_fail_item += fail_callback

        runner = SimpleRunner(pipeline, max_items=1)
        runner.start()

        self.assertFalse(pipeline.has_failed)
        self.assertIOLoopOK()
