import traceback
from .event import Event
from .output import StringOutputCollector

class Item(object):
  def __init__(self, item_id, item_number, properties={}):
    self.item_id = item_id
    self.item_number = item_number
    self.properties = properties
    self._completed = False
    self._failed = False
    self._errors = []
    self._task_status = {}

    self.on_output = Event()
    self.on_error = Event()
    self.on_task_status = Event()
    self.on_complete = Event()
    self.on_fail = Event()
    self.on_finish = Event()

  def log_output(self, data):
    self.on_output.fire(self, data)

  def log_error(self, task, *args):
    self.errors.append((task, args))
    self.on_error.fire(self, task, *args)

  def set_task_status(self, task, status):
    self._task_status[task] = status
    self.on_task_status.fire(self, task, status)

  def complete(self):
    self._completed = True
    self._finished = True
    self.on_complete.fire(self)
    self.on_finish.fire(self)

  def fail(self):
    self._failed = True
    self._finished = True
    self.on_failed.fire(self)
    self.on_finish.fire(self)

  def description(self):
    return "Item %s" % (self.properties["item_name"] if "item_name" in self.properties else "")

  def __getitem__(self, key):
    return self.properties[key]

  def __setitem__(self, key, value):
    self.properties[key] = value

  def __delitem__(self, key):
    del self.properties[key]

  def __str__(self):
    s = "Item " + ("FAILED " if self._failed else "") + str(self.properties) 
    for err in self._errors:
      for e in err[1]:
        # TODO this isn't how exceptions work?
        if isinstance(e, Exception):
          s += "%s\n" % traceback.format_exception(Exception, e)
        else:
          s += "%s\n" % str(e)
      s += "\n  " + str(err)
    return s

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


