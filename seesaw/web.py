'''The warrior web interface.'''
import collections
import hashlib
import json
import os
import os.path
import random
import re
import time

from sockjs.tornado import SockJSConnection, SockJSRouter
from tornado import web, ioloop

from seesaw.config import realize
from seesaw.web_util import BaseWebAdminHandler

PUBLIC_PATH = os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "public"))
TEMPLATES_PATH = os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates"))


class IndexHandler(BaseWebAdminHandler):
    '''Shows the index.html.'''
    def get(self):
        self.render(os.path.join(PUBLIC_PATH, "index.html"),
                    timestamp=time.time())


class ItemMonitor(object):
    '''Pushes item states and information to the client.'''
    def __init__(self, item):
        self.pipeline = item.pipeline
        self.item = item

        item.on_output += self.handle_item_output
        item.on_task_status += self.handle_item_task_status
        item.on_property += self.handle_item_property
        item.on_complete += self.handle_item_complete
        item.on_fail += self.handle_item_fail
        item.on_cancel += self.handle_item_cancel

        self.collected_data = collections.deque((), 500)

        SeesawConnection.broadcast(
            "pipeline.start_item",
            {"pipeline_id": id(self.pipeline),
                "item": self.item_for_broadcast()}
            )

    def item_for_broadcast(self):
        item = self.item

        tasks = []
        for (task, task_name) in self.pipeline.ui_task_list():
            tasks.append({
                "id": id(task),
                "name": task_name,
                "status": (item.task_status[task]
                           if task in item.task_status else None)
            })

        if self.pipeline.project:
            project_name = self.pipeline.project.title
        else:
            project_name = None
        item_data = {
            "id": item.item_id,
            "name": ("Item %s" % item["item_name"]
                     if "item_name" in item else "New item"),
            "number": item.item_number,
            "status": self.item_status(),
            "tasks": tasks,
            "output": "".join(self.collected_data),
            "project": project_name,
            "start_time": item.start_time
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
        self.collected_data.append(data)
        SeesawConnection.broadcast(
            "item.output", {"item_id": item.item_id, "data": data})

    def handle_item_task_status(self, item, task, new_status, old_status):
        SeesawConnection.broadcast(
            "item.task_status",
            {
                "item_id": item.item_id,
                "task_id": id(task),
                "new_status": new_status,
                "old_status": old_status
            }
        )

    def handle_item_property(self, item, key, new_value, old_value):
        if key == "item_name":
            SeesawConnection.broadcast(
                "item.update_name",
                {"item_id": item.item_id, "new_name": "Item %s" % new_value})

    def handle_item_complete(self, item):
        SeesawConnection.broadcast("item.complete", {"item_id": item.item_id})

    def handle_item_fail(self, item):
        SeesawConnection.broadcast("item.fail", {"item_id": item.item_id})

    def handle_item_cancel(self, item):
        SeesawConnection.broadcast("item.cancel", {"item_id": item.item_id})


class ApiHandler(BaseWebAdminHandler):
    '''Processes API requests.'''
    def initialize(self, warrior=None, runner=None):
        self.warrior = warrior
        self.runner = runner

    def get_template_path(self):
        return TEMPLATES_PATH

    def post(self, command):
        if command == "stop":
            if self.warrior:
                self.warrior.stop_gracefully()
            else:
                self.runner.stop_gracefully()
            self.write("OK")
        elif command == "stop_now":
            if self.warrior:
                self.warrior.forced_stop()
            else:
                self.runner.forced_stop()
            self.write("OK")
        elif command == "keep_running":
            if self.warrior:
                self.warrior.keep_running()
            else:
                self.runner.keep_running()
            self.write("OK")
        elif command == "select-project":
            self.warrior.config_manager.set_value(
                "selected_project", self.get_argument("project_name"))
            self.warrior.select_project(self.get_argument("project_name"))
            self.write("OK")
        elif command == "deselect-project":
            self.warrior.config_manager.set_value("selected_project", "none")
            self.warrior.select_project(None)
            self.write("OK")
        elif command == "settings":
            posted_values = {}
            for (name, value) in self.request.arguments.items():
                value[0] = value[0].decode('utf8', 'replace')

                if not self.warrior.config_manager.set_value(name, value[0]):
                    posted_values[name] = value[0]
            if self.warrior.config_manager.all_valid():
                self.warrior.fire_status()
            self.render("settings.html", warrior=self.warrior,
                        posted_values=posted_values)

    def get(self, command):
        if command == "all-projects":
            self.render("all-projects.html", warrior=self.warrior,
                        realize=realize)
        elif command == "settings":
            self.render("settings.html", warrior=self.warrior,
                        posted_values={})
        elif command == "help":
            self.render("help.html", warrior=self.warrior)


class SeesawConnection(SockJSConnection):
    '''A WebSocket server that communicates the state of the warrior.'''
    instance_id = ("%d-%f" % (os.getpid(), random.random()))

    clients = set()
    item_monitors = dict()

    warrior = None
    project = None
    runner = None

    def emit(self, event_name, message):
        '''tornadoio to sockjs adapter.'''
        self.send(json.dumps({'event_name': event_name, 'message': message}))

    def on_open(self, info):
        self.clients.add(self)
        self.emit("instance_id", self.instance_id)

        items = []
        for item_monitor in self.item_monitors.values():
            items.append(item_monitor.item_for_broadcast())

        if self.project:
            self.emit("project.refresh", {
                "project": self.project.data_for_json(),
                "status": ("stopping"
                           if self.runner.should_stop() else "running"),
                "items": items
            })
        else:
            self.emit("project.refresh", None)

        if self.warrior:
            self.emit("warrior.projects_loaded", {
                "projects": self.warrior.projects
            })
            self.emit("warrior.status",
                      {"status": self.warrior.warrior_status()})
            self.emit("warrior.broadcast_message",
                      {
                          "message": self.warrior.broadcast_message,
                          "hash": hash_string(self.warrior.broadcast_message)
                      })

    @classmethod
    def broadcast_bandwidth(cls):
        if cls.warrior:
            bw_stats = cls.warrior.bandwidth_stats()
            if bw_stats:
                cls.broadcast("bandwidth", bw_stats)

    @classmethod
    def broadcast_timestamp(cls):
        cls.broadcast("timestamp", {"timestamp": time.time()})


    @classmethod
    def handle_warrior_status(cls, warrior, new_status):
        cls.broadcast("warrior.status", {"status": new_status})

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
        cls.broadcast("warrior.project_selected", {"project": project})

    @classmethod
    def handle_project_installing(cls, warrior, project):
        cls.broadcast("warrior.project_installing", {"project": project})

    @classmethod
    def handle_project_installed(cls, warrior, project, output):
        output = re.sub("\r\n?", "\n", output)
        cls.broadcast("warrior.project_installed",
                      {"project": project, "output": output})

    @classmethod
    def handle_project_installation_failed(cls, warrior, project, output):
        output = re.sub("\r\n?", "\n", output)
        cls.broadcast("warrior.project_installation_failed",
                      {"project": project, "output": output})

    @classmethod
    def handle_project_refresh(cls, warrior, project, runner):
        cls.project = project
        cls.runner = runner
        cls.broadcast_project_refresh()

    @classmethod
    def handle_broadcast_message(cls, warrior, message):
        cls.broadcast("warrior.broadcast_message",
                      {
                          "message": message,
                          "hash": hash_string(message)
                      })

    @classmethod
    def broadcast_project_refresh(cls):
        if cls.project:
            cls.broadcast("project.refresh", {
                "project": cls.project.data_for_json(),
                "status": ("stopping"
                           if cls.runner.should_stop() else "running"),
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
    def handle_start_item(cls, runner, pipeline, item):
        cls.item_monitors[item] = ItemMonitor(item)

    @classmethod
    def handle_finish_item(cls, runner, pipeline, item):
        del cls.item_monitors[item]

    @classmethod
    def broadcast(cls, event, message):
        for client in cls.clients:
            if message:
                message["session_id"] = client.session.session_id
            client.emit(event, message)

    def on_message(self, message):
        pass

    def on_close(self):
        self.clients.remove(self)


def hash_string(text):
    '''Generate a digest for broadcast message.'''
    return hashlib.md5((text or '').encode('ascii', 'replace')).hexdigest()


def start_runner_server(project, runner, bind_address="localhost", port_number=8001,
                        http_username=None, http_password=None):
    '''Starts a web interface for a manually run pipeline.

    Unlike :func:`start_warrior_server`, this UI does not contain an
    configuration or project management panel.
    '''
#     if bind_address == "0.0.0.0":
#         bind_address = ""

    SeesawConnection.project = project
    SeesawConnection.runner = runner

    runner.on_pipeline_start_item += SeesawConnection.handle_start_item
    runner.on_pipeline_finish_item += SeesawConnection.handle_finish_item
    runner.on_status += SeesawConnection.handle_runner_status

    ioloop.PeriodicCallback(SeesawConnection.broadcast_timestamp, 1000).start()

    router = SockJSRouter(SeesawConnection)

    application = web.Application(
        router.apply_routes([
            (r"/(.*\.(html|css|js|swf|png|ico))$",
                web.StaticFileHandler, {"path": PUBLIC_PATH}),
            ("/", IndexHandler),
            ("/api/(.+)$", ApiHandler, {"runner": runner})]),
        #  flash_policy_port = 843,
        #  flash_policy_file=os.path.join(PUBLIC_PATH, "flashpolicy.xml"),
        socket_io_address=bind_address,
        socket_io_port=port_number,

        # settings for AuthenticatedApplication
        auth_enabled=(http_password or "").strip() != "",
        check_auth=lambda r, username, password:
            (
                password == http_password and
                (http_username or "").strip() in ["", username]
            ),
        auth_realm="ArchiveTeam Warrior",
        skip_auth=[]
    )

    application.listen(port_number, bind_address)


def start_warrior_server(warrior, bind_address="localhost", port_number=8001,
                         http_username=None, http_password=None):
    '''Starts the warrior web interface.'''
    SeesawConnection.warrior = warrior

    warrior.on_projects_loaded += SeesawConnection.handle_projects_loaded
    warrior.on_project_refresh += SeesawConnection.handle_project_refresh
    warrior.on_project_installing += SeesawConnection.handle_project_installing
    warrior.on_project_installed += SeesawConnection.handle_project_installed
    warrior.on_project_installation_failed += \
        SeesawConnection.handle_project_installation_failed
    warrior.on_project_selected += SeesawConnection.handle_project_selected
    warrior.on_broadcast_message_received += SeesawConnection.handle_broadcast_message
    warrior.on_status += SeesawConnection.handle_warrior_status
    warrior.runner.on_pipeline_start_item += SeesawConnection.handle_start_item
    warrior.runner.on_pipeline_finish_item += \
        SeesawConnection.handle_finish_item
    warrior.runner.on_status += SeesawConnection.handle_runner_status

    if not http_username:
        http_username = warrior.http_username
    if not http_password:
        http_password = warrior.http_password

    ioloop.PeriodicCallback(SeesawConnection.broadcast_bandwidth, 1000).start()
    ioloop.PeriodicCallback(SeesawConnection.broadcast_timestamp, 1000).start()

    router = SockJSRouter(SeesawConnection)

    application = web.Application(
        router.apply_routes([
            (r"/(.*\.(html|css|js|swf|png|ico))$",
                web.StaticFileHandler, {"path": PUBLIC_PATH}),
            ("/", IndexHandler),
            ("/api/(.+)$", ApiHandler, {"warrior": warrior})]),
        #   flash_policy_port = 843,
        #   flash_policy_file = os.path.join(PUBLIC_PATH, "flashpolicy.xml"),
        socket_io_address=bind_address,
        socket_io_port=port_number,

        # settings for AuthenticatedApplication
        auth_enabled=lambda: (realize(http_password) or "").strip() != "",
        check_auth=lambda r, username, password:
            (
                password == realize(http_password) and
                (realize(http_username) or "").strip() in ["", username]
            ),
        auth_realm="ArchiveTeam Warrior",
        skip_auth=[]
    )

    application.listen(port_number, bind_address)
