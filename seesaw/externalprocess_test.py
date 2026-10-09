# encoding=utf8
from __future__ import unicode_literals

from seesaw.externalprocess import ExternalProcess
from seesaw.externalprocess import WgetDownload, RsyncUpload, CurlUpload
from seesaw import bandwidth
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


class RealizeArgsDefaultTest(BaseTestCase):
    def tearDown(self):
        bandwidth.reset()
        BaseTestCase.tearDown(self)

    def test_default_realize_args_unchanged(self):
        bandwidth.reset()
        proc = ExternalProcess("Echo", ["echo", "hi"])
        self.assertEqual(["echo", "hi"], proc.realize_args(None))


class WgetLimitRateTest(BaseTestCase):
    def tearDown(self):
        bandwidth.reset()
        BaseTestCase.tearDown(self)

    def test_no_limit_by_default(self):
        bandwidth.reset()
        task = WgetDownload(["wget", "http://example.com/"])
        args = task.realize_args(None)
        self.assertFalse(any(str(a).startswith("--limit-rate") for a in args))

    def test_limit_injected(self):
        bandwidth.set_limits(download=1000, download_divisor=2)
        task = WgetDownload(["wget", "http://example.com/"])
        args = task.realize_args(None)
        self.assertIn("--limit-rate=500k", args)

    def test_pipeline_supplied_limit_not_doubled(self):
        bandwidth.set_limits(download=1000, download_divisor=2)
        task = WgetDownload(["wget", "--limit-rate=10k", "http://example.com/"])
        args = task.realize_args(None)
        self.assertEqual(
            1, len([a for a in args if str(a).startswith("--limit-rate")]))
        self.assertIn("--limit-rate=10k", args)


class RsyncBwlimitTest(BaseTestCase):
    def tearDown(self):
        bandwidth.reset()
        BaseTestCase.tearDown(self)

    def test_default_bwlimit_preserved(self):
        bandwidth.reset()
        task = RsyncUpload("user@host::module/", ["afile"])
        args = task.realize_args(None)
        idx = args.index("--bwlimit")
        self.assertEqual("0", args[idx + 1])

    def test_bwlimit_overridden(self):
        bandwidth.set_limits(upload=1000, upload_divisor=4)
        task = RsyncUpload("user@host::module/", ["afile"])
        args = task.realize_args(None)
        idx = args.index("--bwlimit")
        self.assertEqual("250", args[idx + 1])

    def test_bwlimit_injected_when_absent(self):
        bandwidth.set_limits(upload=1000, upload_divisor=4)
        task = RsyncUpload("user@host::module/", ["afile"])
        # Simulate an rsync arg list with no --bwlimit slot (e.g. a custom
        # pipeline command): the global cap must still be applied.
        task.args = ["rsync", "-rltv", "src/", "user@host::module/"]
        args = task.realize_args(None)
        idx = args.index("--bwlimit")
        self.assertEqual("250", args[idx + 1])
        self.assertEqual(1, args.count("--bwlimit"))

    def test_bwlimit_equals_form_normalized(self):
        bandwidth.set_limits(upload=1000, upload_divisor=4)
        task = RsyncUpload("user@host::module/", ["afile"])
        # A caller-supplied "--bwlimit=N" (equals form) must be normalized to a
        # single authoritative cap, not left as a duplicate.
        task.args = ["rsync", "--bwlimit=50", "src/", "user@host::module/"]
        args = task.realize_args(None)
        self.assertEqual(1, args.count("--bwlimit"))
        self.assertFalse(any(str(a).startswith("--bwlimit=") for a in args))
        self.assertEqual("250", args[args.index("--bwlimit") + 1])


class CurlLimitRateTest(BaseTestCase):
    def tearDown(self):
        bandwidth.reset()
        BaseTestCase.tearDown(self)

    def test_no_limit_by_default(self):
        bandwidth.reset()
        task = CurlUpload("http://host/", "afile")
        args = task.realize_args(None)
        self.assertNotIn("--limit-rate", args)

    def test_pipeline_supplied_limit_not_doubled(self):
        bandwidth.set_limits(upload=1000, upload_divisor=2)
        task = CurlUpload("http://host/", "afile")
        # An existing --limit-rate (equals form) must not be doubled.
        task.args = ["curl", "--limit-rate=10k", "--upload-file", "afile",
                     "http://host/"]
        args = task.realize_args(None)
        self.assertEqual(
            1, len([a for a in args if str(a).startswith("--limit-rate")]))
        self.assertIn("--limit-rate=10k", args)

    def test_limit_injected(self):
        bandwidth.set_limits(upload=1000, upload_divisor=2)
        task = CurlUpload("http://host/", "afile")
        args = task.realize_args(None)
        self.assertIn("--limit-rate", args)
        self.assertEqual("500k", args[args.index("--limit-rate") + 1])
