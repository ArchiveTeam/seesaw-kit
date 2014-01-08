from seesaw.externalprocess import ExternalProcess
from seesaw.pipeline import Pipeline
from seesaw.runner import SimpleRunner
from seesaw.task import PrintItem, SimpleTask
from seesaw.test_base import BaseTestCase


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
