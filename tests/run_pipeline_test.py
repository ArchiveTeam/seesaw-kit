import unittest
import subprocess
import sys


class RunPipelineTest(unittest.TestCase):
    def test_example_pipeline(self):
        if sys.version_info[0] == 3:
            python_exe = 'python3'
        else:
            python_exe = 'python'

        subprocess.check_call([
            python_exe,
            './run-pipeline',
            './examples/example-pipeline.py',
            'testuser',
            '--max-items', '1',
            '--disable-web-server'
        ])

