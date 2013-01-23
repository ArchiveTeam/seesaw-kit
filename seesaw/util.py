import subprocess

def test_executable(name, version, path):
  print "Looking for %s in %s" % (name, path)
  try:
    result = subprocess.check_output([path, "-V"])
    if not version in result:
      print "%s: Incorrect %s version (want %s)." % (path, name, version)
      return False
    else:
      print "Found usable %s in %s" % (name, path)
      return True
  except subprocess.CalledProcessError as e:
    print "%s:" % path, e
  except OSError as e:
    print "%s:" % path, e

def find_executable(name, version, paths):
  for path in paths:
    if test_executable(name, version, path):
      return path
  return None

