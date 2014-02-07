import unittest
import subprocess
import sys


class RunPipelineTest(unittest.TestCase):
    def test_example_pipeline(self):
        if sys.version_info[0] == 3:
            python_exe = 'python3'
            pipeline_exe = './run-pipeline3'
        else:
            python_exe = 'python'
            pipeline_exe = './run-pipeline'

        subprocess.check_call([
            python_exe,
            pipeline_exe,
            './examples/example-pipeline.py',
            'testuser',
            '--max-items', '1',
            '--disable-web-server'
        ])

