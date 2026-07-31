import re
import seesaw
import unittest


# Replaces distutils.version.StrictVersion, which was removed from the
# standard library in Python 3.12. Kept local so the package does not grow a
# dependency on `packaging` just for this check.
STRICT_VERSION_RE = re.compile(
    r'^(\d+)\.(\d+)(?:\.(\d+))?(?:[ab]\d+)?$')


def parse_strict_version(version_string):
    '''Parse a strict "X.Y[.Z]" version into a 3-tuple of ints.

    Raises:
        ValueError: If the version string is not strictly formatted.
    '''
    match = STRICT_VERSION_RE.match(version_string)

    if not match:
        raise ValueError(
            'invalid version number %r' % (version_string,))

    major, minor, patch = match.groups()

    return (int(major), int(minor), int(patch or 0))


class InitTest(unittest.TestCase):
    def test_valid_version(self):
        '''It should not raise ValueError.'''
        parse_strict_version(seesaw.__version__)

    def test_valid_build_number(self):
        '''It should match the version string.'''
        major_ver, minor_ver, patch_ver = \
            parse_strict_version(seesaw.__version__)
        major_build_ver = (seesaw.__build__ & 0xff0000) >> 16
        minor_build_ver = (seesaw.__build__ & 0xff00) >> 8
        patch_build_ver = seesaw.__build__ & 0xff

        self.assertEqual(major_ver, major_build_ver)
        self.assertEqual(minor_ver, minor_build_ver)
        self.assertEqual(patch_ver, patch_build_ver)

    def test_rejects_invalid_version(self):
        '''It should raise ValueError on non-strict versions.'''
        for bad_version in ('1', '1.2.3.4', 'abc', '', '1.2.x'):
            self.assertRaises(
                ValueError, parse_strict_version, bad_version)
