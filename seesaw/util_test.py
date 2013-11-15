import unittest
from seesaw.util import find_executable
import seesaw


class UtilTest(unittest.TestCase):
    def test_find_executable(self):
        find_executable('run-pipeline', seesaw.__version__, ['.', '..'],
            version_arg='--version')
