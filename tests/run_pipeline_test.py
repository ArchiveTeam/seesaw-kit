import shutil
import unittest
import subprocess


# run-pipeline3 is a console entry point, so it only exists once the package
# is installed. Skip rather than fail when it is not on PATH.
PIPELINE_EXE = shutil.which('run-pipeline3')


@unittest.skipUnless(PIPELINE_EXE, 'run-pipeline3 is not installed')
class RunPipelineTest(unittest.TestCase):
    def test_example_pipeline(self):
        subprocess.check_call([
            PIPELINE_EXE,
            './examples/example-pipeline.py',
            'testuser',
            '--max-items', '1',
            '--disable-web-server'
        ])

