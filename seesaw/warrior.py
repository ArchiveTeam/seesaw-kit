import json
import subprocess
import functools
import os.path
import shutil
import sys
import time
import re
import json
from ordereddict import OrderedDict
from distutils.version import StrictVersion

from tornado import ioloop
from tornado import gen
from tornado.ioloop import PeriodicCallback
from tornado.httpclient import AsyncHTTPClient, HTTPRequest

import seesaw
from seesaw.event import Event
from seesaw.externalprocess import AsyncPopen
from seesaw.runner import Runner
from seesaw.config import realize
from seesaw.web import SeesawConnection
from seesaw.config import NumberConfigValue, StringConfigValue, ConfigValue

class ConfigManager(object):
  def __init__(self, config_file):
    self.config_file = config_file
    self.config_memory = {}
    self.config_values = OrderedDict()

    self.load()

  def add(self, config_value):
    self.config_values[config_value.name] = config_value
    if config_value.name in self.config_memory:
      remembered_value = self.config_memory[config_value.name]
      if config_value.check_value(remembered_value) == None:
        config_value.set_value(remembered_value)
    self.save()

  def remove(self, name):
    if name in self.config_values:
      del self.config_values[name]
    self.save()

  def all_valid(self):
    return all([ c.is_valid() for c in self ])

  def set_value(self, name, value):
    if name in self.config_values:
      if self.config_values[name].set_value(value):
        self.config_memory[name] = value
        self.save()
        return True
    return False

  def load(self):
    try:
      with open(self.config_file) as f:
        self.config_memory = json.load(f)
    except Exception, e:
      self.config_memory = {}

  def save(self):
    with open(self.config_file, "w") as f:
      json.dump(self.config_memory, f)

  def __iter__(self):
    return self.config_values.itervalues()

  def editable_values(self):
    return [ v for v in self if v.editable ]


class BandwidthMonitor(object):
  devre = re.compile(r"^\s*([a-z0-9]+):(.+)$")
  
  def __init__(self, device):
    self.device = device
    self.prev_time = None
    self.prev_stats = None
    self.bandwidth = None
    self.update()

  def current_stats(self):
    if self.prev_stats and self.bandwidth:
      return {
          "received": self.prev_stats[0],
          "sent": self.prev_stats[1],
          "receiving": self.bandwidth[0],
          "sending": self.bandwidth[1]
        }
    return None
  
  def update(self):
    cur_time = time.time()
    cur_stats = self._get_stats()
    if self.prev_stats != None and cur_stats != None:
      time_delta = cur_time - self.prev_time
      self.bandwidth = [
        (cur_stats[0] - self.prev_stats[0]) / time_delta,
        (cur_stats[1] - self.prev_stats[1]) / time_delta,
      ]
    self.prev_time = cur_time
    self.prev_stats = cur_stats
    return self.bandwidth
  
  def _get_stats(self):
    with open("/proc/net/dev") as f:
      lines = f.readlines()
    for line in lines:
      m = self.devre.match(line)
      if m and m.group(1) == self.device:
        fields = m.group(2).split()
        received = fields[0]
        sent = fields[8]
        return [int(received), int(sent)]
    return None



class Warrior(object):
  def __init__(self, projects_dir, data_dir, warrior_hq_url, real_shutdown=False):
    self.projects_dir = projects_dir
    self.data_dir = data_dir
    self.warrior_hq_url = warrior_hq_url
    self.real_shutdown = real_shutdown

    self.warrior_id = StringConfigValue(
      name="warrior_id",
      title="Warrior ID",
      description="The unique number of your warrior instance.",
      editable=False
    )
    self.selected_project_config_value = StringConfigValue(
      name="selected_project",
      title="Selected project",
      description="The project (to be continued when the warrior restarts).",
      default="none",
      editable=False
    )
    self.downloader = StringConfigValue(
      name="downloader",
      title="Your nickname",
      description="We use your nickname to show your results on our tracker. Letters and numbers only.",
      regex="^[-_a-zA-Z0-9]{3,30}$"
    )
    self.concurrent_items = NumberConfigValue(
      name="concurrent_items",
      title="Concurrent items",
      description="How many items should the warrior download at a time? (Max: 6)",
      min=1,
      max=6,
      default=2
    )

    self.config_manager = ConfigManager(os.path.join(projects_dir, "config.json"))
    self.config_manager.add(self.warrior_id)
    self.config_manager.add(self.selected_project_config_value)
    self.config_manager.add(self.downloader)
    self.config_manager.add(self.concurrent_items)

    self.bandwidth_monitor = BandwidthMonitor("eth0")
    self.bandwidth_monitor.update()

    self.current_project_name = None
    self.current_project = None
    self.current_pipeline = None
    self.current_runner = None

    self.selected_project = None

    self.projects = {}
    self.installed_projects = set()
    self.failed_projects = set()

    self.on_projects_loaded = Event()
    self.on_project_installing = Event()
    self.on_project_installed = Event()
    self.on_project_installation_failed = Event()
    self.on_project_refresh = Event()
    self.on_project_selected = Event()
    self.on_status = Event()

    self.http_client = AsyncHTTPClient()

    self.installing = False
    self.shut_down_flag = False
    self.reboot_flag = False

    self.hq_updater = ioloop.PeriodicCallback(self.update_warrior_hq, 10*60*1000)
    self.project_updater = ioloop.PeriodicCallback(self.update_project, 60*60*1000)

    self.lat_lng = None
    self.find_lat_lng()

  def find_lat_lng(self):
    # response = self.http_client.fetch("http://www.maxmind.com/app/mylocation", self.handle_lat_lng, user_agent="")
    pass

  def handle_lat_lng(self, response):
    m = re.search(r"geoip-demo-results-tbodyLatitude/Longitude</td>\s*<td[^>]*>\s*([-/.0-9]+)\s*</td>", response.body)
    if m:
      self.lat_lng = m.group(1)

  def bandwidth_stats(self):
    self.bandwidth_monitor.update()
    return self.bandwidth_monitor.current_stats()

  @gen.engine
  def update_warrior_hq(self):
    if realize(self.warrior_id) == None:
      response = yield gen.Task(self.http_client.fetch,
                                os.path.join(self.warrior_hq_url, "api/register.json"),
                                method="POST",
                                headers={"Content-Type": "application/json"},
                                user_agent=("ArchiveTeam Warrior/%s" % seesaw.__version__),
                                body=json.dumps({"warrior":{"version": seesaw.__version__}}))
      if response.code == 200:
        data = json.loads(response.body)
        print "Received Warrior ID '%s'." % data["warrior_id"]
        self.config_manager.set_value("warrior_id", data["warrior_id"])
      else:
        print "HTTP error %s" % (response.code)
        return
    else:
      print "Warrior ID '%s'." % realize(self.warrior_id)

    response = yield gen.Task(self.http_client.fetch,
                              os.path.join(self.warrior_hq_url, "api/update.json"),
                              method="POST",
                              headers={"Content-Type": "application/json"},
                              user_agent=("ArchiveTeam Warrior/%s %s" % (seesaw.__version__, seesaw.runner_type)),
                              body=json.dumps({"warrior":{
                                "warrior_id": realize(self.warrior_id),
                                "lat_lng": self.lat_lng,
                                "downloader": realize(self.downloader),
                                "selected_project": realize(self.selected_project_config_value)
                              }}))
    if response.code == 200:
      data = json.loads(response.body)

      if StrictVersion(seesaw.__version__) < StrictVersion(data["warrior"]["seesaw_version"]):
        # time for an update
        print "Reboot for Seesaw update."
        self.reboot_gracefully()
        return

      projects_list = data["projects"]
      self.projects = OrderedDict([ (project["name"], project) for project in projects_list ])
      for project_data in self.projects.itervalues():
        if "deadline" in project_data:
          project_data["deadline_int"] = time.mktime(time.strptime(project_data["deadline"], "%Y-%m-%dT%H:%M:%SZ"))


      previous_project_choice = realize(self.selected_project_config_value)

      if self.selected_project and not self.selected_project in self.projects:
        self.select_project(None)
      elif previous_project_choice in self.projects:
        # select previous project
        self.select_project(previous_project_choice)
      elif previous_project_choice == "auto":
        # ArchiveTeam's choice
        if "auto_project" in data:
          self.select_project(data["auto_project"])
        else:
          self.select_project(None)

      self.on_projects_loaded(self, self.projects)

    else:
      print "HTTP error %s" % (response.code)

  @gen.engine
  def install_project(self, project_name, callback=None):
    self.installed_projects.discard(project_name)

    if project_name in self.projects and not self.installing:
      self.installing = project_name
      self.install_output = []

      project = self.projects[project_name]
      project_path = os.path.join(self.projects_dir, project_name)

      self.on_project_installing(self, project)

      if project_name in self.failed_projects:
        if os.path.exists(project_path):
          shutil.rmtree(project_path)
        self.failed_projects.discard(project_name)

      if os.path.exists(project_path):
        p = AsyncPopen(
            args=[ "git", "pull" ],
            cwd=project_path
        )
      else:
        p = AsyncPopen(args=[ "git", "clone", project["repository"], project_path ])
      p.on_output += self.collect_install_output
      p.on_end += yield gen.Callback("gitend")
      p.run()
      result = yield gen.Wait("gitend")

      if result != 0:
        self.install_output.append("\ngit returned %d\n" % result)
        self.on_project_installation_failed(self, project, "".join(self.install_output))
        self.installing = None
        self.failed_projects.add(project_name)
        if callback:
          callback(False)
        return

      project_install_file = os.path.join(project_path, "warrior-install.sh")

      if os.path.exists(project_install_file):
        p = AsyncPopen(
            args=[ project_install_file ],
            cwd=project_path
        )
        p.on_output += self.collect_install_output
        p.on_end += yield gen.Callback("installend")
        p.run()
        result = yield gen.Wait("installend")

        if result != 0:
          self.install_output.append("\nCustom installer returned %d\n" % result)
          self.on_project_installation_failed(self, project, "".join(self.install_output))
          self.installing = None
          self.failed_projects.add(project_name)
          if callback:
            callback(False)
          return

      data_dir = os.path.join(self.data_dir, "data")
      if os.path.exists(data_dir):
        shutil.rmtree(data_dir)
      os.makedirs(data_dir)

      project_data_dir = os.path.join(project_path, "data")
      if os.path.islink(project_data_dir):
        os.remove(project_data_dir)
      elif os.path.isdir(project_data_dir):
        shutil.rmtree(project_data_dir)
      os.symlink(data_dir, project_data_dir)

      self.installed_projects.add(project_name)
      self.on_project_installed(self, project, "".join(self.install_output))

      self.installing = None
      if callback:
        callback(True)

  @gen.engine
  def update_project(self):
    if self.selected_project and (yield gen.Task(self.check_project_has_update, self.selected_project)):
      # restart project
      if self.current_runner:
        self.current_runner.stop_gracefully()

  @gen.engine
  def check_project_has_update(self, project_name, callback):
    if project_name in self.projects:
      project = self.projects[project_name]
      project_path = os.path.join(self.projects_dir, project_name)

      self.install_output = []

      if not os.path.exists(project_path):
        callback(True)
        return

      p = AsyncPopen(
          args=[ "git", "fetch" ],
          cwd=project_path
      )
      p.on_output += self.collect_install_output
      p.on_end += yield gen.Callback("gitend")
      p.run()
      result = yield gen.Wait("gitend")

      if result != 0:
        callback(True)
        return

      output = subprocess.Popen(
          args=[ "git", "rev-list", "HEAD..FETCH_HEAD" ],
          cwd=project_path,
          stdout=subprocess.PIPE
      ).communicate()[0]
      if output.strip() != "":
        callback(True)
      else:
        callback(False)

  def collect_install_output(self, data):
    sys.stdout.write(data)
    data = re.sub("[\x00-\x08\x0b\x0c]", "", data)
    self.install_output.append(data)

  @gen.engine
  def select_project(self, project_name):
    if project_name == "auto":
      self.update_warrior_hq()
      return

    if not project_name in self.projects:
      project_name = None

    if project_name != self.selected_project:
      # restart
      self.selected_project = project_name
      self.on_project_selected(self, project_name)
      self.start_selected_project()

  def load_pipeline(self, pipeline_path, context):
    dirname, basename = os.path.split(pipeline_path)
    if dirname == "":
      dirname = "."

    with open(pipeline_path) as f:
      pipeline_str = f.read()

    ConfigValue.start_collecting()

    local_context = context
    global_context = context
    curdir = os.getcwd()
    try:
      os.chdir(dirname)
      exec pipeline_str in local_context, global_context
    finally:
      os.chdir(curdir)

    config_values = ConfigValue.stop_collecting()

    return ( local_context["project"], local_context["pipeline"], config_values )

  @gen.engine
  def start_selected_project(self):
    project_name = self.selected_project

    if self.current_project_name == project_name:
      # already running
      return

    if self.current_runner:
      self.current_runner.stop_gracefully()
      self.fire_status()
      return

    if project_name in self.projects:
      if not project_name in self.installed_projects or (yield gen.Task(self.check_project_has_update, project_name)):
        result = yield gen.Task(self.install_project, project_name)
        if not result:
          return

      project = self.projects[self.selected_project]

      project_path = os.path.join(self.projects_dir, project_name)
      pipeline_path = os.path.join(project_path, "pipeline.py")

      (project, pipeline, config_values) = self.load_pipeline(pipeline_path, { "downloader": self.downloader })

      for config_value in config_values:
        self.config_manager.add(config_value)
      project.config_values = config_values

      runner = Runner(pipeline, concurrent_items=self.concurrent_items)
      runner.on_finish += self.handle_runner_finish

      self.current_project_name = project_name
      self.current_project = project
      self.current_pipeline = pipeline
      self.current_runner = runner

      self.on_project_refresh(self, self.current_project, self.current_runner)
      self.fire_status()

      runner.start()

  def handle_runner_finish(self, runner):
    if self.current_project:
      for config_value in self.current_project.config_values:
        self.config_manager.remove(config_value.name)

    self.current_project_name = None
    self.current_project = None
    self.current_pipeline = None
    self.current_runner = None

    self.on_project_refresh(self, self.current_project, self.current_runner)
    self.fire_status()

    if self.shut_down_flag or self.reboot_flag:
      ioloop.IOLoop.instance().stop()

      if self.real_shutdown:
        if self.shut_down_flag:
          os.system("sudo shutdown -h now")
        elif self.reboot_flag:
          os.system("sudo shutdown -r now")

    elif self.selected_project:
      self.start_selected_project()

  def start(self):
    self.hq_updater.start()
    self.project_updater.start()
    self.update_warrior_hq()
    ioloop.IOLoop.instance().start()

  def reboot_gracefully(self):
    self.shut_down_flag = False
    self.reboot_flag = True
    self.fire_status()
    if self.current_runner:
      self.current_runner.stop_gracefully()
    else:
      ioloop.IOLoop.instance().stop()
      if self.real_shutdown:
        os.system("sudo shutdown -r now")

  def stop_gracefully(self):
    self.shut_down_flag = True
    self.reboot_flag = False
    self.fire_status()
    if self.current_runner:
      self.current_runner.stop_gracefully()
    else:
      ioloop.IOLoop.instance().stop()
      if self.real_shutdown:
        os.system("sudo shutdown -h now")

  def keep_running(self):
    self.shut_down_flag = False
    self.reboot_flag = False
    if self.current_runner:
      self.current_runner.keep_running()
    self.fire_status()

  class Status(object):
    NO_PROJECT = "NO_PROJECT"
    INVALID_SETTINGS = "INVALID_SETTINGS"
    STOPPING_PROJECT = "STOPPING_PROJECT"
    RESTARTING_PROJECT = "RESTARTING_PROJECT"
    RUNNING_PROJECT = "RUNNING_PROJECT"
    SWITCHING_PROJECT = "SWITCHING_PROJECT"
    STARTING_PROJECT = "STARTING_PROJECT"
    SHUTTING_DOWN = "SHUTTING_DOWN"
    REBOOTING = "REBOOTING"

  def fire_status(self):
    self.on_status(self, self.warrior_status())

  def warrior_status(self):
    if self.shut_down_flag:
      return Warrior.Status.SHUTTING_DOWN
    elif self.reboot_flag:
      return Warrior.Status.REBOOTING
    elif not self.config_manager.all_valid():
      return Warrior.Status.INVALID_SETTINGS
    elif self.selected_project == None and self.current_project_name == None:
      return Warrior.Status.NO_PROJECT
    elif self.selected_project:
      if self.selected_project == self.current_project_name:
        if self.current_runner.should_stop():
          return Warrior.Status.RESTARTING_PROJECT
        else:
          return Warrior.Status.RUNNING_PROJECT
      elif self.current_runner:
        return Warrior.Status.SWITCHING_PROJECT
      else:
        return Warrior.Status.STARTING_PROJECT
    else:
      return Warrior.Status.STOPPING_PROJECT

