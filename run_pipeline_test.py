import unittest
import subprocess


class RunPipelineTest(unittest.TestCase):
    def test_example_pipeline(self):
        subprocess.check_call([
            './run-pipeline',
            'example-pipeline.py',
            'testuser',
            '--max-items', '1',
            '--disable-web-server'
        ])

