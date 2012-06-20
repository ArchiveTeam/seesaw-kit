import re
import os.path

from tornado import web, ioloop
from tornadio2 import SocketConnection, TornadioRouter, SocketServer, event

PUBLIC_PATH = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "public"))

class IndexHandler(web.RequestHandler):
  def get(self):
    self.render(os.path.join(PUBLIC_PATH, "index.html"))


class ItemMonitor(object):
  def __init__(self, pipeline, item):
    self.pipeline = pipeline
    self.item = item

    item.on_output += self.handle_item_output
    item.on_task_status += self.handle_item_task_status
    item.on_property += self.handle_item_property
    item.on_complete += self.handle_item_complete
    item.on_fail += self.handle_item_fail
    item.on_cancel += self.handle_item_cancel

    self.collected_data = []

    SeesawConnection.broadcast("pipeline.start_item", { "pipeline_id": id(self.pipeline), "item": self.item_for_broadcast() })

  def item_for_broadcast(self):
    item = self.item

    tasks = []
    for (task, task_name) in self.pipeline.ui_task_list():
      tasks.append({
        "id": id(task),
        "name": task_name,
        "status": (item.task_status[task] if task in item.task_status else None)
      })

    item_data = {
      "id": item.item_id,
      "name": ("Item %s" % item["item_name"] if "item_name" in item else "New item"),
      "number": item.item_number,
      "status": self.item_status(),
      "tasks": tasks,
      "output": "".join(self.collected_data)
    }

    return item_data

  def item_status(self):
    if self.item.completed:
      return "completed"
    elif self.item.failed:
      return "failed"
    elif self.item.canceled:
      return "canceled"
    else:
      return "running"

  def handle_item_output(self, item, data):
    data = re.sub("\r\n?", "\n", data)
    self.collected_data.append(data)
    SeesawConnection.broadcast("item.output", { "item_id": item.item_id, "data": data })

  def handle_item_task_status(self, item, task, new_status, old_status):
    SeesawConnection.broadcast("item.task_status", { "item_id": item.item_id, "task_id": id(task), "new_status": new_status, "old_status": old_status })

  def handle_item_property(self, item, key, new_value, old_value):
    if key == "item_name":
      SeesawConnection.broadcast("item.update_name", { "item_id": item.item_id, "new_name": "Item %s" % new_value })

  def handle_item_complete(self, item):
    SeesawConnection.broadcast("item.complete", { "item_id": item.item_id })

  def handle_item_fail(self, item):
    SeesawConnection.broadcast("item.fail", { "item_id": item.item_id })

  def handle_item_cancel(self, item):
    SeesawConnection.broadcast("item.cancel", { "item_id": item.item_id })


class ApiHandler(web.RequestHandler):
  def initialize(self, runner):
    self.runner = runner

  def post(self, command):
    if command == "stop":
      self.runner.stop_gracefully()
      return "OK"
    elif command == "keep_running":
      self.runner.keep_running()
      return "OK"


class SeesawConnection(SocketConnection):
  clients = set()
  item_monitors = dict()

  project = None
  runner = None

  def on_open(self, info):
    self.clients.add(self)

    items = []
    for item_monitor in self.item_monitors.itervalues():
      items.append(item_monitor.item_for_broadcast())
    self.emit("project.refresh", {
      "project": self.project.data_for_json(),
      "status": ("stopping" if self.runner.should_stop() else "running"),
      "items": items
    })

  @classmethod
  def handle_runner_status(cls, runner, status):
    cls.broadcast("runner.status", {
      "status": ("stopping" if runner.should_stop() else "running")
    })

  @classmethod
  def handle_start_item(cls, pipeline, item):
    cls.item_monitors[item] = ItemMonitor(pipeline, item)

  @classmethod
  def handle_finish_item(cls, pipeline, item):
    del cls.item_monitors[item]

  @classmethod
  def broadcast(cls, event, message):
    for client in cls.clients:
      client.emit(event, message)

  def on_message(self, message):
    pass

  def on_close(self):
    self.clients.remove(self)


def start_server(project, runner, port_number=8001):
  SeesawConnection.project = project
  SeesawConnection.runner = runner

  runner.pipeline.on_start_item += SeesawConnection.handle_start_item
  runner.pipeline.on_finish_item += SeesawConnection.handle_finish_item
  runner.on_status += SeesawConnection.handle_runner_status

  router = TornadioRouter(SeesawConnection)
  application = web.Application(
    router.apply_routes([(r"/(.*\.(html|css|js|swf))$",
                          web.StaticFileHandler, {"path": PUBLIC_PATH}),
                         ("/", IndexHandler),
                         ("/api/(.+)$", ApiHandler, {"runner": runner})]),
#   flash_policy_port = 843,
#   flash_policy_file = os.path.join(PUBLIC_PATH, "flashpolicy.xml"),
    socket_io_port = port_number
  )
  SocketServer(application, auto_start=False)

