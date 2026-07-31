import unittest
import subprocess
import sys


class RunPipelineTest(unittest.TestCase):
    def test_example_pipeline(self):
        subprocess.check_call([
            sys.executable,
            './run-pipeline3',
            './examples/example-pipeline.py',
            'testuser',
            '--max-items', '1',
            '--disable-web-server'
        ])

