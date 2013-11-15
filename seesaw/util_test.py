import unittest
from seesaw.util import find_executable
import seesaw


class UtilTest(unittest.TestCase):
    def test_find_executable(self):
        self.assertTrue(find_executable(
            'pipeline runner',
            seesaw.__version__,
            ['./run-pipeline', '../run-pipeline'],
            version_arg='--version')
        )

    def test_find_executable_bad_version(self):
        self.assertFalse(find_executable(
            'pipeline runner',
            '123-notrealversion',
            ['./run-pipeline', '../run-pipeline'],
            version_arg='--version')
        )
