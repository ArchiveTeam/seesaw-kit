import re
import subprocess


def test_executable(name, version, path):
    print "Looking for %s in %s" % (name, path)
    try:
        process = subprocess.Popen([path, "-V"], stdout=subprocess.PIPE)
        result = process.communicate()[0]
        if not process.returncode == 0:
            print "%s: Returned code %d" % (path, process.returncode)
            return False

        if isinstance(version, basestring):
            if not version in result:
                print "%s: Incorrect %s version (want %s)." % (path, name, version)
                return False
        elif hasattr(version, "search"):
            if not version.search(result):
                print "%s: Incorrect %s version." % (path, name)
                return False
        elif hasattr(version, "__iter__"):
            if not any((v in result) for v in version):
                print "%s: Incorrect %s version (want %s)." % (path, name, str(version))
                return False

        print "Found usable %s in %s" % (name, path)
        return True
    except OSError as e:
        print "%s:" % path, e
        return False


def find_executable(name, version, paths):
    for path in paths:
        if test_executable(name, version, path):
            return path
    return None
