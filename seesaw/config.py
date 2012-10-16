import re

def realize(v, item=None):
  if isinstance(v, dict):
    realized_dict = {}
    for (key, value) in v.iteritems():
      realized_dict[key] = realize(value, item)
    return realized_dict
  elif isinstance(v, list):
    return [ realize(vi, item) for vi in v ]
  elif hasattr(v, "realize"):
    return v.realize(item)
  else:
    return v

class ConfigValue(object):
  collector = None

  @classmethod
  def start_collecting(cls):
    ConfigValue.collector = []

  @classmethod
  def stop_collecting(cls):
    collected = ConfigValue.collector
    ConfigValue.collector = None
    return collected

  def __init__(self, name, title="", description="", default=None, editable=True):
    self.name = name
    self.title = title
    self.description = description
    self.value = self.convert_value(default)
    self.error = None
    self.editable = editable

    if ConfigValue.collector != None:
      ConfigValue.collector.append(self)

  def realize(self, ignored):
    return self.value

  def set_value(self, value):
    self.error = self.check_value(value)
    if self.error == None:
      self.value = self.convert_value(value)
      return True
    else:
      return False

  def check_value(self, value):
    return None

  def convert_value(self, value):
    return value

  def is_valid(self):
    return self.value != None

  def __str__(self):
    return "<" + self.name + ":" + str(self.value) + ">"

class StringConfigValue(ConfigValue):
  def __init__(self, *args, **kwargs):
    if "regex" in kwargs:
      self.regex = kwargs["regex"]
      del kwargs["regex"]
    else:
      self.regex = None

    ConfigValue.__init__(self, **kwargs)

  def check_value(self, value):
    value = value.strip()
    if self.regex and not re.search(self.regex, value):
      return "Invalid value for %s." % self.title.lower()
    else:
      return None

class NumberConfigValue(ConfigValue):
  def __init__(self, *args, **kwargs):
    if "min" in kwargs:
      self.min = kwargs["min"]
      del kwargs["min"]
    else:
      self.min = None
    if "max" in kwargs:
      self.max = kwargs["max"]
      del kwargs["max"]
    else:
      self.max = None

    ConfigValue.__init__(self, **kwargs)

  def check_value(self, value):
    value = value.strip()
    if not re.match("^[0-9]+$", value):
      return "Invalid number."
    elif self.min and int(value) < self.min:
      return "Number must be %d or greater." % self.min
    elif self.max and int(value) > self.max:
      return "Number must be %d or smaller." % self.max
    else:
      return None

  def convert_value(self, value):
    return int(value)

class ConfigInterpolation(object):
  def __init__(self, s, c):
    self.s = s
    self.c = c

  def realize(self, item):
    return realize(self.s, item) % realize(self.c, item)

  def __str__(self):
    return "<'" + self.s + "'>"

