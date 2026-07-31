import os
import os.path

from seesaw.externalprocess import ExternalProcess
from seesaw.item import Item
from seesaw.pipeline import Pipeline
from seesaw.project import Project
from seesaw.runner import SimpleRunner
from seesaw.task import PrintItem, SetItemKey, SimpleTask, Task
from tests.test_base import BaseTestCase


class ExternalProcessTest(BaseTestCase):
    def test_max_items(self):
        pipeline = Pipeline(PrintItem(), PrintItem())
        pipeline.has_failed = None

        def fail_callback(task, item):
            pipeline.has_failed = True

        pipeline.on_fail_item += fail_callback

        runner = SimpleRunner(pipeline, max_items=3)

        def finish_item_callback(runner, pipeline, item):
            if runner.item_count > 10:
                raise Exception('Too many items.')

        runner.on_pipeline_finish_item += finish_item_callback
        runner.start()

        self.assertFalse(pipeline.has_failed)
        self.assertEqual(3, runner.item_count)
        self.assertIOLoopOK()

    def test_max_items_with_subproc(self):
        pipeline = Pipeline(PrintItem(), PrintItem(),
            ExternalProcess("pwd", ["pwd"]))
        pipeline.has_failed = None

        def fail_callback(task, item):
            pipeline.has_failed = True

        pipeline.on_fail_item += fail_callback

        runner = SimpleRunner(pipeline, max_items=3)

        def finish_item_callback(runner, pipeline, item):
            if runner.item_count > 10:
                raise Exception('Too many items.')

        runner.on_pipeline_finish_item += finish_item_callback
        runner.start()

        self.assertFalse(pipeline.has_failed)
        self.assertEqual(3, runner.item_count)
        self.assertIOLoopOK()

    def test_no_stack_overflow(self):
        pipeline = Pipeline(PrintItem())
        pipeline.has_failed = None

        def fail_callback(task, item):
            pipeline.has_failed = True

        pipeline.on_fail_item += fail_callback

        runner = SimpleRunner(pipeline, max_items=50)

        def finish_item_callback(runner, pipeline, item):
            if runner.item_count > 200:
                raise Exception('Too many items.')

        runner.on_pipeline_finish_item += finish_item_callback
        runner.start()

        self.assertFalse(pipeline.has_failed)
        self.assertEqual(50, runner.item_count)
        self.assertIOLoopOK()

    def test_spurious_item_events(self):
        class StupidTask(SimpleTask):
            def __init__(self):
                SimpleTask.__init__(self, "StupidTask")

            def process(self, item):
                item.log_output('Failing the item.')
                self.fail_item(item)
                item.log_output('Completing the item.')
                self.complete_item(item)
                item.log_output('Failing the item.')
                self.fail_item(item)

        pipeline = Pipeline(StupidTask())
        pipeline.fail_count_test = 0

        def fail_callback(task, item):
            pipeline.fail_count_test += 1

        pipeline.on_fail_item += fail_callback

        runner = SimpleRunner(pipeline, max_items=1)
        runner.start()

        self.assertEqual(1, pipeline.fail_count_test)
        self.assertIOLoopOK()


class RecordingTask(SimpleTask):
    def __init__(self, name):
        SimpleTask.__init__(self, name)
        self.processed = []

    def process(self, item):
        self.processed.append(item)


class PipelineTest(BaseTestCase):
    def test_defaults_to_a_data_directory_under_the_cwd(self):
        pipeline = Pipeline()

        self.assertEqual(os.path.join(os.getcwd(), 'data'),
                         pipeline.data_dir)
        self.assertEqual(None, pipeline.project)

    def test_collects_the_tasks_it_was_given(self):
        first = RecordingTask('First')
        second = RecordingTask('Second')

        pipeline = Pipeline(first, second)

        self.assertEqual([first, second], pipeline.tasks)

    def test_add_task_appends_to_the_end(self):
        pipeline = Pipeline(RecordingTask('First'))
        second = RecordingTask('Second')

        pipeline.add_task(second)

        self.assertEqual(second, pipeline.tasks[-1])

    def test_runs_the_tasks_in_order(self):
        order = []

        class Ordered(SimpleTask):
            def process(self, item):
                order.append(self.name)

        pipeline = Pipeline(Ordered('First'), Ordered('Second'),
                            Ordered('Third'))
        SimpleRunner(pipeline, max_items=1).start()

        self.assertEqual(['First', 'Second', 'Third'], order)
        self.assertIOLoopOK()

    def test_a_failing_task_stops_the_remaining_tasks(self):
        never_run = RecordingTask('NeverRun')

        class Failing(SimpleTask):
            def process(self, item):
                raise ValueError('boom')

        pipeline = Pipeline(Failing('Failing'), never_run)
        SimpleRunner(pipeline, max_items=1).start()

        self.assertEqual([], never_run.processed)
        self.assertIOLoopOK()

    def test_fires_the_item_lifecycle_events(self):
        events = []
        pipeline = Pipeline(PrintItem())
        pipeline.on_start_item += lambda p, item: events.append('start')
        pipeline.on_complete_item += lambda p, item: events.append('complete')
        pipeline.on_finish_item += lambda p, item: events.append('finish')

        SimpleRunner(pipeline, max_items=1).start()

        self.assertEqual(['start', 'complete', 'finish'], events)
        self.assertIOLoopOK()

    def test_the_item_leaves_the_pipeline_when_it_finishes(self):
        pipeline = Pipeline(PrintItem())

        SimpleRunner(pipeline, max_items=2).start()

        self.assertEqual(set(), pipeline.items_in_pipeline)
        self.assertIOLoopOK()

    def test_cancel_items_only_cancels_cancellable_ones(self):
        pipeline = Pipeline(PrintItem())
        cancelable = Item(pipeline, 'cancel-me', 1, keep_data=True,
                          prepare_data_directory=False)
        cancelable.may_be_canceled = True
        keeper = Item(pipeline, 'keep-me', 2, keep_data=True,
                      prepare_data_directory=False)
        pipeline.items_in_pipeline.update([cancelable, keeper])
        canceled = []
        pipeline.on_cancel_item += lambda p, item: canceled.append(item)

        pipeline.cancel_items()

        self.assertEqual([cancelable], canceled)
        self.assertTrue(cancelable.canceled)
        self.assertEqual({keeper}, pipeline.items_in_pipeline)

    def test_cancelling_an_item_not_in_the_pipeline_is_ignored(self):
        pipeline = Pipeline(PrintItem())
        item = Item(pipeline, 'stray', 1, keep_data=True,
                    prepare_data_directory=False)
        canceled = []
        pipeline.on_cancel_item += lambda p, i: canceled.append(i)

        pipeline._cancel_item(item)

        self.assertEqual([], canceled)
        self.assertFalse(item.canceled)

    def test_ui_task_list_names_every_task(self):
        first = RecordingTask('First')
        second = RecordingTask('Second')

        pipeline = Pipeline(first, second)

        self.assertEqual([(first, 'First'), (second, 'Second')],
                         pipeline.ui_task_list())

    def test_str_lists_the_tasks(self):
        pipeline = Pipeline(RecordingTask('First'), RecordingTask('Second'))

        self.assertEqual('Pipeline:\n -> First\n -> Second', str(pipeline))

    def test_a_project_can_be_attached(self):
        pipeline = Pipeline(PrintItem())
        project = Project(title='Example')

        pipeline.project = project

        self.assertEqual('Example', pipeline.project.title)

    def test_an_exception_from_enqueue_fails_the_item(self):
        class Exploding(Task):
            def enqueue(self, item):
                self.start_item(item)
                raise ValueError('boom in enqueue')

        failed = []
        pipeline = Pipeline(Exploding('Exploding'))
        pipeline.on_fail_item += lambda p, item: failed.append(item)

        SimpleRunner(pipeline, max_items=1).start()

        self.assertEqual(1, len(failed))
        self.assertIOLoopOK()

    def test_item_properties_carry_between_tasks(self):
        recorder = RecordingTask('Recorder')
        pipeline = Pipeline(SetItemKey('carried', 'value'), recorder)

        SimpleRunner(pipeline, max_items=1).start()

        self.assertEqual('value', recorder.processed[0]['carried'])
        self.assertIOLoopOK()
