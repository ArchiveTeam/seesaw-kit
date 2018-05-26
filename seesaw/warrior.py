'''The warrior server.

The warrior phones home to Warrior HQ
(https://github.com/ArchiveTeam/warrior-hq).
'''
import datetime
from distutils.version import StrictVersion
import json
import os
import os.path
import re
import shutil
import subprocess
import sys
import time
import logging

from tornado import gen
from tornado import ioloop
from tornado.httpclient import AsyncHTTPClient

import seesaw
from seesaw.config import NumberConfigValue, StringConfigValue, ConfigValue
from seesaw.config import realize
from seesaw.event import Event
from seesaw.externalprocess import AsyncPopen2
from seesaw.log import InternalTempLogHandler
from seesaw.runner import Runner
import seesaw.six


try:
    from collections import OrderedDict
except ImportError:
    from ordereddict import OrderedDict


if seesaw.six.PY2:
    bigint = long  # @UndefinedVariable  pylint: disable=undefined-variable
else:
    bigint = int


logger = logging.getLogger(__name__)


class ConfigManager(object):
    '''Manages the configuration.'''
    def __init__(self, config_file):
        self.config_file = config_file
        self.config_memory = {}
        self.config_values = OrderedDict()

        self.load()

    def add(self, config_value):
        self.config_values[config_value.name] = config_value
        if config_value.name in self.config_memory:
            remembered_value = self.config_memory[config_value.name]
            if config_value.check_value(remembered_value) is None:
                config_value.set_value(remembered_value)
        self.save()

    def remove(self, name):
        if name in self.config_values:
            del self.config_values[name]
        self.save()

    def all_valid(self):
        return all([c.is_valid() for c in self])

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
        except Exception:
            logger.exception('Error loading config.')
            self.config_memory = {}

    def save(self):
        with open(self.config_file, "w") as f:
            json.dump(self.config_memory, f)

    def __iter__(self):
        return iter(self.config_values.values())

    def editable_values(self):
        return [v for v in self if v.editable]


class BandwidthMonitor(object):
    '''Extracts the bandwidth usage from the system stats.'''
    devre = re.compile(r"^\s*([a-z0-9]+):(.+)$")

    def __init__(self, device):
        self.device = device
        self.prev_time = None
        self.prev_stats = None
        self.bandwidth = None
        self._prev_received = 0
        self._prev_sent = 0
        self._overflow_received = 0
        self._overflow_sent = 0

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
        if self.prev_stats is not None and cur_stats is not None:
            time_delta = cur_time - self.prev_time
            if time_delta:
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
                received = bigint(fields[0])
                sent = bigint(fields[8])
                if self._prev_received > received:
                    self._overflow_received += 2 ** 32
                self._prev_received = received
                if self._prev_sent > sent:
                    self._overflow_sent += 2 ** 32
                self._prev_sent = sent
                return [received + self._overflow_received,
                        sent + self._overflow_sent ]
        return None


class Warrior(object):
    '''The warrior god object.'''
    def __init__(self, projects_dir, data_dir, warrior_hq_url,
                 real_shutdown=False, keep_data=False):
        if not os.access(projects_dir, os.W_OK):
            raise Exception(
                "Couldn't write to projects directory: %s" % projects_dir)
        if not os.access(data_dir, os.W_OK):
            raise Exception("Couldn't write to data directory: %s" % data_dir)

        self.projects_dir = projects_dir
        self.data_dir = data_dir
        self.warrior_hq_url = warrior_hq_url
        self.real_shutdown = real_shutdown
        self.keep_data = keep_data

        # disable the password prompts
        self.gitenv = dict(
            list(os.environ.items()) +
            list({
                'GIT_ASKPASS': 'echo',
                'SSH_ASKPASS': 'echo'
            }.items())
        )

        self.warrior_id = StringConfigValue(
            name="warrior_id",
            title="Warrior ID",
            description="The unique number of your warrior instance.",
            editable=False
        )
        self.selected_project_config_value = StringConfigValue(
            name="selected_project",
            title="Selected project",
            description="The project (to be continued when the warrior "
                        "restarts).",
            default="none",
            editable=False
        )
        self.downloader = StringConfigValue(
            name="downloader",
            title="Your nickname",
            description="We use your nickname to show your results on our "
                        "tracker. Letters and numbers only.",
            regex="^[-_a-zA-Z0-9]{3,30}$",
            advanced=False
        )
        self.concurrent_items = NumberConfigValue(
            name="concurrent_items",
            title="Concurrent items",
            description="How many items should the warrior download at a "
                        "time? (Max: 6)",
            min=1,
            max=6,
            default=2
        )
        self.http_username = StringConfigValue(
            name="http_username",
            title="HTTP username",
            description="Enter a username to protect the web interface, "
                        "or leave empty.",
            default=""
        )
        self.http_password = StringConfigValue(
            name="http_password",
            title="HTTP password",
            description="Enter a password to protect the web interface, "
                        "or leave empty.",
            default=""
        )

        self.config_manager = ConfigManager(os.path.join(projects_dir,
                                                         "config.json"))
        self.config_manager.add(self.warrior_id)
        self.config_manager.add(self.selected_project_config_value)
        self.config_manager.add(self.downloader)
        self.config_manager.add(self.concurrent_items)
        self.config_manager.add(self.http_username)
        self.config_manager.add(self.http_password)

        self.bandwidth_monitor = BandwidthMonitor("eth0")
        self.bandwidth_monitor.update()

        self.runner = Runner(concurrent_items=self.concurrent_items,
                             keep_data=self.keep_data)
        self.runner.on_finish += self.handle_runner_finish

        self.current_project_name = None
        self.current_project = None

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
        self.on_broadcast_message_received = Event()

        self.http_client = AsyncHTTPClient()

        self.installing = False
        self.shut_down_flag = False
        self.reboot_flag = False

        io_loop = ioloop.IOLoop.instance()

        def update_warror_callback():
            io_loop.add_future(
                self.update_warrior_hq(), lambda fut: fut.result()
            )

        def update_project_callback():
            io_loop.add_future(self.update_project(), lambda fut: fut.result())

        self.hq_updater = ioloop.PeriodicCallback(update_warror_callback,
                                                  10 * 60 * 1000)
        self.project_updater = ioloop.PeriodicCallback(update_project_callback,
                                                       30 * 60 * 1000)
        self.forced_reboot_timeout = None

        self.lat_lng = None
        self.find_lat_lng()

        self.install_output = None
        self.broadcast_message = None
        self.contacting_hq_failed = False

        self.internal_log_handler = InternalTempLogHandler()
        self.internal_log_handler.setFormatter(
            logging.Formatter(seesaw.script.run_warrior.LOG_FORMAT))
        self.internal_log_handler.addFilter(
            seesaw.script.run_warrior.LogFilter())
        logging.getLogger().addHandler(self.internal_log_handler)

    def find_lat_lng(self):
        # response = self.http_client.fetch("http://www.maxmind.com/app/mylocation", self.handle_lat_lng, user_agent="")
        pass

    def handle_lat_lng(self, response):
        m = re.search(r"geoip-demo-results-tbodyLatitude/Longitude</td>"
                      r"\s*<td[^>]*>\s*([-/.0-9]+)\s*</td>",
                      response.body)
        if m:
            self.lat_lng = m.group(1)

    def bandwidth_stats(self):
        self.bandwidth_monitor.update()
        return self.bandwidth_monitor.current_stats()

    @gen.coroutine
    def update_warrior_hq(self):
        logger.debug('Update warrior hq.')

        if realize(self.warrior_id) is None:
            headers = {"Content-Type": "application/json"}
            user_agent = "ArchiveTeam Warrior/%s" % seesaw.__version__
            body = json.dumps(
                {"warrior": {"version": seesaw.__version__}}
            )
            response = yield self.http_client.fetch(
                os.path.join(self.warrior_hq_url,
                             "api/register.json"),
                method="POST",
                headers=headers,
                user_agent=user_agent,
                body=body
                )

            if response.code == 200:
                data = json.loads(response.body.decode('utf-8'))
                logger.info("Received Warrior ID '%s'." % data["warrior_id"])
                self.config_manager.set_value("warrior_id", data["warrior_id"])
                self.fire_status()
            else:
                logger.error("HTTP error %s" % (response.code))
                self.fire_status()
                return
        else:
            logger.debug("Warrior ID '%s'." % realize(self.warrior_id))

        headers = {"Content-Type": "application/json"}
        user_agent = "ArchiveTeam Warrior/%s %s" % (seesaw.__version__,
                                                    seesaw.runner_type)
        body = json.dumps({
            "warrior": {
                "warrior_id": realize(self.warrior_id),
                "lat_lng": self.lat_lng,
                "downloader": realize(self.downloader),
                "selected_project": realize(self.selected_project_config_value)
            }})

        response = yield self.http_client.fetch(
            os.path.join(self.warrior_hq_url,
                         "api/update.json"),
            method="POST",
            headers=headers,
            user_agent=user_agent,
            body=body
        )

        if response.code == 200:
            data = json.loads(response.body.decode('utf-8'))

            if StrictVersion(seesaw.__version__) < \
                    StrictVersion(data["warrior"]["seesaw_version"]):
                # time for an update
                logger.info("Reboot for Seesaw update.")
                self.reboot_gracefully()

                # schedule a forced reboot after two days
                self.schedule_forced_reboot()
                return

            projects_list = data["projects"]
            self.projects = OrderedDict(
                [(project["name"], project) for project in projects_list])
            for project_data in self.projects.values():
                if "deadline" in project_data:
                    project_data["deadline_int"] = time.mktime(
                        time.strptime(project_data["deadline"],
                                      "%Y-%m-%dT%H:%M:%SZ"))

            previous_project_choice = realize(
                self.selected_project_config_value)

            if self.selected_project and \
                    self.selected_project not in self.projects:
                yield self.select_project(None)
            elif previous_project_choice in self.projects:
                # select previous project
                yield self.select_project(previous_project_choice)
            elif previous_project_choice == "auto":
                # ArchiveTeam's choice
                if "auto_project" in data:
                    yield self.select_project(data["auto_project"])
                else:
                    yield self.select_project(None)

            self.contacting_hq_failed = False
            self.on_projects_loaded(self, self.projects)

            self.broadcast_message = data.get('broadcast_message')
            self.on_broadcast_message_received(
                self, data.get('broadcast_message'))
        else:
            logger.error("HTTP error %s" % (response.code))
            self.contacting_hq_failed = True

            # We don't set projects to {} because it causes the
            # "Stop Current" project button to disappear
            for name in tuple(self.projects):
                if name != self.selected_project:
                    del self.projects[name]

            self.on_projects_loaded(self, self.projects)

    @gen.coroutine
    def install_project(self, project_name):
        logger.debug('Install project %s', project_name)

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
                subprocess.Popen(
                    args=["git", "config", "remote.origin.url",
                          project["repository"]],
                    cwd=project_path
                ).communicate()

                logger.debug('git pull from %s', project["repository"])
                p = AsyncPopen2(
                    args=["git", "pull"],
                    cwd=project_path,
                    env=self.gitenv
                )
            else:
                logger.debug('git clone')
                p = AsyncPopen2(
                    args=["git", "clone", project["repository"], project_path],
                    env=self.gitenv
                )
            p.on_output += self.collect_install_output
            p.on_end += yield gen.Callback("gitend")

            try:
                p.run()
            except OSError as error:
                logger.exception("Install command error")
                result = 9999
                self.install_output.append(str(error))
            else:
                result = yield gen.Wait("gitend")

            if result != 0:
                self.install_output.append("\ngit returned %d\n" % result)
                logger.error(
                    "Project failed to install: %s",
                    "".join(self.install_output)
                )
                self.on_project_installation_failed(
                    self, project, "".join(self.install_output))
                self.installing = None
                self.failed_projects.add(project_name)

                raise gen.Return(False)
            else:
                logger.debug(
                    "git operation: %s", "".join(self.install_output)
                )

            project_install_file = os.path.join(project_path,
                                                "warrior-install.sh")

            if os.path.exists(project_install_file):
                if not is_executable(project_install_file):
                    logger.warning('File %s is not executable. '
                        'Automatically changing it to be executable!',
                        project_install_file)
                    set_file_executable(project_install_file)

                p = AsyncPopen2(
                    args=[project_install_file],
                    cwd=project_path
                )
                p.on_output += self.collect_install_output
                p.on_end += yield gen.Callback("installend")
                try:
                    p.run()
                except OSError as error:
                    logger.exception("Custom project install file error")
                    result = 9999
                    self.install_output.append(str(error))
                else:
                    result = yield gen.Wait("installend")

                if result != 0:
                    self.install_output.append(
                        "\nCustom installer returned %d\n" % result)
                    logger.error(
                        "Custom installer failed to install: %s",
                        "".join(self.install_output)
                    )
                    self.on_project_installation_failed(
                        self, project, "".join(self.install_output))
                    self.installing = None
                    self.failed_projects.add(project_name)

                    raise gen.Return(False)
                else:
                    logger.debug('Project install file result: %s', result)

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
            logger.debug('Install complete %s', "".join(self.install_output))
            self.on_project_installed(self, project,
                                      "".join(self.install_output))

            self.installing = None

            raise gen.Return(True)

        else:
            logger.warning('Not installing project %s because it is not a '
                'known project or an install is already in progress',
                project_name)

    @gen.coroutine
    def update_project(self):
        logger.debug('Update project.')

        if self.selected_project and \
                (yield self.check_project_has_update(self.selected_project)):
            # restart project
            yield self.start_selected_project(reinstall=True)

    @gen.coroutine
    def check_project_has_update(self, project_name):
        logger.debug('Check project has update %s', project_name)

        if project_name in self.projects:
            project = self.projects[project_name]
            project_path = os.path.join(self.projects_dir, project_name)

            self.install_output = []

            if not os.path.exists(project_path):
                logger.debug("Project doesn't exist.")
                raise gen.Return(True)

            subprocess.Popen(
                args=["git", "config", "remote.origin.url",
                      project["repository"]],
                cwd=project_path
            ).communicate()

            logger.debug('git fetch')

            p = AsyncPopen2(
                args=["git", "fetch"],
                cwd=project_path,
                env=self.gitenv
            )
            p.on_output += self.collect_install_output
            p.on_end += yield gen.Callback("gitend")
            p.run()
            result = yield gen.Wait("gitend")

            if result != 0:
                logger.debug('Got return code %s', result)
                raise gen.Return(True)

            output = subprocess.Popen(
                args=["git", "rev-list", "HEAD..origin/HEAD"],
                cwd=project_path,
                stdout=subprocess.PIPE
            ).communicate()[0]
            if output.strip():
                logger.debug('True')
                raise gen.Return(True)
            else:
                logger.debug('False')
                raise gen.Return(False)

    def collect_install_output(self, data):
        if isinstance(data, seesaw.six.binary_type):
            text = data.decode('ascii', 'replace')
        else:
            text = data

        sys.stdout.write(text)
        text = re.sub("[\x00-\x08\x0b\x0c]", "", text)
        self.install_output.append(text)

    @gen.coroutine
    def select_project(self, project_name):
        logger.debug('Select project %s', project_name)

        if project_name == "auto":
            yield self.update_warrior_hq()
            return

        if project_name not in self.projects:
            logger.debug("Project doesn't exist.")
            project_name = None

        if project_name != self.selected_project:
            # restart
            self.selected_project = project_name
            self.on_project_selected(self, project_name)
            yield self.start_selected_project()

    def clone_project(self, project_name, project_path):
        logger.debug('Clone project %s %s', project_name, project_path)

        version_string = subprocess.Popen(
            args=["git", "log", "-1", "--pretty=%h"],
            cwd=project_path,
            stdout=subprocess.PIPE
        ).communicate()[0].strip().decode('ascii')

        logger.debug('Cloning version %s', version_string)

        project_versioned_path = os.path.join(
            self.data_dir, "projects",
            "%s-%s" % (project_name, version_string))
        if not os.path.exists(project_versioned_path):
            if not os.path.exists(os.path.join(self.data_dir, "projects")):
                os.makedirs(os.path.join(self.data_dir, "projects"))

            subprocess.Popen(
                args=["git", "clone", project_path, project_versioned_path],
                env=self.gitenv
            ).communicate()

        return project_versioned_path

    def load_pipeline(self, pipeline_path, context):
        logger.debug('Load pipeline %s', pipeline_path)

        dirname, basename = os.path.split(pipeline_path)
        if dirname == "":
            dirname = "."

        with open(pipeline_path) as f:
            pipeline_str = f.read()

        logger.debug('Pipeline has been read. Begin ConfigValue collection')
        ConfigValue.start_collecting()

        local_context = context
        global_context = context
        curdir = os.getcwd()
        try:
            os.chdir(dirname)
            logger.debug('Executing pipeline')
            exec(pipeline_str, local_context, global_context)
        finally:
            os.chdir(curdir)

        config_values = ConfigValue.stop_collecting()
        logger.debug('Stopped ConfigValue collecting')

        project = local_context["project"]
        pipeline = local_context["pipeline"]
        pipeline.project = project
        return (project, pipeline, config_values)

    @gen.coroutine
    def start_selected_project(self, reinstall=False):
        logger.debug(
            'Start selected project %s (reinstall=%s)',
            self.selected_project, reinstall
        )
        project_name = self.selected_project

        if project_name in self.projects:
            # install or update project if necessary
            if project_name not in self.installed_projects or \
                    reinstall or \
                    (yield self.check_project_has_update(project_name)):
                result = yield self.install_project(project_name)
                logger.debug('Result of the install process: %s', result)

                if not result:
                    self._fail_starting_project(project_name)
                    return

            # remove the configuration variables from the previous project
            if self.current_project:
                for config_value in self.current_project.config_values:
                    self.config_manager.remove(config_value.name)

            # the path with the project code
            # (this is the most recent code from the repository)
            project_path = os.path.join(self.projects_dir, project_name)

            # clone the project code to a versioned directory
            # where the pipeline is actually run
            project_versioned_path = self.clone_project(project_name,
                                                        project_path)

            # load the pipeline from the versioned directory
            pipeline_path = os.path.join(project_versioned_path, "pipeline.py")

            try:
                (project, pipeline, config_values) = self.load_pipeline(
                    pipeline_path, {"downloader": self.downloader})
            except Exception:
                logger.exception('Error loading pipeline')
                self._fail_starting_project(project_name)
                return

            # add the configuration values to the config manager
            for config_value in config_values:
                self.config_manager.add(config_value)
            project.config_values = config_values

            # start the pipeline
            if not self.shut_down_flag and not self.reboot_flag:
                self.runner.set_current_pipeline(pipeline)

            self.current_project_name = project_name
            self.current_project = project

            self.on_project_refresh(self, self.current_project, self.runner)
            self.fire_status()

            if not self.shut_down_flag and not self.reboot_flag:
                logger.info('Project %s installed', project_name)
                self.runner.start()

        else:
            # project_name not in self.projects,
            # stop the current project (if there is one)
            logger.debug("Project does not exist.")
            self.runner.set_current_pipeline(None)
            self.fire_status()

    def _fail_starting_project(self, project_name):
        logger.warning(
            "Project %s did not install correctly and "
            "we're ignoring this problem.",
            project_name
        )
        self.runner.set_current_pipeline(None)
        self.fire_status()

    def handle_runner_finish(self, runner):
        logger.info("Runner has finished.")

        if self.current_project:
            for config_value in self.current_project.config_values:
                self.config_manager.remove(config_value.name)

        self.current_project_name = None
        self.current_project = None

        self.on_project_refresh(self, self.current_project, self.runner)
        self.fire_status()

        if self.shut_down_flag or self.reboot_flag:
            ioloop.IOLoop.instance().stop()

            if self.real_shutdown:
                if self.shut_down_flag:
                    system_shutdown()
                elif self.reboot_flag:
                    system_reboot()

    def start(self):
        io_loop = ioloop.IOLoop.instance()

        if self.real_shutdown:
            # schedule a reboot
            io_loop.add_timeout(datetime.timedelta(days=7),
                                self.max_age_reached)

        self.hq_updater.start()
        self.project_updater.start()
        io_loop.add_future(self.update_warrior_hq(), lambda fut: fut.result())
        io_loop.start()

    def max_age_reached(self):
        if self.real_shutdown:
            # time for an sanity reboot
            logger.info("Running for more than 7 days. Time to schedule a reboot.")
            self.reboot_gracefully()

            # schedule a forced reboot after two days
            self.schedule_forced_reboot()

    def reboot_gracefully(self):
        self.shut_down_flag = False
        self.reboot_flag = True
        self.fire_status()
        if self.runner.is_active():
            self.runner.set_current_pipeline(None)
        else:
            ioloop.IOLoop.instance().stop()
            if self.real_shutdown:
                system_reboot()

    def schedule_forced_reboot(self):
        if self.real_shutdown and not self.forced_reboot_timeout:
            self.forced_reboot_timeout = ioloop.IOLoop.instance().add_timeout(
                datetime.timedelta(days=2), self.forced_reboot)

    def forced_reboot(self):
        logger.info("Stopping immediately...")
        if self.real_shutdown:
            system_reboot()

    def stop_gracefully(self):
        self.shut_down_flag = True
        self.reboot_flag = False
        self.fire_status()
        if self.runner.is_active():
            self.runner.set_current_pipeline(None)
        else:
            ioloop.IOLoop.instance().stop()
            if self.real_shutdown:
                system_shutdown()

    def forced_stop(self):
        ioloop.IOLoop.instance().stop()
        if self.real_shutdown:
            system_shutdown()

    def keep_running(self):
        self.shut_down_flag = False
        self.reboot_flag = False
        ioloop.IOLoop.instance().add_future(
            self.start_selected_project(), lambda fut: fut.result()
        )
        self.fire_status()

    class Status(object):
        UNINITIALIZED = 'UNINITIALIZED'
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
        elif realize(self.warrior_id) is None:
            return Warrior.Status.UNINITIALIZED
        elif not self.config_manager.all_valid():
            return Warrior.Status.INVALID_SETTINGS
        elif self.selected_project is None and \
                self.current_project_name is None:
            return Warrior.Status.NO_PROJECT
        elif self.selected_project:
            if self.selected_project == self.current_project_name:
                return Warrior.Status.RUNNING_PROJECT
            else:
                return Warrior.Status.STARTING_PROJECT
        else:
            return Warrior.Status.STOPPING_PROJECT


def system_shutdown():
    # Sentinel to tell the host to reboot/shutdown if the warrior is in a
    # Docker container. This will require the host to be monitoring the file
    # of course.
    with open('/tmp/warrior_poweroff_required', 'w') as file_obj:
        file_obj.write(str(time.time()))

    os.system("sudo shutdown -h now")


def system_reboot():
    with open('/tmp/warrior_reboot_required', 'w') as file_obj:
        file_obj.write(str(time.time()))

    os.system("sudo shutdown -r now")


def is_executable(path):
    return bool(os.stat(path).st_mode & 0o100)


def set_file_executable(path):
    assert os.path.isfile(path)

    os.chmod(path, os.stat(path).st_mode | 0o100)
