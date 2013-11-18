import distutils.version
import seesaw
import unittest


class InitTest(unittest.TestCase):
    def test_valid_version(self):
        '''It should not raise ValueError.'''
        distutils.version.StrictVersion(seesaw.__version__)

    def test_valid_build_number(self):
        '''It should match the version string.'''
        version = distutils.version.StrictVersion(seesaw.__version__)
        major_ver, minor_ver, patch_ver = version.version
        major_build_ver = (seesaw.__build__ & 0xff0000) >> 16
        minor_build_ver = (seesaw.__build__ & 0xff00) >> 8
        patch_build_ver = seesaw.__build__ & 0xff

        self.assertEqual(major_ver, major_build_ver)
        self.assertEqual(minor_ver, minor_build_ver)
        self.assertEqual(patch_ver, patch_build_ver)
