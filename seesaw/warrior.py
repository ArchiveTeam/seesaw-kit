'''The warrior server.

The warrior phones home to Warrior HQ
(https://github.com/ArchiveTeam/warrior-hq).
'''
import datetime
from distutils.version import StrictVersion
import json
import os
import os.path
import random
import re
import shutil
import signal
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
        self.temp_docker_path = os.path.join(projects_dir, ".docker-temp")
        if os.path.exists(self.temp_docker_path):
            shutil.rmtree(self.temp_docker_path)
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
        self.cleanup_project_versions()

        self.current_project_name = None
        self.current_project = None

        self.selected_project = None
        self.previous_auto_projects = None
        self.auto_project_chain = []
        self.project_retry_at = {}
        self.starting_project = False

        self.projects = {}
        self.image_references = {}
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

        if not realize(self.warrior_id):
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
                project_data["use_docker"] = len(project_data.get("repository", "")) == 0 \
                    and len(project_data.get("docker", "")) > 0
                if "deadline" in project_data:
                    project_data["deadline_int"] = time.mktime(
                        time.strptime(project_data["deadline"],
                                      "%Y-%m-%dT%H:%M:%SZ"))

            yield self.select_hq_project(data)

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
                if name != self.selected_project \
                        and name not in self.auto_project_chain:
                    del self.projects[name]

            self.on_projects_loaded(self, self.projects)

    @gen.coroutine
    def select_hq_project(self, data):
        previous_project_choice = realize(
            self.selected_project_config_value)

        if self.selected_project is not None \
                and self.selected_project not in self.projects \
                and previous_project_choice != 'auto':
            yield self.select_project(None)
        elif previous_project_choice in self.projects:
            # select previous project
            yield self.select_project(previous_project_choice)
        elif previous_project_choice == 'auto':
            # ArchiveTeam's choice
            selected = self.select_auto_project(
                data.get('auto_projects_config')
            )
            if selected is not None:
                yield self.update_project(start=True)
            else:
                yield self.select_project(data.get('auto_project'))

    def auto_projects_changed(self, auto_projects_config):
        if self.previous_auto_projects is None \
                or len(auto_projects_config) != len(self.previous_auto_projects):
            return True
        for priority, weights in auto_projects_config.items():
            previous_weights = self.previous_auto_projects[priority]
            if set(weights) != set(previous_weights) \
                    or any(
                        abs(weight - previous_weights[name]) > 0.001 * max(weight, previous_weights[name])
                        for name, weight in weights.items()
                    ):
                return True
        return False

    def select_auto_project(self, auto_projects_config):
        priorities = self.normalize_auto_projects_config(
            auto_projects_config
        )
        if priorities is None:
            return None
        if not self.auto_projects_changed(priorities):
            return self.auto_project_chain[0] if len(self.auto_project_chain) > 0 else None
        chain = []
        for project_name in self.auto_project_chain:
            auto_projects_config = self.get_auto_project_weights(priorities, chain)
            if len(auto_projects_config) == 0:
                break
            previous_auto_projects = self.get_auto_project_weights(self.previous_auto_projects, chain)
            chosen_project = self.choose_auto_project(
                auto_projects_config, previous_auto_projects, project_name
            )
            if chosen_project is None:
                break
            chain.append(chosen_project)
            if chosen_project != project_name:
                break
        if len(self.auto_project_chain) == 0:
            chosen_project = self.choose_auto_project(priorities[max(priorities)])
            if chosen_project is not None:
                chain.append(chosen_project)
        self.previous_auto_projects = priorities
        self.auto_project_chain = chain
        return chain[0] if len(chain) > 0 else None

    def get_auto_project_weights(self, auto_projects_config, chain):
        for priority in sorted(auto_projects_config, reverse=True):
            weights = {
                name: weight
                for name, weight in auto_projects_config[priority].items()
                if name not in chain
            }
            if len(weights) > 0:
                total = sum(weights.values())
                return {
                    name: weight / total
                    for name, weight in weights.items()
                }
        return {}

    def choose_auto_project(self, auto_projects_config,
                            previous_auto_projects=None,
                            previous_auto_project=None):
        if previous_auto_project is not None:
            previous_selected_weight = previous_auto_projects[previous_auto_project]
            current_selected_weight = auto_projects_config.get(
                previous_auto_project, 0
            )
            # minimize switching between projects
            if current_selected_weight >= previous_selected_weight \
                    or random.random() < current_selected_weight / previous_selected_weight:
                return previous_auto_project
            increased_auto_projects = {}
            for project_name, weight in auto_projects_config.items():
                difference = weight - previous_auto_projects.get(project_name, 0)
                if difference > 0:
                    increased_auto_projects[project_name] = difference
            auto_projects_config = increased_auto_projects
        # Select project not using random.choices to maintain Python<3
        # compatibility.
        total = sum(auto_projects_config.values())
        u = random.random() * total
        for project_name, weight in auto_projects_config.items():
            u -= weight
            if u <= 0:
                return project_name
        # Should not reach this, but not throwing error.
        return None

    def normalize_auto_projects_config(self, auto_projects_config):
        # Basic check if config is correct
        if not isinstance(auto_projects_config, (tuple, list)) \
            or len(auto_projects_config) == 0 \
            or not all(isinstance(d, dict) for d in auto_projects_config):
            return None
        priorities = [d['priority'] for d in auto_projects_config if 'priority' in d]
        if not all(
            isinstance(priority, (int, bigint))
            and not isinstance(priority, bool)
            for priority in priorities
        ):
            return None
        default_priority = min(priorities) - 1 if len(priorities) > 0 else 0
        auto_projects_config = [d.copy() for d in auto_projects_config]
        for d in auto_projects_config:
            if 'weight' not in d:
                d['weight'] = 0
            if 'priority' not in d:
                d['priority'] = default_priority
            if 'project' not in d \
                    or not isinstance(d['project'], seesaw.six.string_types) \
                    or not isinstance(d['weight'], (int, float, bigint)) \
                    or isinstance(d['weight'], bool) \
                    or d['weight'] < 0:
                return None
        project_weights = {}
        for d in auto_projects_config:
            priority = d['priority']
            if priority not in project_weights:
                project_weights[priority] = {}
            weights = project_weights[priority]
            weights[d['project']] = weights.get(d['project'], 0) + d['weight']
        normalized_auto_projects = []
        seen = set()
        for priority in sorted(project_weights, reverse=True):
            weights = {
                name: weight
                for name, weight in project_weights[priority].items()
                if weight > 0 and name not in seen
            }
            if len(weights) == 0:
                continue
            seen.update(weights)
            total = float(sum(weights.values()))
            normalized_auto_projects.append({
                name: weights[name] / total
                for name in sorted(weights)
            })
        if len(normalized_auto_projects) == 0:
            return None
        return dict(enumerate(reversed(normalized_auto_projects)))

    @gen.coroutine
    def get_image_reference(self, repository):
        p = AsyncPopen2(
            args=["crane", "digest", "--full-ref", repository]
        )
        output = []
        p.on_stdout += output.append
        result = yield self.run_install_command(p)
        if result != 0:
            raise OSError("crane digest returned %d" % result)
        raise gen.Return(b"".join(output).decode('utf-8').strip())

    @gen.coroutine
    def run_install_command(self, p):
        p.on_output += self.collect_install_output
        p.on_end += yield gen.Callback("end")
        p.run()
        result = yield gen.Wait("end")
        raise gen.Return(result)

    @gen.coroutine
    def install_project(self, project_name):
        logger.debug('Install project %s', project_name)

        self.installed_projects.discard(project_name)

        if project_name in self.projects and not self.installing:
            self.installing = project_name
            self.install_output = []

            project = self.projects[project_name]
            project_path = os.path.join(self.projects_dir, project_name)
            image = project["use_docker"]
            source = "image" if image else "git"

            self.on_project_installing(self, project)

            if project_name in self.failed_projects \
                or (
                    not image
                    and os.path.exists(project_path)
                    and (
                        project_name in self.image_references
                        or not os.path.exists(os.path.join(project_path, ".git"))
                    )
                ):
                if os.path.exists(project_path):
                    shutil.rmtree(project_path)
                self.failed_projects.discard(project_name)

            if image:
                os.makedirs(self.temp_docker_path)
                image_file = os.path.join(self.temp_docker_path, "image.tar")

            try:
                if image:
                    reference = yield self.get_image_reference(project["docker"])
                    p = AsyncPopen2(
                        args=["crane", "export", reference, image_file]
                    )
                elif os.path.exists(project_path):
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
                result = yield self.run_install_command(p)

                if image and result == 0:
                    p = AsyncPopen2(
                        args=["tar", "-xf", image_file, "-C", self.temp_docker_path, "grab"]
                    )
                    result = yield self.run_install_command(p)
                    image_project_path = os.path.join(self.temp_docker_path, "grab")
                    if result == 0 \
                        and not os.path.isfile(os.path.join(image_project_path, "pipeline.py")):
                        raise OSError("Image has no /grab/pipeline.py")

                if image and result == 0:
                    if os.path.exists(project_path):
                        shutil.rmtree(project_path)
                    os.rename(image_project_path, project_path)
            except OSError as error:
                logger.exception("Install command error")
                result = 9999
                self.install_output.append(str(error))
            finally:
                if image:
                    shutil.rmtree(self.temp_docker_path)

            if result != 0:
                self.install_output.append("\n%s returned %d\n" % (source, result))
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
                    "%s operation: %s", source, "".join(self.install_output)
                )

            project_install_file = os.path.join(project_path,
                                                "warrior-install.sh")

            if image and os.path.exists(project_install_file) \
                and os.path.getsize(project_install_file) == 0:
                os.remove(project_install_file)

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
                try:
                    result = yield self.run_install_command(p)
                except OSError as error:
                    logger.exception("Custom project install file error")
                    result = 9999
                    self.install_output.append(str(error))

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

            if image:
                self.image_references[project_name] = reference
            elif project_name in self.image_references:
                del self.image_references[project_name]
            self.installed_projects.add(project_name)
            self.project_retry_at.pop(project_name, None)
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
    def update_project(self, start=False):
        logger.debug('Update project.')
        if self.starting_project:
            raise gen.Return(None)
        auto = realize(self.selected_project_config_value) == 'auto' \
            and self.previous_auto_projects is not None
        if auto and not start:
            raise gen.Return(None)
        self.starting_project = True
        chain = self.auto_project_chain
        projects = self.projects
        try:
            if auto:
                yield self.update_auto_project(chain, projects)
            elif start:
                yield self.start_selected_project()
            elif self.selected_project is not None:
                update = yield self.check_project_has_update(self.selected_project)
                if update:
                    yield self.start_selected_project(reinstall=True)
        finally:
            self.starting_project = False
            if chain is not self.auto_project_chain \
                    or projects != self.projects:
                yield self.update_project(start=True)

    @gen.coroutine
    def update_auto_project(self, chain, projects):
        def selection_is_current():
            return chain is self.auto_project_chain \
                and projects == self.projects \
                and not self.shut_down_flag \
                and not self.reboot_flag

        index = 0
        while selection_is_current():
            if index == len(chain):
                remaining = self.get_auto_project_weights(self.previous_auto_projects, chain)
                if len(remaining) == 0:
                    break
                chain.append(self.choose_auto_project(remaining))
            project_name = chain[index]
            index += 1
            if project_name not in self.projects:
                continue
            result = None
            try:
                update = yield self.check_project_has_update(project_name)
                if not selection_is_current():
                    break
                retry_at = self.project_retry_at.get(project_name)
                if not update \
                        and retry_at is not None \
                        and time.time() < retry_at:
                    continue
                installed = True
                if update:
                    installed = yield self.install_project(project_name)
                    if not selection_is_current():
                        break
                if installed:
                    result = yield self.check_project(project_name)
            except Exception:
                logger.exception('Could not prepare project %s', project_name)
            if not selection_is_current():
                break
            if result is None:
                if project_name == self.current_project_name \
                        and self.runner.pipeline is not None:
                    raise gen.Return(None)
                continue
            if result['runnable']:
                self.project_retry_at.pop(project_name, None)
                try:
                    started = yield self.start_selected_project(prepared_project=project_name)
                    if started:
                        raise gen.Return(None)
                except OSError:
                    logger.exception('Could not start project %s', project_name)
                result = {
                    'reason': 'Error loading pipeline',
                    'retry_after': 0
                }
            logger.warning('Project %s is not runnable: %s', project_name,
                           result['reason'])
            self.project_retry_at[project_name] = time.time() + result['retry_after']
            if project_name == self.current_project_name:
                self.selected_project = None
                self.on_project_selected(self, None)
                self.runner.set_current_pipeline(None)
                self.fire_status()
        if selection_is_current():
            self.selected_project = None
            self.on_project_selected(self, None)
            self.runner.set_current_pipeline(None)
            if not self.runner.is_active():
                self.handle_runner_finish(self.runner)
            self.fire_status()
            self.cleanup_project_versions()

    @gen.coroutine
    def check_project_has_update(self, project_name):
        logger.debug('Check project has update %s', project_name)

        if project_name in self.projects:
            project = self.projects[project_name]
            project_path = os.path.join(self.projects_dir, project_name)

            self.install_output = []

            if project_name not in self.installed_projects \
                or not os.path.exists(project_path):
                logger.debug("Project not installed.")
                raise gen.Return(True)

            try:
                if project["use_docker"]:
                    if project_name not in self.image_references:
                        raise gen.Return(True)
                    reference = yield self.get_image_reference(project["docker"])
                    raise gen.Return(self.image_references.get(project_name) != reference)

                if project_name in self.image_references:
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
                result = yield self.run_install_command(p)

                if result != 0:
                    raise OSError("git fetch returned %d" % result)

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
            except OSError as error:
                logger.warning("Could not check update for project %s: %s",
                               project_name, error)
                raise gen.Return(False)

    @gen.coroutine
    def check_project(self, project_name, timeout=60):
        p = AsyncPopen2(
            args=[
                sys.executable,
                '-m',
                'seesaw.script.check_project',
                os.path.abspath(os.path.join(self.projects_dir, project_name, 'pipeline.py')),
                realize(self.downloader)
            ],
            cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        )
        output = []
        p.on_stdout += output.append
        self.install_output = []
        deadline = ioloop.IOLoop.instance().time() + timeout
        pending = self.run_install_command(p)
        try:
            try:
                result = yield gen.with_timeout(deadline, pending)
            except gen.TimeoutError:
                os.killpg(p.pipe.pid, signal.SIGKILL)
                yield pending
                raise ValueError('Project check timed out')
            if result != 0:
                raise ValueError('Project check returned %d' % result)
            result = b''.join(output).rsplit(b'\nwarrior_check_result: ', 1)[1]
            result = json.loads(result.splitlines()[0].decode('utf-8'))
            result = {
                'runnable': result['runnable'],
                'reason': result.get('reason', ''),
                'retry_after': result.get('retry_after', 0)
            }
        except Exception as error:
            result = {
                'runnable': False,
                'reason': str(error),
                'retry_after': 0
            }
        raise gen.Return(result)

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

        self.previous_auto_projects = None
        self.auto_project_chain = []

        if project_name not in self.projects:
            logger.debug("Project doesn't exist.")
            project_name = None

        if project_name != self.selected_project:
            # restart
            self.selected_project = project_name
            self.on_project_selected(self, project_name)
            if project_name is None:
                yield self.start_selected_project()
            else:
                yield self.update_project(start=True)

    def cleanup_project_versions(self):
        if self.keep_data:
            return
        projects_path = os.path.realpath(os.path.join(self.data_dir, "projects"))
        if os.path.exists(projects_path):
            project_paths = set()
            for item in self.runner.active_items:
                project_paths.add(item.pipeline.cwd)
            if self.runner.pipeline:
                project_paths.add(self.runner.pipeline.cwd)
            for name in os.listdir(projects_path):
                path = os.path.join(projects_path, name)
                if path not in project_paths and os.path.isdir(path):
                    shutil.rmtree(path)

    def clone_project(self, project_name, project_path):
        logger.debug('Clone project %s %s', project_name, project_path)

        image = self.projects[project_name]["use_docker"]
        if image:
            version_string = self.image_references[project_name].split("@")[-1].replace(":", "-")
        else:
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

            if image:
                try:
                    shutil.copytree(
                        project_path,
                        project_versioned_path,
                        symlinks=True
                    )
                    os.remove(os.path.join(project_versioned_path, "data"))
                except Exception:
                    if os.path.exists(project_versioned_path):
                        shutil.rmtree(project_versioned_path)
                    raise
            else:
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
    def start_selected_project(self, reinstall=False, prepared_project=None):
        project_name = self.selected_project
        if prepared_project is not None:
            project_name = prepared_project
        logger.debug(
            'Start selected project %s (reinstall=%s)',
            project_name, reinstall
        )

        if project_name in self.projects:
            image = self.projects[project_name]["use_docker"]

            # install or update project if necessary
            update = prepared_project is None \
                and (project_name not in self.installed_projects or reinstall)
            if prepared_project is None and not update:
                update = yield self.check_project_has_update(project_name)
            if update:
                result = yield self.install_project(project_name)
                logger.debug('Result of the install process: %s', result)

                if not result:
                    if image:
                        same_source = project_name in self.image_references
                    else:
                        same_source = project_name not in self.image_references
                    if project_name == self.current_project_name \
                        and self.runner.pipeline is not None \
                        and same_source:
                        logger.warning("Keeping current version for project %s", project_name)
                        return
                    self._fail_starting_project(project_name)
                    return

            # the path with the project code
            # (this is the most recent code from the repository)
            project_path = os.path.join(self.projects_dir, project_name)

            # clone the project code to a versioned directory
            # where the pipeline is actually run
            project_versioned_path = self.clone_project(project_name,
                                                        project_path)
            if prepared_project is not None \
                    and self.runner.pipeline is not None \
                    and self.runner.pipeline.cwd == project_versioned_path:
                if self.selected_project != project_name:
                    self.selected_project = project_name
                    self.on_project_selected(self, project_name)
                raise gen.Return(True)

            # load the pipeline from the versioned directory
            pipeline_path = os.path.join(project_versioned_path, "pipeline.py")

            try:
                (project, pipeline, config_values) = self.load_pipeline(
                    pipeline_path, {"downloader": self.downloader})
            except Exception:
                logger.exception('Error loading pipeline')
                if prepared_project is None:
                    self._fail_starting_project(project_name)
                else:
                    self.cleanup_project_versions()
                return

            # remove the configuration variables from the previous project
            if self.current_project:
                for config_value in self.current_project.config_values:
                    self.config_manager.remove(config_value.name)

            # add the configuration values to the config manager
            for config_value in config_values:
                self.config_manager.add(config_value)
            project.config_values = config_values

            if self.selected_project != project_name:
                self.selected_project = project_name
                self.on_project_selected(self, project_name)

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

        self.cleanup_project_versions()
        raise gen.Return(True)

    def _fail_starting_project(self, project_name):
        logger.warning(
            "Project %s did not install correctly and "
            "we're ignoring this problem.",
            project_name
        )
        self.runner.set_current_pipeline(None)
        self.fire_status()
        self.cleanup_project_versions()

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
            self.update_project(start=True), lambda fut: fut.result()
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
        elif not realize(self.warrior_id):
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
