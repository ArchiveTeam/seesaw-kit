import errno
import os
import os.path
import shutil
import sys
import tempfile

from seesaw.pipeline import Pipeline
from seesaw.runner import Runner, SimpleRunner
from seesaw.six import StringIO
from seesaw.task import PrintItem, SetItemKey, SimpleTask, Task
from tests.test_base import BaseTestCase


class RunnerTest(BaseTestCase):
    def setUp(self):
        super(RunnerTest, self).setUp()

        self.cleanup_calls = 0
        self.stop_canceled_calls = 0
        self.stop_requested_calls = 0

    def test_runner_does_pipeline_cleanup_before_shutdown(self):
        pipeline = Pipeline(PrintItem())
        runner = SimpleRunner(pipeline, max_items=1)

        def cleanup():
            self.cleanup_calls += 1

        pipeline.on_cleanup += cleanup
        runner.start()

        self.assertEqual(1, self.cleanup_calls)
        self.assertEqual(1, runner.item_count)

    def test_runner_signals_pipeline_on_stop(self):
        pipeline = Pipeline(PrintItem())
        runner = SimpleRunner(pipeline, max_items=1)

        def stop_requested():
            self.stop_requested_calls += 1

        pipeline.on_stop_requested += stop_requested
        runner.start()
        runner.stop_gracefully()

        self.assertEqual(1, self.stop_requested_calls)

    def test_runner_signals_pipeline_when_stop_canceled(self):
        pipeline = Pipeline(PrintItem())
        runner = SimpleRunner(pipeline, max_items=1)

        def stop_canceled():
            self.stop_canceled_calls += 1

        pipeline.on_stop_canceled += stop_canceled
        runner.start()
        runner.stop_gracefully()
        runner.keep_running()

        self.assertEqual(1, self.stop_canceled_calls)


class RecordingTask(SimpleTask):
    def __init__(self, name='RecordingTask'):
        SimpleTask.__init__(self, name)
        self.processed = []

    def process(self, item):
        self.processed.append(item)


class FailingTask(SimpleTask):
    def __init__(self):
        SimpleTask.__init__(self, 'FailingTask')

    def process(self, item):
        raise ValueError('boom')


class HoldingTask(Task):
    '''Keeps items in flight until :meth:`release` is called.

    Tasks that finish synchronously never let more than one item be active
    at a time, which hides the runner's concurrency bookkeeping.
    '''
    def __init__(self):
        Task.__init__(self, 'HoldingTask')
        self.held = []

    def enqueue(self, item):
        self.start_item(item)
        self.held.append(item)

    def release(self):
        self.complete_item(self.held.pop(0))


class RunnerBehaviourTest(BaseTestCase):
    def test_creates_items_with_increasing_numbers(self):
        recorder = RecordingTask()
        runner = SimpleRunner(Pipeline(recorder), max_items=3)

        runner.start()

        self.assertEqual([1, 2, 3],
                         [item.item_number for item in recorder.processed])
        self.assertEqual(3, runner.item_count)

    def test_item_ids_are_unique(self):
        recorder = RecordingTask()
        runner = SimpleRunner(Pipeline(recorder), max_items=5)

        runner.start()

        item_ids = [item.item_id for item in recorder.processed]
        self.assertEqual(5, len(set(item_ids)))

    def test_runs_several_items_concurrently(self):
        task = HoldingTask()
        runner = Runner(concurrent_items=3, max_items=5)
        runner.set_current_pipeline(Pipeline(task))

        runner.start()

        self.assertEqual(3, len(task.held))
        self.assertEqual(3, len(runner.active_items))

    def test_does_not_exceed_max_items(self):
        task = HoldingTask()
        runner = Runner(concurrent_items=5, max_items=2)
        runner.set_current_pipeline(Pipeline(task))

        runner.start()

        self.assertEqual(2, len(task.held))
        self.assertEqual(2, runner.item_count)

    def test_is_active_reflects_the_items_in_flight(self):
        task = HoldingTask()
        runner = Runner(concurrent_items=1, max_items=1)
        runner.set_current_pipeline(Pipeline(task))

        self.assertFalse(runner.is_active())

        runner.start()

        self.assertTrue(runner.is_active())

    def test_a_failing_item_still_finishes_the_run(self):
        finished = []
        runner = SimpleRunner(Pipeline(FailingTask()), max_items=2)
        runner.on_pipeline_finish_item += \
            lambda r, p, item: finished.append(item)

        runner.start()

        self.assertEqual(2, len(finished))
        self.assertTrue(all(item.failed for item in finished))

    def test_on_finish_fires_when_no_items_are_left(self):
        finishes = []
        runner = SimpleRunner(Pipeline(RecordingTask()), max_items=2)
        runner.on_finish += finishes.append

        runner.start()

        self.assertTrue(finishes)
        self.assertEqual({runner}, set(finishes))
        self.assertFalse(runner.is_active())

    def test_keep_data_is_passed_on_to_items(self):
        recorder = RecordingTask()
        runner = SimpleRunner(Pipeline(recorder), max_items=1,
                              keep_data=True)

        runner.start()

        self.assertTrue(recorder.processed[0]._keep_data)

    def test_a_runner_without_a_pipeline_creates_nothing(self):
        runner = Runner(max_items=3)

        runner.add_items()

        self.assertEqual(0, runner.item_count)

    def test_set_current_pipeline_cancels_the_previous_ones_items(self):
        task = HoldingTask()
        first = Pipeline(task)
        runner = Runner(concurrent_items=1, max_items=1, keep_data=True)
        runner.set_current_pipeline(first)
        runner.start()

        item = task.held[0]
        item.may_be_canceled = True

        runner.set_current_pipeline(Pipeline(HoldingTask()))

        self.assertTrue(item.canceled)
        self.assertEqual(set(), first.items_in_pipeline)
        self.assertFalse(runner.is_active())

    def test_set_current_pipeline_keeps_items_that_may_not_be_canceled(self):
        task = HoldingTask()
        first = Pipeline(task)
        runner = Runner(concurrent_items=1, max_items=1, keep_data=True)
        runner.set_current_pipeline(first)
        runner.start()

        runner.set_current_pipeline(Pipeline(HoldingTask()))

        self.assertFalse(task.held[0].canceled)
        self.assertTrue(runner.is_active())

    def test_stop_gracefully_cancels_the_cancellable_items(self):
        task = HoldingTask()
        pipeline = Pipeline(task)
        runner = Runner(concurrent_items=2, max_items=2, keep_data=True)
        runner.set_current_pipeline(pipeline)
        runner.start()

        cancelable, keeper = task.held
        cancelable.may_be_canceled = True

        runner.stop_gracefully()

        self.assertTrue(runner.should_stop())
        self.assertTrue(cancelable.canceled)
        self.assertFalse(keeper.canceled)

    def test_on_status_reports_stopping_and_running(self):
        statuses = []
        runner = SimpleRunner(Pipeline(RecordingTask()), max_items=1)
        runner.on_status += lambda r, status: statuses.append(status)

        runner.start()
        runner.stop_gracefully()
        runner.keep_running()

        self.assertEqual(['stopping', 'running'], statuses)


class RunnerStopFileTest(BaseTestCase):
    def setUp(self):
        super(RunnerStopFileTest, self).setUp()
        self.temp_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.temp_dir, True)
        self.stop_file = os.path.join(self.temp_dir, 'STOP')

    def test_no_stop_file_means_no_change(self):
        runner = Runner(stop_file=self.stop_file)

        self.assertEqual(None, runner.stop_file_mtime())
        self.assertFalse(runner.stop_file_changed())
        self.assertFalse(runner.should_stop())

    def test_creating_the_stop_file_is_a_change(self):
        runner = Runner(stop_file=self.stop_file)

        open(self.stop_file, 'wb').close()

        self.assertTrue(runner.stop_file_changed())
        self.assertTrue(runner.should_stop())

    def test_a_preexisting_stop_file_is_not_a_change(self):
        open(self.stop_file, 'wb').close()

        runner = Runner(stop_file=self.stop_file)

        self.assertFalse(runner.stop_file_changed())

    def test_touching_the_stop_file_again_is_a_change(self):
        open(self.stop_file, 'wb').close()
        runner = Runner(stop_file=self.stop_file)

        os.utime(self.stop_file,
                 (runner.initial_stop_file_mtime + 10,
                  runner.initial_stop_file_mtime + 10))

        self.assertTrue(runner.stop_file_changed())

    def test_check_stop_file_stops_the_runner(self):
        pipeline = Pipeline(PrintItem())
        runner = SimpleRunner(pipeline, stop_file=self.stop_file, max_items=1)
        open(self.stop_file, 'wb').close()

        runner.check_stop_file()

        self.assertTrue(runner.stop_flag)

    def test_stop_gracefully_rebaselines_the_stop_file(self):
        pipeline = Pipeline(PrintItem())
        runner = SimpleRunner(pipeline, stop_file=self.stop_file, max_items=1)
        open(self.stop_file, 'wb').close()

        runner.stop_gracefully()

        self.assertFalse(runner.stop_file_changed())
        self.assertTrue(runner.should_stop())

        runner.keep_running()

        self.assertFalse(runner.should_stop())


class RunnerOutputTest(BaseTestCase):
    def test_item_output_is_written_to_stdout(self):
        original_stdout = sys.stdout
        sys.stdout = StringIO()
        try:
            pipeline = Pipeline(SetItemKey('item_name', 'example'),
                                PrintItem())
            SimpleRunner(pipeline, max_items=1).start()
            written = sys.stdout.getvalue()
        finally:
            sys.stdout = original_stdout

        self.assertIn("<Item 'example'", written)

    def test_undecodable_output_falls_back_to_ascii(self):
        class NarrowStdout(object):
            def __init__(self):
                self.written = []
                self.allow_unicode = False

            def write(self, data):
                if not self.allow_unicode and any(ord(c) > 127 for c in data):
                    raise UnicodeError('cannot encode')
                self.written.append(data)

            def flush(self):
                pass

        narrow = NarrowStdout()
        original_stdout = sys.stdout
        sys.stdout = narrow
        try:
            pipeline = Pipeline(SetItemKey('item_name', 'wörld'), PrintItem())
            SimpleRunner(pipeline, max_items=1).start()
        finally:
            sys.stdout = original_stdout

        self.assertIn("<Item 'w?rld'", ''.join(narrow.written))


class RunnerInterruptedOutputTest(BaseTestCase):
    '''Signals can interrupt a write to stdout; the runner retries.'''

    class InterruptingStdout(object):
        def __init__(self, failures):
            self.failures_left = failures
            self.written = []

        def write(self, data):
            if self.failures_left:
                self.failures_left -= 1
                raise IOError(errno.EINTR, 'Interrupted system call')
            self.written.append(data)

        def flush(self):
            pass

    def run_with_stdout(self, stdout):
        original_stdout = sys.stdout
        sys.stdout = stdout
        try:
            pipeline = Pipeline(SetItemKey('item_name', 'example'),
                                PrintItem())
            SimpleRunner(pipeline, max_items=1).start()
        finally:
            sys.stdout = original_stdout

    def test_an_interrupted_write_is_retried(self):
        stdout = self.InterruptingStdout(failures=2)

        self.run_with_stdout(stdout)

        self.assertIn("<Item 'example'", ''.join(stdout.written))

    def test_any_other_io_error_is_raised(self):
        class BrokenStdout(object):
            def write(self, data):
                raise IOError(errno.EPIPE, 'Broken pipe')

            def flush(self):
                pass

        runner = SimpleRunner(Pipeline(PrintItem()), max_items=1)
        original_stdout = sys.stdout
        sys.stdout = BrokenStdout()
        try:
            self.assertRaises(IOError, runner._handle_item_output,
                              None, 'some output')
        finally:
            sys.stdout = original_stdout
