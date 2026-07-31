import os
import os.path
import re
import shutil
import tempfile
import time
import unittest

import seesaw
import seesaw.util
from seesaw.util import find_executable, unique_id_str


class UtilTest(unittest.TestCase):
    def test_find_executable(self):
        exes = ['./run-pipeline3', '../run-pipeline3']

        self.assertTrue(find_executable(
            'pipeline runner',
            seesaw.__version__,
            exes,
            version_arg='--version')
        )

    def test_find_executable_regex_version(self):
        exes = ['./run-pipeline3', '../run-pipeline3']

        self.assertTrue(find_executable(
            'pipeline runner',
            re.compile(seesaw.__version__.replace('.', '\\.')),
            exes,
            version_arg='--version')
        )

    def test_find_executable_list_version(self):
        exes = ['./run-pipeline3', '../run-pipeline3']

        self.assertTrue(find_executable(
            'pipeline runner',
            [seesaw.__version__],
            exes,
            version_arg='--version')
        )

    def test_find_executable_bad_version(self):
        exes = ['./run-pipeline3', '../run-pipeline3']

        self.assertFalse(find_executable(
            'pipeline runner',
            '123-notrealversion',
            exes,
            version_arg='--version')
        )

    def test_unique_id_str(self):
        # check for no crash
        self.assertTrue(unique_id_str())


class TestExecutableTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.temp_dir, True)

    def write_script(self, body, name='tool'):
        path = os.path.join(self.temp_dir, name)
        with open(path, 'w') as file_obj:
            file_obj.write('#!/bin/sh\n' + body)
        os.chmod(path, 0o755)
        return path

    def check(self, version, path, **kwargs):
        # Reached through the module because pytest would otherwise collect
        # an imported name starting with "test_" as a test function.
        return seesaw.util.test_executable('tool', version, path, **kwargs)

    def test_accepts_a_matching_version_string(self):
        path = self.write_script('echo "tool version 1.2.3"\n')

        self.assertTrue(self.check('1.2.3', path))

    def test_rejects_a_different_version_string(self):
        path = self.write_script('echo "tool version 1.2.3"\n')

        self.assertFalse(self.check('9.9.9', path))

    def test_reads_the_version_from_stderr_too(self):
        path = self.write_script('echo "tool version 1.2.3" >&2\n')

        self.assertTrue(self.check('1.2.3', path))

    def test_rejects_a_nonzero_exit_code(self):
        path = self.write_script('echo "tool version 1.2.3"\nexit 1\n')

        self.assertFalse(self.check('1.2.3', path))

    def test_accepts_a_matching_regex(self):
        path = self.write_script('echo "tool version 1.2.3"\n')

        self.assertTrue(self.check(re.compile(r'version 1\.\d+\.\d+'), path))

    def test_rejects_a_regex_that_does_not_match(self):
        path = self.write_script('echo "tool version 1.2.3"\n')

        self.assertFalse(self.check(re.compile(r'version 9\.'), path))

    def test_accepts_any_version_from_a_list(self):
        path = self.write_script('echo "tool version 1.2.3"\n')

        self.assertTrue(self.check(['9.9.9', '1.2.3'], path))

    def test_rejects_a_list_with_no_match(self):
        path = self.write_script('echo "tool version 1.2.3"\n')

        self.assertFalse(self.check(['9.9.9', '8.8.8'], path))

    def test_passes_the_version_argument(self):
        path = self.write_script('echo "called with $1"\n')

        self.assertTrue(self.check('called with --version', path,
                                   version_arg='--version'))

    def test_a_missing_executable_is_not_usable(self):
        self.assertFalse(
            self.check('1.2.3', os.path.join(self.temp_dir, 'no-such-tool')))

    def test_find_executable_returns_none_when_nothing_matches(self):
        self.assertEqual(
            None,
            find_executable('tool', '1.2.3',
                            [os.path.join(self.temp_dir, 'missing')]))

    def test_find_executable_returns_the_first_match(self):
        first = self.write_script('exit 1\n', name='first')
        second = self.write_script('echo "tool version 1.2.3"\n',
                                   name='second')

        self.assertEqual(second,
                         find_executable('tool', '1.2.3', [first, second]))


class UniqueIdTest(unittest.TestCase):
    def test_ids_are_unique(self):
        ids = set(unique_id_str() for dummy in range(100))

        self.assertEqual(100, len(ids))

    def test_the_id_starts_with_the_current_time(self):
        before = int(time.time())
        unique_id = unique_id_str()

        self.assertTrue(unique_id.startswith(str(before)) or
                        unique_id.startswith(str(before + 1)))

    def test_the_id_is_lowercase_hexadecimal_after_the_timestamp(self):
        unique_id = unique_id_str()

        self.assertEqual(16, len(unique_id[-16:]))
        self.assertEqual(unique_id.lower(), unique_id)
        int(unique_id[-16:], 16)
