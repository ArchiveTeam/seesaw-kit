import re
import os.path

from tornado import web, ioloop, template
from tornadio2 import SocketConnection, TornadioRouter, SocketServer, event

from seesaw.config import realize

PUBLIC_PATH = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "public"))
TEMPLATES_PATH = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates"))

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
  def initialize(self, warrior = None, runner = None):
    self.warrior = warrior
    self.runner = runner

  def get_template_path(self):
    return TEMPLATES_PATH

  def post(self, command):
    if self.warrior:
      self.runner = self.warrior.current_runner

    if command == "stop":
      if self.warrior:
        self.warrior.stop_gracefully()
      else:
        self.runner.stop_gracefully()
      self.write("OK")
    elif command == "keep_running":
      if self.warrior:
        self.warrior.keep_running()
      else:
        self.runner.keep_running()
      self.write("OK")
    elif command == "select-project":
      self.warrior.config_manager.set_value("selected_project", self.get_argument("project_name"))
      self.warrior.select_project(self.get_argument("project_name"))
      self.write("OK")
    elif command == "deselect-project":
      self.warrior.config_manager.set_value("selected_project", "none")
      self.warrior.select_project(None)
      self.write("OK")
    elif command == "settings":
      success = True
      posted_values = {}
      for (name, value) in self.request.arguments.iteritems():
        if not self.warrior.config_manager.set_value(name, value[0]):
          success = False
          posted_values[name] = value[0]
      if self.warrior.config_manager.all_valid():
        self.warrior.fire_status()
      self.render("settings.html", warrior=self.warrior, posted_values=posted_values)

  def get(self, command):
    if command == "all-projects":
      self.render("all-projects.html", warrior=self.warrior, realize=realize)
    elif command == "settings":
      self.render("settings.html", warrior=self.warrior, posted_values={})


class SeesawConnection(SocketConnection):
  clients = set()
  item_monitors = dict()

  warrior = None
  project = None
  runner = None

  def on_open(self, info):
    self.clients.add(self)

    items = []
    for item_monitor in self.item_monitors.itervalues():
      items.append(item_monitor.item_for_broadcast())

    if self.project:
      self.emit("project.refresh", {
        "project": self.project.data_for_json(),
        "status": ("stopping" if self.runner.should_stop() else "running"),
        "items": items
      })
    else:
      self.emit("project.refresh", None)

    if self.warrior:
      self.emit("warrior.projects_loaded", {
        "projects": self.warrior.projects
      })
      self.emit("warrior.status", { "status": self.warrior.warrior_status() })

  @classmethod
  def broadcast_bandwidth(cls):
    if cls.warrior:
      bw_stats = cls.warrior.bandwidth_stats()
      if bw_stats:
        cls.broadcast("bandwidth", bw_stats)

  @classmethod
  def handle_warrior_status(cls, warrior, new_status):
    cls.broadcast("warrior.status", { "status": new_status })

  @classmethod
  def handle_projects_loaded(cls, warrior, projects):
    cls.broadcast_projects()

  @classmethod
  def broadcast_projects(cls):
    cls.broadcast("warrior.projects_loaded", {
      "projects": cls.warrior.projects
    })

  @classmethod
  def handle_project_selected(cls, warrior, project):
    cls.broadcast("warrior.project_selected", { "project": project })

  @classmethod
  def handle_project_installing(cls, warrior, project):
    cls.broadcast("warrior.project_installing", { "project": project })

  @classmethod
  def handle_project_installed(cls, warrior, project, output):
    output = re.sub("\r\n?", "\n", output)
    cls.broadcast("warrior.project_installed", { "project": project, "output": output })

  @classmethod
  def handle_project_installation_failed(cls, warrior, project, output):
    output = re.sub("\r\n?", "\n", output)
    cls.broadcast("warrior.project_installation_failed", { "project": project, "output": output })

  @classmethod
  def handle_project_refresh(cls, warrior, project, runner):
    cls.project = project
    cls.runner = runner
    if project:
      runner.pipeline.on_start_item += SeesawConnection.handle_start_item
      runner.pipeline.on_finish_item += SeesawConnection.handle_finish_item
      runner.on_status += SeesawConnection.handle_runner_status
    cls.broadcast_project_refresh()

  @classmethod
  def broadcast_project_refresh(cls):
    if cls.project:
      cls.broadcast("project.refresh", {
        "project": cls.project.data_for_json(),
        "status": ("stopping" if cls.runner.should_stop() else "running"),
        "items": []
      })
    else:
      cls.broadcast("project.refresh", None)

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


def start_runner_server(project, runner, port_number=8001):
  SeesawConnection.project = project
  SeesawConnection.runner = runner

  runner.pipeline.on_start_item += SeesawConnection.handle_start_item
  runner.pipeline.on_finish_item += SeesawConnection.handle_finish_item
  runner.on_status += SeesawConnection.handle_runner_status

  router = TornadioRouter(SeesawConnection)
  application = web.Application(
    router.apply_routes([(r"/(.*\.(html|css|js|swf|png))$",
                          web.StaticFileHandler, {"path": PUBLIC_PATH}),
                         ("/", IndexHandler),
                         ("/api/(.+)$", ApiHandler, {"runner": runner})]),
#   flash_policy_port = 843,
#   flash_policy_file = os.path.join(PUBLIC_PATH, "flashpolicy.xml"),
    socket_io_port = port_number
  )
  SocketServer(application, auto_start=False)

def start_warrior_server(warrior, port_number=8001):
  SeesawConnection.warrior = warrior

  warrior.on_projects_loaded += SeesawConnection.handle_projects_loaded
  warrior.on_project_refresh += SeesawConnection.handle_project_refresh
  warrior.on_project_installing += SeesawConnection.handle_project_installing
  warrior.on_project_installed += SeesawConnection.handle_project_installed
  warrior.on_project_installation_failed += SeesawConnection.handle_project_installation_failed
  warrior.on_project_selected += SeesawConnection.handle_project_selected
  warrior.on_status += SeesawConnection.handle_warrior_status

  ioloop.PeriodicCallback(SeesawConnection.broadcast_bandwidth, 1000).start()

  router = TornadioRouter(SeesawConnection)
  application = web.Application(
    router.apply_routes([(r"/(.*\.(html|css|js|swf|png))$",
                          web.StaticFileHandler, {"path": PUBLIC_PATH}),
                         ("/", IndexHandler),
                         ("/api/(.+)$", ApiHandler, {"warrior": warrior})]),
#   flash_policy_port = 843,
#   flash_policy_file = os.path.join(PUBLIC_PATH, "flashpolicy.xml"),
    socket_io_port = port_number,
    debug = True
  )
  SocketServer(application, auto_start=False)

