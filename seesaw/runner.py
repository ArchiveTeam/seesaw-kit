import os.path

from tornado.ioloop import IOLoop

from .item import Item
from .output import StdoutOutputCollector

class SimpleRunner(object):
  def __init__(self, pipeline, stop_file=None):
    self.ioloop = IOLoop.instance()
    self.pipeline = pipeline
    self.item_count = 0
    self._should_stop = False
    self.stop_file = stop_file
    self.stop_file_mtime = os.path.getmtime(stop_file) if stop_file and os.path.exists(stop_file) else None

    self.pipeline.on_complete = self.item_complete
    self.pipeline.on_error = self.item_error

  def start(self):
    self.add_item()
    self.ioloop.start()

  def stop(self):
    self._should_stop = True

  def should_stop(self):
    if self._should_stop:
      return True
    elif self.stop_file and os.path.exists(self.stop_file):
      if self.stop_file_mtime == None or self.stop_file_mtime < os.path.getmtime(self.stop_file):
        return True
    return False

  def add_item(self):
    self.item_count += 1
    item = Item({"n":self.item_count, "item_name":None})
    item.output_collector = StdoutOutputCollector()
    self.pipeline.enqueue(item)

  def item_complete(self, item):
    print "Complete:", item.description()
    print
    if not self.should_stop():
      self.add_item()
    else:
      if self.pipeline.working == 0:
        self.ioloop.stop()

  def item_error(self, item):
    print "Failed:", item.description()
    print
    if not self.should_stop():
      self.add_item()
    else:
      if self.pipeline.working == 0:
        self.ioloop.stop()

