# encoding=utf8
from __future__ import unicode_literals

import os
import os.path
import shutil
import tempfile

from seesaw.config import ConfigInterpolation
from seesaw.item import Item, ItemValue
from seesaw.pipeline import Pipeline
from seesaw.runner import SimpleRunner
from seesaw.task import ConditionalTask, LimitConcurrent, PrintItem, \
    SetItemKey, SimpleTask, Task
from tests.test_base import BaseTestCase


class RecordingTask(SimpleTask):
    '''A task that notes every item it processed.'''
    def __init__(self, name='RecordingTask'):
        SimpleTask.__init__(self, name)
        self.processed = []

    def process(self, item):
        self.processed.append(item)


class ExplodingTask(SimpleTask):
    def __init__(self):
        SimpleTask.__init__(self, 'ExplodingTask')

    def process(self, item):
        raise ValueError('boom')


class SlowTask(Task):
    '''Completes items only when :meth:`release` is called.'''
    def __init__(self):
        Task.__init__(self, 'SlowTask')
        self.pending = []
        self.started = []

    def enqueue(self, item):
        self.start_item(item)
        self.started.append(item)
        self.pending.append(item)

    def release(self):
        self.complete_item(self.pending.pop(0))

    def release_failing(self):
        self.fail_item(self.pending.pop(0))


class ExplodingEnqueueTask(Task):
    def __init__(self):
        Task.__init__(self, 'ExplodingEnqueueTask')

    def enqueue(self, item):
        self.start_item(item)
        raise ValueError('boom in enqueue')


def make_item(keep_data=True):
    class _Pipeline(object):
        data_dir = None

    return Item(_Pipeline(), 'the-id', 1, keep_data=keep_data,
                prepare_data_directory=False)


class TaskTest(BaseTestCase):
    def test_start_complete_and_fail_set_the_task_status(self):
        task = Task('Whatever')
        item = make_item()

        task.start_item(item)
        self.assertEqual(Item.TaskStatus.running, item.task_status[task])

        task.complete_item(item)
        self.assertEqual(Item.TaskStatus.completed, item.task_status[task])

        task.fail_item(item)
        self.assertEqual(Item.TaskStatus.failed, item.task_status[task])

    def test_fires_the_lifecycle_events(self):
        task = Task('Whatever')
        item = make_item()
        events = []
        task.on_start_item += lambda t, i: events.append('start')
        task.on_complete_item += lambda t, i: events.append('complete')
        task.on_fail_item += lambda t, i: events.append('fail')
        task.on_finish_item += lambda t, i: events.append('finish')

        task.start_item(item)
        task.complete_item(item)
        task.fail_item(item)

        self.assertEqual(['start', 'complete', 'finish', 'fail', 'finish'],
                         events)

    def test_task_cwd_restores_the_previous_directory(self):
        temp_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, temp_dir, True)
        task = Task('Whatever')
        task.cwd = temp_dir
        before = os.getcwd()

        with task.task_cwd():
            inside = os.getcwd()

        self.assertEqual(os.path.realpath(temp_dir),
                         os.path.realpath(inside))
        self.assertEqual(before, os.getcwd())

    def test_task_cwd_restores_the_directory_after_an_error(self):
        temp_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, temp_dir, True)
        task = Task('Whatever')
        task.cwd = temp_dir
        before = os.getcwd()

        def explode():
            with task.task_cwd():
                raise ValueError('boom')

        self.assertRaises(ValueError, explode)
        self.assertEqual(before, os.getcwd())

    def test_fill_ui_task_list_reports_itself(self):
        task = Task('Whatever')
        task_list = []

        task.fill_ui_task_list(task_list)

        self.assertEqual([(task, 'Whatever')], task_list)

    def test_str_is_the_name(self):
        self.assertEqual('Whatever', str(Task('Whatever')))


class SimpleTaskTest(BaseTestCase):
    def test_processes_and_completes_an_item(self):
        task = RecordingTask()
        pipeline = Pipeline(task)
        runner = SimpleRunner(pipeline, max_items=2)

        runner.start()

        self.assertEqual(2, len(task.processed))
        self.assertTrue(all(item.completed for item in task.processed))
        self.assertIOLoopOK()

    def test_an_exception_in_process_fails_the_item(self):
        failed = []
        pipeline = Pipeline(ExplodingTask())
        pipeline.on_fail_item += lambda p, item: failed.append(item)
        runner = SimpleRunner(pipeline, max_items=1)

        runner.start()

        self.assertEqual(1, len(failed))
        self.assertTrue(failed[0].failed)
        self.assertIOLoopOK()

    def test_the_failure_is_logged_on_the_item(self):
        output = []
        pipeline = Pipeline(ExplodingTask())
        runner = SimpleRunner(pipeline, max_items=1)
        runner.on_create_item += \
            lambda r, item: item.on_output.handle(
                lambda i, data: output.append(data))

        runner.start()

        text = ''.join(output)
        self.assertIn('Failed ExplodingTask', text)
        self.assertIn('ValueError: boom', text)
        self.assertIOLoopOK()

    def test_str_is_the_name(self):
        self.assertEqual('RecordingTask', str(RecordingTask()))


class SetItemKeyTest(BaseTestCase):
    def test_sets_a_plain_value(self):
        recorder = RecordingTask()
        pipeline = Pipeline(SetItemKey('greeting', 'hello'), recorder)
        runner = SimpleRunner(pipeline, max_items=1)

        runner.start()

        self.assertEqual('hello', recorder.processed[0]['greeting'])
        self.assertIOLoopOK()

    def test_realizes_the_value(self):
        recorder = RecordingTask()
        # SetItemKey realizes against the task, not the item, so a value that
        # reads from the item is not usable here.
        pipeline = Pipeline(
            SetItemKey('greeting', ConfigInterpolation('hello %s', ('you',))),
            recorder)
        runner = SimpleRunner(pipeline, max_items=1)

        runner.start()

        self.assertEqual('hello you', recorder.processed[0]['greeting'])
        self.assertIOLoopOK()

    def test_str_shows_the_key_and_value(self):
        self.assertEqual('SetItemKey(greeting: hello)',
                         str(SetItemKey('greeting', 'hello')))


class PrintItemTest(BaseTestCase):
    def test_writes_the_item_to_the_output(self):
        output = []
        pipeline = Pipeline(SetItemKey('item_name', 'example'), PrintItem())
        runner = SimpleRunner(pipeline, max_items=1)
        runner.on_create_item += \
            lambda r, item: item.on_output.handle(
                lambda i, data: output.append(data))

        runner.start()

        self.assertIn("<Item 'example'", ''.join(output))
        self.assertIOLoopOK()


class ConditionalTaskTest(BaseTestCase):
    def test_runs_the_inner_task_when_the_condition_holds(self):
        inner = RecordingTask()
        pipeline = Pipeline(ConditionalTask(lambda item: True, inner))
        runner = SimpleRunner(pipeline, max_items=1)

        runner.start()

        self.assertEqual(1, len(inner.processed))
        self.assertIOLoopOK()

    def test_skips_the_inner_task_when_the_condition_fails(self):
        inner = RecordingTask()
        completed = []
        pipeline = Pipeline(ConditionalTask(lambda item: False, inner))
        pipeline.on_complete_item += lambda p, item: completed.append(item)
        runner = SimpleRunner(pipeline, max_items=1)

        runner.start()

        self.assertEqual([], inner.processed)
        self.assertEqual(1, len(completed))
        self.assertIOLoopOK()

    def test_a_failing_inner_task_fails_the_item(self):
        failed = []
        pipeline = Pipeline(ConditionalTask(lambda item: True,
                                            ExplodingTask()))
        pipeline.on_fail_item += lambda p, item: failed.append(item)
        runner = SimpleRunner(pipeline, max_items=1)

        runner.start()

        self.assertEqual(1, len(failed))
        self.assertIOLoopOK()

    def test_an_exception_from_the_inner_enqueue_fails_the_item(self):
        failed = []
        pipeline = Pipeline(ConditionalTask(lambda item: True,
                                            ExplodingEnqueueTask()))
        pipeline.on_fail_item += lambda p, item: failed.append(item)
        runner = SimpleRunner(pipeline, max_items=1)

        runner.start()

        self.assertEqual(1, len(failed))
        self.assertIOLoopOK()

    def test_fill_ui_task_list_reports_the_inner_task(self):
        inner = RecordingTask()
        task_list = []

        ConditionalTask(lambda item: True, inner).fill_ui_task_list(task_list)

        self.assertEqual([(inner, 'RecordingTask')], task_list)

    def test_str_shows_the_inner_task(self):
        task = ConditionalTask(lambda item: True, RecordingTask())

        self.assertEqual('Conditional(RecordingTask)', str(task))


class LimitConcurrentTest(BaseTestCase):
    def setUp(self):
        super(LimitConcurrentTest, self).setUp()
        self.inner = SlowTask()

    def test_runs_up_to_the_limit_and_queues_the_rest(self):
        task = LimitConcurrent(2, self.inner)

        items = [make_item() for _ in range(5)]
        for item in items:
            task.enqueue(item)

        self.assertEqual(items[:2], self.inner.started)
        self.assertEqual(3, len(task._queue))

    def test_completing_an_item_starts_the_next_queued_one(self):
        task = LimitConcurrent(2, self.inner)
        completed = []
        task.on_complete_item += lambda t, item: completed.append(item)

        items = [make_item() for _ in range(4)]
        for item in items:
            task.enqueue(item)

        self.inner.release()

        self.assertEqual(items[:3], self.inner.started)
        self.assertEqual([items[0]], completed)

    def test_a_failing_item_also_starts_the_next_queued_one(self):
        task = LimitConcurrent(1, self.inner)
        failed = []
        task.on_fail_item += lambda t, item: failed.append(item)

        items = [make_item() for _ in range(2)]
        for item in items:
            task.enqueue(item)

        self.inner.release_failing()

        self.assertEqual(items, self.inner.started)
        self.assertEqual([items[0]], failed)

    def test_draining_the_queue_stops_starting_new_items(self):
        task = LimitConcurrent(1, self.inner)

        items = [make_item() for _ in range(2)]
        for item in items:
            task.enqueue(item)

        self.inner.release()
        self.inner.release()

        self.assertEqual(items, self.inner.started)
        self.assertEqual(0, task._working)
        self.assertEqual([], task._queue)

    def test_the_concurrency_limit_is_realized(self):
        task = LimitConcurrent(ItemValue('limit'), self.inner)

        item = make_item()
        item['limit'] = 1
        task.enqueue(item)

        other = make_item()
        other['limit'] = 1
        task.enqueue(other)

        self.assertEqual([item], self.inner.started)

    def test_runs_a_whole_pipeline(self):
        inner = RecordingTask()
        pipeline = Pipeline(LimitConcurrent(2, inner))
        runner = SimpleRunner(pipeline, concurrent_items=2, max_items=6)

        runner.start()

        self.assertEqual(6, len(inner.processed))
        self.assertIOLoopOK()

    def test_fill_ui_task_list_reports_the_inner_task(self):
        inner = RecordingTask()
        task_list = []

        LimitConcurrent(2, inner).fill_ui_task_list(task_list)

        self.assertEqual([(inner, 'RecordingTask')], task_list)

    def test_str_shows_the_limit_and_inner_task(self):
        task = LimitConcurrent(2, RecordingTask())

        self.assertEqual('LimitConcurrent(2 x RecordingTask )', str(task))
