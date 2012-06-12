import sys
import os.path

from .event import Event
from .item import Item, realize

from tornado import ioloop

class Runner(object):
  def __init__(self, pipeline, stop_file=None, concurrent_items=1):
    self.pipeline = pipeline
    self.concurrent_items = concurrent_items

    self.item_count = 0
    self.active_items = set()
    self.stop_flag = False
    self.stop_file = stop_file
    self.initial_stop_file_mtime = self.stop_file_mtime()

    self.on_create_item = Event()
    self.on_finish = Event()
    self.pipeline.on_finish_item.handle(self._item_finished)

  def start(self):
    self.add_items()

  def should_stop(self):
    return self.stop_flag or self.stop_file_changed()

  def stop_file_changed(self):
    current_stop_file_mtime = self.stop_file_mtime()
    if current_stop_file_mtime:
      return self.initial_stop_file_mtime == None or self.initial_stop_file_mtime < current_stop_file_mtime
    else:
      return False

  def stop_file_mtime(self):
    if self.stop_file and os.path.exists(self.stop_file):
      return os.path.getmtime(self.stop_file)
    else:
      return None

  def add_items(self):
    items_required = realize(self.concurrent_items)
    while len(self.active_items) < items_required:
      self.item_count += 1
      item_id = "%d-%d" % (id(self), self.item_count)
      item = Item(item_id=item_id, item_number=self.item_count)
      self.on_create_item(self, item)
      self.active_items.add(item)
      self.pipeline.enqueue(item)

  def _item_finished(self, pipeline, item):
    self.active_items.remove(item)
    if not self.should_stop():
      self.add_items()
    elif len(self.active_items) == 0:
      self.on_finish.fire(self)

class SimpleRunner(Runner):
  def __init__(self, pipeline, stop_file=None, concurrent_items=1):
    Runner.__init__(self, pipeline, stop_file=stop_file, concurrent_items=concurrent_items)

    self.on_create_item.handle(self._handle_create_item)
    self.on_finish.handle(self._stop_ioloop)

  def start(self):
    Runner.start(self)
    ioloop.IOLoop.instance().start()

  def _stop_ioloop(self, ignored):
    ioloop.IOLoop.instance().stop()

  def _handle_create_item(self, ignored, item):
    item.on_output.handle(self._handle_item_output)

  def _handle_item_output(self, item, data):
    sys.stdout.write(data)

