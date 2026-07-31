# encoding=utf8

import os
import os.path
import shutil
import sys
import tempfile

from tornado.ioloop import IOLoop

import seesaw.externalprocess as externalprocess
from seesaw.externalprocess import AsyncPopen, AsyncPopen2, CurlUpload, \
    ExternalProcess, RsyncUpload, WgetDownload
from seesaw.item import ItemInterpolation, ItemValue
from seesaw.pipeline import Pipeline
from seesaw.runner import SimpleRunner
from io import StringIO
from seesaw.task import SetItemKey
from tests.test_base import BaseTestCase


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
            "Echo", [sys.executable, "-c", "print('hello world!')"], max_tries=4)
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
                "Quitter", [sys.executable, "-c", "import sys;sys.exit(33)"],
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
            "Echo", [sys.executable, "-c", "print('hello world!')"], max_tries=4)

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
            "Echo", [sys.executable, "-c", "print(u'hello world!áßðfáßðf')"],
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

    def test_accept_on_exit_code_treats_a_nonzero_exit_as_success(self):
        external_process = ExternalProcessUser(
            "Quitter", [sys.executable, "-c", "import sys;sys.exit(4)"],
            accept_on_exit_code=[0, 4])
        pipeline = run_to_completion(external_process)

        self.assertFalse(pipeline.has_failed)
        self.assertEqual(1, external_process.exit_count)
        self.assertIOLoopOK()

    def test_an_exit_code_outside_accept_on_exit_code_fails(self):
        external_process = ExternalProcessUser(
            "Quitter", [sys.executable, "-c", "import sys;sys.exit(0)"],
            accept_on_exit_code=[4])
        pipeline = run_to_completion(external_process)

        self.assertTrue(pipeline.has_failed)
        self.assertIOLoopOK()

    def test_retry_on_exit_code_retries_a_listed_code(self):
        external_process = ExternalProcessUser(
            "Quitter", [sys.executable, "-c", "import sys;sys.exit(7)"],
            max_tries=3, retry_on_exit_code=[7])
        pipeline = run_to_completion(external_process)

        self.assertTrue(pipeline.has_failed)
        self.assertEqual(3, external_process.exit_count)
        self.assertIOLoopOK()

    def test_retry_on_exit_code_gives_up_on_an_unlisted_code(self):
        external_process = ExternalProcessUser(
            "Quitter", [sys.executable, "-c", "import sys;sys.exit(9)"],
            max_tries=3, retry_on_exit_code=[7])
        pipeline = run_to_completion(external_process)

        self.assertTrue(pipeline.has_failed)
        self.assertEqual(1, external_process.exit_count)
        self.assertIOLoopOK()

    def test_max_tries_of_none_keeps_retrying_until_success(self):
        temp_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, temp_dir, True)
        counter = os.path.join(temp_dir, 'attempts')

        # Fails for the first two attempts, then succeeds. With max_tries of
        # None the task must keep going rather than giving up after one.
        script = (
            "import os,sys\n"
            "attempts = len(open(sys.argv[1]).read()) "
            "if os.path.exists(sys.argv[1]) else 0\n"
            "open(sys.argv[1], 'a').write('x')\n"
            "sys.exit(0 if attempts >= 2 else 1)\n"
        )
        external_process = ExternalProcessUser(
            "Flaky", [sys.executable, "-c", script, counter], max_tries=None)
        pipeline = run_to_completion(external_process)

        self.assertFalse(pipeline.has_failed)
        self.assertEqual(3, external_process.exit_count)
        self.assertIOLoopOK()

    def test_stdin_data_reaches_the_process(self):
        external_process = ExternalProcessUser(
            "Cat",
            [sys.executable, "-c",
             "import sys;sys.stdout.write(sys.stdin.read())"])
        external_process.stdin_data = lambda item: b"from stdin"
        pipeline = run_to_completion(external_process)

        self.assertFalse(pipeline.has_failed)
        self.assertIn('from stdin',
                      external_process.output_buffer.getvalue())
        self.assertIOLoopOK()

    def test_the_environment_is_passed_to_the_process(self):
        external_process = ExternalProcessUser(
            "Env",
            [sys.executable, "-c",
             "import os;print(os.environ['SEESAW_TEST_VALUE'])"],
            env={'SEESAW_TEST_VALUE': 'from the env',
                 'PATH': os.environ.get('PATH', '')})
        pipeline = run_to_completion(external_process)

        self.assertFalse(pipeline.has_failed)
        self.assertIn('from the env',
                      external_process.output_buffer.getvalue())
        self.assertIOLoopOK()

    def test_pythonioencoding_is_defaulted(self):
        external_process = ExternalProcess("Echo", ["true"])

        self.assertEqual('utf8:replace',
                         external_process.env['PYTHONIOENCODING'])

    def test_an_explicit_pythonioencoding_is_kept(self):
        external_process = ExternalProcess("Echo", ["true"],
                                           env={'PYTHONIOENCODING': 'ascii'})

        self.assertEqual('ascii', external_process.env['PYTHONIOENCODING'])

    def test_the_arguments_are_realized_against_the_item(self):
        external_process = ExternalProcessUser(
            "Echo", [sys.executable, "-c", ItemInterpolation(
                "print('the value is %(the_value)s')")])
        pipeline = Pipeline(SetItemKey('the_value', 'realized'),
                            external_process)
        pipeline.has_failed = None
        pipeline.on_fail_item += \
            lambda task, item: setattr(pipeline, 'has_failed', True)
        SimpleRunner(pipeline, max_items=1).start()

        self.assertFalse(pipeline.has_failed)
        self.assertIn('the value is realized',
                      external_process.output_buffer.getvalue())
        self.assertIOLoopOK()

    def test_the_item_does_not_fail_while_the_process_is_running(self):
        external_process = ExternalProcess("Echo", ["true"])
        item = ItemStub()
        item['ExternalProcess.running'] = True

        external_process.fail_item(item)

        self.assertEqual({}, item.task_status)

        item['ExternalProcess.running'] = False
        external_process.fail_item(item)

        self.assertEqual(1, len(item.task_status))


class WgetDownloadTest(BaseTestCase):
    def test_sends_no_stdin_by_default(self):
        task = WgetDownload(['wget'])

        self.assertEqual(b'', task.stdin_data(None))
        self.assertEqual([0], task.accept_on_exit_code)

    def test_uses_the_stdin_data_function(self):
        task = WgetDownload(
            ['wget'],
            stdin_data_function=lambda item: b'http://example.com/\n')

        self.assertEqual(b'http://example.com/\n', task.stdin_data(None))

    def test_passes_through_the_exit_code_settings(self):
        task = WgetDownload(['wget'], accept_on_exit_code=[0, 8],
                            retry_on_exit_code=[4], max_tries=3)

        self.assertEqual([0, 8], task.accept_on_exit_code)
        self.assertEqual([4], task.retry_on_exit_code)
        self.assertEqual(3, task.max_tries)


class RsyncUploadTest(BaseTestCase):
    def test_builds_the_default_argument_list(self):
        task = RsyncUpload('rsync://example.com/target/', ['a.txt'])

        self.assertEqual(
            ['rsync', '-rltv', '--timeout=300', '--contimeout=300',
             '--progress', '--bwlimit', '0', '--files-from=-', './',
             'rsync://example.com/target/'],
            task.args)

    def test_includes_the_bandwidth_limit_and_extra_arguments(self):
        task = RsyncUpload('rsync://example.com/target/', ['a.txt'],
                           bwlimit='512', extra_args=['--partial'])

        self.assertIn('512', task.args)
        self.assertEqual(['--bwlimit', '512', '--partial', '--files-from=-'],
                         task.args[5:9])

    def test_stdin_data_lists_paths_relative_to_the_source(self):
        task = RsyncUpload('rsync://example.com/target/',
                           ['/data/one.txt', '/data/nested/two.txt'],
                           target_source_path='/data/')

        self.assertEqual(b'one.txt\nnested/two.txt\n', task.stdin_data(None))

    def test_stdin_data_realizes_the_file_list(self):
        task = RsyncUpload('rsync://example.com/target/',
                           [ItemInterpolation('%(data_dir)s/%(item_name)s')],
                           target_source_path=ItemValue('data_dir'))

        result = task.stdin_data({'data_dir': '/data', 'item_name': 'thing'})

        self.assertEqual(b'thing\n', result)


class CurlUploadTest(BaseTestCase):
    def test_builds_the_argument_list(self):
        task = CurlUpload('http://example.com/target/', '/data/one.txt')

        self.assertEqual(
            ['curl', '--fail', '--output', '/dev/null',
             '--connect-timeout', '60', '--speed-limit', '1',
             '--speed-time', '900',
             '--header', 'X-Curl-Limits: inf,1,900',
             '--write-out', 'Upload server: %{url_effective}\\n',
             '--location', '--upload-file', '/data/one.txt',
             'http://example.com/target/'],
            task.args)

    def test_the_limits_appear_in_both_the_flags_and_the_header(self):
        task = CurlUpload('http://example.com/target/', '/data/one.txt',
                          connect_timeout=30, speed_limit=100,
                          speed_time=60)

        self.assertEqual(['--connect-timeout', '30'], task.args[4:6])
        self.assertIn('X-Curl-Limits: inf,100,60', task.args)


class AsyncPopenTest(BaseTestCase):
    '''Covers the deprecated pty-based runner still shipped in the kit.'''

    def run_process(self, popen_class, args, **kwargs):
        collected = []
        result = {}

        process = popen_class(args=args, **kwargs)
        process.on_output += collected.append

        def handle_end(return_code):
            result['return_code'] = return_code
            IOLoop.instance().add_callback(IOLoop.instance().stop)

        process.on_end += handle_end
        process.run()
        IOLoop.instance().start()

        return result.get('return_code'), b''.join(
            data if isinstance(data, bytes) else data.encode('utf8')
            for data in collected)

    def test_collects_output_and_the_return_code(self):
        return_code, output = self.run_process(
            AsyncPopen,
            [sys.executable, '-c', "print('hello from a pty')"])

        self.assertEqual(0, return_code)
        self.assertIn(b'hello from a pty', output)

    def test_reports_a_nonzero_return_code(self):
        return_code, dummy = self.run_process(
            AsyncPopen, [sys.executable, '-c', 'import sys;sys.exit(5)'])

        self.assertEqual(5, return_code)

    def test_async_popen2_collects_stdout_and_stderr(self):
        return_code, output = self.run_process(
            AsyncPopen2,
            [sys.executable, '-c',
             "import sys;sys.stdout.write('out');sys.stderr.write('err')"])

        self.assertEqual(0, return_code)
        self.assertIn(b'out', output)
        self.assertIn(b'err', output)

    def test_async_popen2_exposes_stdin(self):
        temp_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, temp_dir, True)
        target = os.path.join(temp_dir, 'written.txt')

        import subprocess

        collected = []
        result = {}
        process = AsyncPopen2(
            args=[sys.executable, '-c',
                  "import sys;open(sys.argv[1],'w').write(sys.stdin.read())",
                  target],
            stdin=subprocess.PIPE)
        process.on_output += collected.append

        def handle_end(return_code):
            result['return_code'] = return_code
            IOLoop.instance().add_callback(IOLoop.instance().stop)

        process.on_end += handle_end
        process.run()
        process.stdin.write(b'piped in')
        process.stdin.close()
        IOLoop.instance().start()

        self.assertEqual(0, result['return_code'])
        with open(target) as file_obj:
            self.assertEqual('piped in', file_obj.read())


class ItemStub(dict):
    '''The little of an Item that ExternalProcess.fail_item touches.'''
    def __init__(self, *args, **kwargs):
        dict.__init__(self, *args, **kwargs)
        self.task_status = {}

    def set_task_status(self, task, status):
        self.task_status[task] = status

    def description(self):
        return 'Item stub'

    def log_output(self, data, full_line=True):
        pass

    def log_error(self, task, *args):
        pass


def run_to_completion(task, max_items=1):
    '''Run a single-task pipeline and report whether the item failed.'''
    pipeline = Pipeline(task)
    pipeline.has_failed = None
    pipeline.on_fail_item += \
        lambda dummy, item: setattr(pipeline, 'has_failed', True)

    SimpleRunner(pipeline, max_items=max_items).start()

    return pipeline


class CleanupTest(BaseTestCase):
    '''The atexit hook that kills anything still running.'''

    def setUp(self):
        super(CleanupTest, self).setUp()
        self.original_procs = set(externalprocess._all_procs)
        self.addCleanup(self.restore_procs)

    def restore_procs(self):
        externalprocess._all_procs.clear()
        externalprocess._all_procs.update(self.original_procs)

    def test_nothing_to_do_when_no_process_is_left(self):
        externalprocess._all_procs.clear()

        externalprocess.cleanup()

    def test_kills_a_process_that_is_still_running(self):
        import subprocess

        process = subprocess.Popen([sys.executable, '-c',
                                    'import time;time.sleep(30)'])
        self.addCleanup(process.wait)
        externalprocess._all_procs.clear()
        externalprocess._all_procs.add(process)

        externalprocess.cleanup()

        self.assertIsNotNone(process.poll() if process.poll() is not None
                             else process.wait())

    def test_an_error_while_killing_is_swallowed(self):
        class Stubborn(object):
            def terminate(self):
                raise OSError('cannot terminate')

            def kill(self):
                raise OSError('cannot kill')

        externalprocess._all_procs.clear()
        externalprocess._all_procs.add(Stubborn())

        externalprocess.cleanup()

    def test_a_wrapper_object_is_unwrapped_before_killing(self):
        import subprocess

        class Wrapper(object):
            def __init__(self, proc):
                self.proc = proc

        process = subprocess.Popen([sys.executable, '-c',
                                    'import time;time.sleep(30)'])
        self.addCleanup(process.wait)
        externalprocess._all_procs.clear()
        externalprocess._all_procs.add(Wrapper(process))

        externalprocess.cleanup()

        self.assertIsNotNone(process.wait())
