import traceback
from .output import StringOutputCollector

class Item(dict):
  def __init__(self, *args):
    dict.__init__(self, *args)
    self.failed = False
    self.errors = []
    self.output_collector = StringOutputCollector()

  def log_error(self, task, *args):
    self.errors.append((task, args))

  def description(self):
    if "item_name" in self:
      if self["item_name"]:
        return "item '%s'" % str(self["item_name"])
      else:
        return "new item"
    else:
      return "item %d" % id(self)

  def __str__(self):
    s = "Item " + ("FAILED " if self.failed else "") + dict.__str__(self) 
    for err in self.errors:
      for e in err[1]:
        # TODO this isn't how exceptions work?
        if isinstance(e, Exception):
          s += "%s\n" % traceback.format_exception(Exception, e)
        else:
          s += "%s\n" % str(e)
      s += "\n  " + str(err)
    return s

def realize(v, item):
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

class ItemValue(object):
  def __init__(self, key):
    self.key = key

  def realize(self, item):
    return item[self.key]

  def fill(self, item, value):
    if isinstance(self, ItemValue):
      item[self.key] = value
    elif self == None:
      pass
    else:
      raise Exception("Attempting to fill "+str(type(self)))

  def __str__(self):
    return "<" + self.key + ">"

class ItemInterpolation(object):
  def __init__(self, s):
    self.s = s

  def realize(self, item):
    return self.s % item

  def __str__(self):
    return "<'" + self.s + "'>"

class ConfigValue(object):
  def __init__(self, name="", default=None):
    self.name = name
    self.value = default

  def realize(self, ignored):
    return self.value

  def __str__(self):
    return "<" + self.name + ":" + str(self.value) + ">"


