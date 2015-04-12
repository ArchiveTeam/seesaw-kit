import unittest
import re

import seesaw
import seesaw.six
from seesaw.util import find_executable, unique_id_str


class UtilTest(unittest.TestCase):
    def test_find_executable(self):
        if seesaw.six.PY3:
            exes = ['./run-pipeline3', '../run-pipeline3']
        else:
            exes = ['./run-pipeline', '../run-pipeline']

        self.assertTrue(find_executable(
            'pipeline runner',
            seesaw.__version__,
            exes,
            version_arg='--version')
        )

    def test_find_executable_regex_version(self):
        if seesaw.six.PY3:
            exes = ['./run-pipeline3', '../run-pipeline3']
        else:
            exes = ['./run-pipeline', '../run-pipeline']

        self.assertTrue(find_executable(
            'pipeline runner',
            re.compile(seesaw.__version__.replace('.', '\\.')),
            exes,
            version_arg='--version')
        )

    def test_find_executable_list_version(self):
        if seesaw.six.PY3:
            exes = ['./run-pipeline3', '../run-pipeline3']
        else:
            exes = ['./run-pipeline', '../run-pipeline']

        self.assertTrue(find_executable(
            'pipeline runner',
            [seesaw.__version__],
            exes,
            version_arg='--version')
        )

    def test_find_executable_bad_version(self):
        if seesaw.six.PY3:
            exes = ['./run-pipeline3', '../run-pipeline3']
        else:
            exes = ['./run-pipeline', '../run-pipeline']

        self.assertFalse(find_executable(
            'pipeline runner',
            '123-notrealversion',
            exes,
            version_arg='--version')
        )

    def test_unique_id_str(self):
        # check for no crash
        self.assertTrue(unique_id_str())
