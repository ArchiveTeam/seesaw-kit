import traceback

from .item import realize

class Task(object):
  def __init__(self, name):
    self.name = name
    self.prev_task = None
    self.on_complete = None
    self.on_error = None

  def __str__(self):
    return self.name

class SimpleTask(Task):
  def __init__(self, name):
    Task.__init__(self, name)

  def enqueue(self, item):
    item.output_collector.append("Starting %s for %s\n" % (self, item.description()))
    try:
      self.process(item)
    except Exception, e:
      item.log_error(e)
      item.failed = True
      item.output_collector.append("Failed %s for %s\n" % (self, item.description()))
      item.output_collector.append("%s\n" % traceback.format_exc())
      if self.on_error:
        self.on_error(item)
    else:
      item.output_collector.append("Finished %s for %s\n" % (self, item.description()))
      if self.on_complete:
        self.on_complete(item)

  def process(self, item):
    pass

  def __str__(self):
    return self.name

class LimitConcurrent(Task):
  def __init__(self, concurrency, inner_task):
    Task.__init__(self, "LimitConcurrent")
    self.concurrency = concurrency
    self.inner_task = inner_task
    self.inner_task.on_complete = self.on_inner_task_complete
    self.inner_task.on_error = self.on_inner_task_error
    self.queue = []
    self.working = 0

  def enqueue(self, item):
    if self.working < realize(self.concurrency, item):
      self.working += 1
      self.inner_task.enqueue(item)
    else:
      self.queue.append(item)
  
  def on_inner_task_complete(self, item):
    self.working -= 1
    if len(self.queue) > 0:
      self.working += 1
      self.inner_task.enqueue(self.queue.pop(0))
    if self.on_complete:
      self.on_complete(item)
  
  def on_inner_task_error(self, item):
    self.working -= 1
    if len(self.queue) > 0:
      self.working += 1
      self.inner_task.enqueue(self.queue.pop(0))
    if self.on_error:
      self.on_error(item)

  def __str__(self):
    return "LimitConcurrent(" + str(self.concurrency) + " x " + str(self.inner_task) + ")"

class SetItemKey(SimpleTask):
  def __init__(self, key, value):
    SimpleTask.__init__(self, "SetItemKey")
    self.key = key
    self.value = value

  def process(self, item):
    item[self.key] = self.value

  def __str__(self):
    return "SetItemKey(" + str(self.key) + ": " + str(self.value) + ")"

class PrintItem(SimpleTask):
  def __init__(self):
    SimpleTask.__init__(self, "PrintItem")

  def process(self, item):
    print item

