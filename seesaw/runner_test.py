from seesaw.pipeline import Pipeline
from seesaw.runner import SimpleRunner
from seesaw.task import PrintItem, SimpleTask
from seesaw.test_base import BaseTestCase

class RunnerTest(BaseTestCase):
    def setUp(self):
        super(RunnerTest, self).setUp()

        self.cleanup_calls = 0

    def test_runner_does_pipeline_cleanup_before_shutdown(self):
        pipeline = Pipeline(PrintItem())
        runner = SimpleRunner(pipeline, max_items=1)

        def cleanup():
            self.cleanup_calls += 1

        pipeline.on_cleanup += cleanup
        runner.start()

        self.assertEqual(1, self.cleanup_calls)
        self.assertEqual(1, runner.item_count)
