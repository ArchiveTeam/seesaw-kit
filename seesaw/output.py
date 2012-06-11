import sys

class StringOutputCollector(object):
  def __init__(self):
    self.parts = []

  def append(self, data):
    self.parts.append(data)

  def __str__(self):
    return "".join(self.parts)

class StdoutOutputCollector(object):
  def __init__(self):
    self.parts = []

  def append(self, data):
    sys.stdout.write(data)
    self.parts.append(data)

  def __str__(self):
    return "".join(self.parts)

