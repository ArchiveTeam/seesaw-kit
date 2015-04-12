'''Miscellaneous functions.'''
import os
import subprocess
import time
import base64


def test_executable(name, version, path, version_arg="-V"):
    '''Try to run an executable and check its version.'''
    print("Looking for %s in %s" % (name, path))
    try:
        process = subprocess.Popen(
            [path, version_arg],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        stdout_data, stderr_data = process.communicate()
        result = stdout_data.decode('utf-8', 'replace') + \
            stderr_data.decode('utf-8', 'replace')

        if not process.returncode == 0:
            print("%s: Returned code %d" % (path, process.returncode))
            return False

        if isinstance(version, str):
            if version not in result:
                print("%s: Incorrect %s version (want %s)." % (path, name,
                                                               version))
                return False
        elif hasattr(version, "search"):
            if not version.search(result):
                print("%s: Incorrect %s version." % (path, name))
                return False
        elif hasattr(version, "__iter__"):
            if not any((v in result) for v in version):
                print("%s: Incorrect %s version (want %s)." % (path, name,
                                                               str(version)))
                return False

        print("Found usable %s in %s" % (name, path))
        return True
    except OSError as e:
        print("%s:" % path, e)
        return False


def find_executable(name, version, paths, version_arg="-V"):
    '''Returns the path of a matching executable.

    .. seealso:: :func:`test_executable`
    '''
    for path in paths:
        if test_executable(name, version, path, version_arg):
            return path
    return None


def unique_id_str():
    '''Returns a unique string suitable for IDs.'''
    rand_str = base64.b16encode(os.urandom(8)).decode('ascii').lower()
    return "{0}{1}".format(int(time.time()), rand_str)
