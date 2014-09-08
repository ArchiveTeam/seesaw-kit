from seesaw.pipeline import Pipeline
from seesaw.runner import SimpleRunner
from seesaw.task import PrintItem, SimpleTask
from seesaw.test_base import BaseTestCase

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
