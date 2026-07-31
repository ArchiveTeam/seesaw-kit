# encoding=utf8
'''Warrior tests.

The Warrior HQ is stood up as a real HTTP server and the projects it offers
are real local git repositories, so installing, updating and loading a
project all run for real rather than against stubs.
'''
from __future__ import unicode_literals

import datetime
import json
import logging
import os
import os.path
import random
import shutil
import stat
import subprocess
import tempfile
import unittest

from tornado.httpclient import HTTPError
from tornado.httpserver import HTTPServer
from tornado.ioloop import IOLoop
from tornado.testing import bind_unused_port
from tornado.web import Application, RequestHandler

import seesaw
from seesaw.config import ConfigValue, NumberConfigValue, StringConfigValue
from tests.test_base import BaseTestCase
from seesaw.warrior import BandwidthMonitor, ConfigManager, Warrior, \
    is_executable, set_file_executable


HAS_GIT = bool(shutil.which('git'))


PIPELINE_SOURCE = '''
from seesaw.config import NumberConfigValue
from seesaw.pipeline import Pipeline
from seesaw.project import Project
from seesaw.task import PrintItem

number_of_things = NumberConfigValue(name="number_of_things",
                                     title="Number of things", default=3)

project = Project(title="A test project")
pipeline = Pipeline(PrintItem())
'''


class ConfigManagerTest(BaseTestCase):
    def setUp(self):
        super(ConfigManagerTest, self).setUp()
        self.temp_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.temp_dir, True)
        self.config_file = os.path.join(self.temp_dir, 'config.json')

    def write_config(self, contents):
        with open(self.config_file, 'w') as file_obj:
            file_obj.write(contents)

    def read_config(self):
        with open(self.config_file) as file_obj:
            return json.load(file_obj)

    def test_a_missing_config_file_starts_empty(self):
        manager = ConfigManager(self.config_file)

        self.assertEqual({}, manager.config_memory)

    def test_a_malformed_config_file_starts_empty(self):
        self.write_config('this is not json')

        manager = ConfigManager(self.config_file)

        self.assertEqual({}, manager.config_memory)

    def test_an_existing_config_file_is_loaded(self):
        self.write_config(json.dumps({'downloader': 'someone'}))

        manager = ConfigManager(self.config_file)

        self.assertEqual({'downloader': 'someone'}, manager.config_memory)

    def test_adding_a_value_writes_the_config_file(self):
        manager = ConfigManager(self.config_file)

        manager.add(StringConfigValue(name='downloader'))

        self.assertTrue(os.path.exists(self.config_file))

    def test_a_remembered_value_is_applied_when_added(self):
        self.write_config(json.dumps({'downloader': 'someone'}))
        manager = ConfigManager(self.config_file)
        config_value = StringConfigValue(name='downloader',
                                         regex='^[a-z]+$')

        manager.add(config_value)

        self.assertEqual('someone', config_value.value)

    def test_a_remembered_value_that_no_longer_validates_is_ignored(self):
        self.write_config(json.dumps({'downloader': '!!!'}))
        manager = ConfigManager(self.config_file)
        config_value = StringConfigValue(name='downloader', default='fallback',
                                         regex='^[a-z]+$')

        manager.add(config_value)

        self.assertEqual('fallback', config_value.value)

    def test_set_value_stores_and_persists(self):
        manager = ConfigManager(self.config_file)
        manager.add(StringConfigValue(name='downloader', regex='^[a-z]+$'))

        self.assertTrue(manager.set_value('downloader', 'someone'))
        self.assertEqual({'downloader': 'someone'}, self.read_config())

    def test_set_value_rejects_an_invalid_value(self):
        manager = ConfigManager(self.config_file)
        manager.add(StringConfigValue(name='downloader', regex='^[a-z]+$'))

        self.assertFalse(manager.set_value('downloader', '!!!'))
        self.assertEqual({}, self.read_config())

    def test_set_value_ignores_an_unknown_name(self):
        manager = ConfigManager(self.config_file)

        self.assertFalse(manager.set_value('nothing', 'value'))

    def test_remove_forgets_the_value(self):
        manager = ConfigManager(self.config_file)
        manager.add(StringConfigValue(name='downloader'))

        manager.remove('downloader')

        self.assertEqual([], list(manager))

    def test_remove_of_an_unknown_name_is_harmless(self):
        manager = ConfigManager(self.config_file)

        manager.remove('nothing')

        self.assertEqual([], list(manager))

    def test_all_valid_requires_every_value_to_be_set(self):
        manager = ConfigManager(self.config_file)
        manager.add(StringConfigValue(name='set', default='value'))

        self.assertTrue(manager.all_valid())

        manager.add(StringConfigValue(name='unset'))

        self.assertFalse(manager.all_valid())

    def test_iteration_keeps_the_insertion_order(self):
        manager = ConfigManager(self.config_file)
        manager.add(StringConfigValue(name='first'))
        manager.add(NumberConfigValue(name='second', default=1))
        manager.add(StringConfigValue(name='third'))

        self.assertEqual(['first', 'second', 'third'],
                         [value.name for value in manager])

    def test_editable_values_leaves_out_the_fixed_ones(self):
        manager = ConfigManager(self.config_file)
        manager.add(StringConfigValue(name='shown'))
        manager.add(StringConfigValue(name='hidden', editable=False))

        self.assertEqual(['shown'],
                         [value.name for value in manager.editable_values()])

    def test_a_new_manager_sees_the_saved_values(self):
        manager = ConfigManager(self.config_file)
        manager.add(StringConfigValue(name='downloader'))
        manager.set_value('downloader', 'someone')

        reloaded = ConfigManager(self.config_file)
        config_value = StringConfigValue(name='downloader')
        reloaded.add(config_value)

        self.assertEqual('someone', config_value.value)


class BandwidthMonitorTest(BaseTestCase):
    def setUp(self):
        super(BandwidthMonitorTest, self).setUp()
        self.temp_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.temp_dir, True)
        self.stats_file = os.path.join(self.temp_dir, 'dev')

    def write_stats(self, received, sent, device='eth0'):
        # /proc/net/dev has two header lines then one line per interface;
        # the counters are bytes, packets, errs, drop, fifo, frame,
        # compressed, multicast for receive then transmit.
        fields = [received, 1, 0, 0, 0, 0, 0, 0, sent, 1, 0, 0, 0, 0, 0, 0]
        with open(self.stats_file, 'w') as file_obj:
            file_obj.write('Inter-|   Receive   |  Transmit\n')
            file_obj.write(' face |bytes ...    |bytes ...\n')
            file_obj.write('  %s: %s\n' % (device,
                                           ' '.join(str(f) for f in fields)))

    def make_monitor(self, device='eth0'):
        monitor = BandwidthMonitor.__new__(BandwidthMonitor)
        monitor.stats_file = self.stats_file
        BandwidthMonitor.__init__(monitor, device)
        return monitor

    def test_reads_the_real_system_stats(self):
        # Whatever interfaces exist, reading must not raise.
        monitor = BandwidthMonitor('lo')

        monitor.update()

        self.assertIn(monitor.current_stats(), (None, monitor.current_stats()))

    def test_an_unknown_device_has_no_stats(self):
        self.write_stats(100, 200, device='eth0')
        monitor = self.make_monitor(device='nosuchdev')

        monitor.update()

        self.assertEqual(None, monitor.prev_stats)
        self.assertEqual(None, monitor.current_stats())

    def test_the_first_reading_has_no_rate_yet(self):
        self.write_stats(100, 200)
        monitor = self.make_monitor()

        self.assertEqual([100, 200], monitor.prev_stats)
        self.assertEqual(None, monitor.bandwidth)
        self.assertEqual(None, monitor.current_stats())

    def test_a_second_reading_produces_a_rate(self):
        self.write_stats(100, 200)
        monitor = self.make_monitor()
        self.write_stats(1100, 2200)

        monitor.update()

        stats = monitor.current_stats()
        self.assertEqual(1100, stats['received'])
        self.assertEqual(2200, stats['sent'])
        self.assertGreater(stats['receiving'], 0)
        self.assertGreater(stats['sending'], 0)

    def test_a_counter_wrap_is_compensated(self):
        self.write_stats(2 ** 32 - 100, 2 ** 32 - 100)
        monitor = self.make_monitor()

        # The kernel counter wrapped back around to a small number.
        self.write_stats(50, 50)
        monitor.update()

        stats = monitor.current_stats()
        self.assertEqual(2 ** 32 + 50, stats['received'])
        self.assertEqual(2 ** 32 + 50, stats['sent'])
        self.assertGreater(stats['receiving'], 0)


class ExecutableBitTest(BaseTestCase):
    def setUp(self):
        super(ExecutableBitTest, self).setUp()
        self.temp_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.temp_dir, True)
        self.path = os.path.join(self.temp_dir, 'script.sh')
        with open(self.path, 'w') as file_obj:
            file_obj.write('#!/bin/sh\nexit 0\n')
        os.chmod(self.path, 0o644)

    def test_a_plain_file_is_not_executable(self):
        self.assertFalse(is_executable(self.path))

    def test_setting_the_bit_makes_it_executable(self):
        set_file_executable(self.path)

        self.assertTrue(is_executable(self.path))
        self.assertTrue(os.stat(self.path).st_mode & stat.S_IXUSR)

    def test_setting_the_bit_keeps_the_other_permissions(self):
        before = os.stat(self.path).st_mode

        set_file_executable(self.path)

        self.assertEqual(before | 0o100, os.stat(self.path).st_mode)

    def test_a_directory_cannot_be_marked_executable(self):
        self.assertRaises(AssertionError, set_file_executable, self.temp_dir)


class AutoProjectConfigTest(BaseTestCase):
    '''The auto-project weighting is pure logic, so it is tested directly.'''

    def setUp(self):
        super(AutoProjectConfigTest, self).setUp()
        self.warrior = Warrior.__new__(Warrior)
        self.warrior.projects = {'a': {}, 'b': {}, 'c': {}}
        self.warrior.selected_project = None
        self.warrior.previous_auto_projects = None
        self.warrior.previous_auto_project = None

    def normalize(self, config):
        return self.warrior.normalize_auto_projects_config(config)

    def test_normalizes_weights_into_fractions(self):
        result = self.normalize([{'project': 'a', 'weight': 1},
                                 {'project': 'b', 'weight': 3}])

        self.assertEqual({'a': 0.25, 'b': 0.75}, result)

    def test_sums_duplicate_projects(self):
        result = self.normalize([{'project': 'a', 'weight': 1},
                                 {'project': 'a', 'weight': 1},
                                 {'project': 'b', 'weight': 2}])

        self.assertEqual({'a': 0.5, 'b': 0.5}, result)

    def test_accepts_float_weights(self):
        result = self.normalize([{'project': 'a', 'weight': 0.5},
                                 {'project': 'b', 'weight': 0.5}])

        self.assertEqual({'a': 0.5, 'b': 0.5}, result)

    def test_rejects_a_non_list(self):
        self.assertEqual(None, self.normalize({'project': 'a', 'weight': 1}))
        self.assertEqual(None, self.normalize('a'))
        self.assertEqual(None, self.normalize(None))

    def test_rejects_an_empty_list(self):
        self.assertEqual(None, self.normalize([]))

    def test_rejects_entries_with_missing_keys(self):
        self.assertEqual(None, self.normalize([{'project': 'a'}]))
        self.assertEqual(None, self.normalize([{'weight': 1}]))

    def test_rejects_a_non_string_project_name(self):
        self.assertEqual(None, self.normalize([{'project': 1, 'weight': 1}]))

    def test_rejects_a_non_numeric_weight(self):
        self.assertEqual(None,
                         self.normalize([{'project': 'a', 'weight': '1'}]))

    def test_rejects_a_boolean_weight(self):
        self.assertEqual(None,
                         self.normalize([{'project': 'a', 'weight': True}]))

    def test_rejects_a_negative_weight(self):
        self.assertEqual(None,
                         self.normalize([{'project': 'a', 'weight': -1}]))

    def test_rejects_weights_that_sum_to_zero(self):
        self.assertEqual(None,
                         self.normalize([{'project': 'a', 'weight': 0}]))

    def test_choose_returns_a_weighted_project(self):
        chosen = set()

        random.seed(1234)
        for dummy in range(200):
            chosen.add(self.warrior.choose_auto_project({'a': 0.5, 'b': 0.5}))

        self.assertEqual({'a', 'b'}, chosen)

    def test_choose_never_picks_a_zero_weight_project(self):
        random.seed(1234)

        chosen = set(self.warrior.choose_auto_project({'a': 1.0, 'b': 0.0})
                     for dummy in range(200))

        self.assertEqual({'a'}, chosen)

    def test_choose_favours_the_heavier_project(self):
        random.seed(1234)

        picks = [self.warrior.choose_auto_project({'a': 0.9, 'b': 0.1})
                 for dummy in range(500)]

        self.assertGreater(picks.count('a'), picks.count('b'))

    def test_select_returns_none_for_an_invalid_config(self):
        self.assertEqual(None, self.warrior.select_auto_project([]))

    def test_select_picks_a_known_project(self):
        random.seed(1234)

        selected = self.warrior.select_auto_project(
            [{'project': 'a', 'weight': 1}])

        self.assertEqual('a', selected)
        self.assertEqual({'a': 1.0}, self.warrior.previous_auto_projects)
        self.assertEqual('a', self.warrior.previous_auto_project)

    def test_select_rejects_a_project_the_warrior_does_not_know(self):
        selected = self.warrior.select_auto_project(
            [{'project': 'unknown', 'weight': 1}])

        self.assertEqual(None, selected)
        self.assertEqual(None, self.warrior.previous_auto_project)

    def test_an_unchanged_config_keeps_the_current_project(self):
        config = [{'project': 'a', 'weight': 1}, {'project': 'b', 'weight': 1}]
        self.warrior.previous_auto_projects = self.normalize(config)
        self.warrior.selected_project = 'b'
        self.warrior.previous_auto_project = 'b'

        self.assertEqual('b', self.warrior.select_auto_project(config))

    def test_a_project_gaining_weight_is_kept(self):
        self.warrior.previous_auto_projects = {'a': 0.5, 'b': 0.5}
        self.warrior.selected_project = 'b'
        self.warrior.previous_auto_project = 'b'
        random.seed(1234)

        selected = self.warrior.select_auto_project(
            [{'project': 'a', 'weight': 1}, {'project': 'b', 'weight': 3}])

        self.assertEqual('b', selected)

    def test_a_project_dropping_to_zero_weight_is_replaced(self):
        self.warrior.previous_auto_projects = {'a': 0.5, 'b': 0.5}
        self.warrior.selected_project = 'b'
        self.warrior.previous_auto_project = 'b'
        random.seed(1234)

        selected = self.warrior.select_auto_project(
            [{'project': 'a', 'weight': 1}])

        self.assertEqual('a', selected)

    def test_a_project_losing_weight_is_sometimes_replaced(self):
        random.seed(1234)
        selections = set()

        for dummy in range(200):
            self.warrior.previous_auto_projects = {'a': 0.1, 'b': 0.9}
            self.warrior.selected_project = 'b'
            self.warrior.previous_auto_project = 'b'
            selections.add(self.warrior.select_auto_project(
                [{'project': 'a', 'weight': 9},
                 {'project': 'b', 'weight': 1}]))

        # Switching is probabilistic, so both outcomes must be reachable.
        self.assertEqual({'a', 'b'}, selections)


class RegisterHandler(RequestHandler):
    def initialize(self, state):
        self.state = state

    def post(self):
        self.state.requests.append('register')
        self.set_status(self.state.register_code, reason='HQ response')
        self.finish(json.dumps({'warrior_id': 'the-warrior-id'}))


class UpdateHandler(RequestHandler):
    def initialize(self, state):
        self.state = state

    def post(self):
        self.state.requests.append(
            ('update', json.loads(self.request.body.decode('utf-8'))))
        self.set_status(self.state.update_code, reason='HQ response')
        self.finish(json.dumps(self.state.update_response))


class HQState(object):
    def __init__(self):
        self.requests = []
        self.register_code = 200
        self.update_code = 200
        self.update_response = {
            'warrior': {'seesaw_version': seesaw.__version__},
            'broadcast_message': 'hello everyone',
            'projects': [],
        }


class WarriorTestCase(BaseTestCase):
    '''Builds a Warrior pointed at a local Warrior HQ.'''

    def setUp(self):
        super(WarriorTestCase, self).setUp()

        self.addCleanup(self.clear_pending_io_loop_stop)

        self.temp_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.temp_dir, True)
        self.projects_dir = os.path.join(self.temp_dir, 'projects')
        self.data_dir = os.path.join(self.temp_dir, 'data')
        os.makedirs(self.projects_dir)
        os.makedirs(self.data_dir)

        self.state = HQState()
        sock, port = bind_unused_port()
        application = Application([
            (r'/api/register.json', RegisterHandler, {'state': self.state}),
            (r'/api/update.json', UpdateHandler, {'state': self.state}),
        ])
        self.server = HTTPServer(application)
        self.server.add_sockets([sock])
        self.addCleanup(self.server.stop)

        self.hq_url = 'http://localhost:%d/' % port

    def clear_pending_io_loop_stop(self):
        '''Undo any IO loop stop the warrior asked for.

        ``stop_gracefully``, ``reboot_gracefully``, ``forced_stop`` and
        ``handle_runner_finish`` all stop the process's IO loop by design.
        That loop is a global singleton shared with every other test, so a
        stop left pending here would cut short the next test's run.
        '''
        io_loop = IOLoop.instance()
        io_loop._stopped = False
        io_loop._running = False
        io_loop._callbacks.clear()

    def make_warrior(self, **kwargs):
        warrior = Warrior(self.projects_dir, self.data_dir, self.hq_url,
                          **kwargs)
        # The warrior attaches a handler to the root logger for the debug
        # log shown in the web interface; take it back off afterwards.
        self.addCleanup(logging.getLogger().removeHandler,
                        warrior.internal_log_handler)
        self.addCleanup(ConfigValue.stop_collecting)
        ConfigValue.start_collecting()
        ConfigValue.stop_collecting()
        return warrior

    def run_coroutine(self, coroutine_function, *args):
        return IOLoop.instance().run_sync(
            lambda: coroutine_function(*args))


class WarriorSetupTest(WarriorTestCase):
    def test_refuses_an_unwritable_projects_directory(self):
        self.assertRaises(Exception, Warrior,
                          os.path.join(self.temp_dir, 'nope'),
                          self.data_dir, self.hq_url)

    def test_refuses_an_unwritable_data_directory(self):
        self.assertRaises(Exception, Warrior, self.projects_dir,
                          os.path.join(self.temp_dir, 'nope'), self.hq_url)

    def test_registers_its_settings_with_the_config_manager(self):
        warrior = self.make_warrior()

        self.assertEqual(
            ['warrior_id', 'selected_project', 'downloader',
             'concurrent_items', 'http_username', 'http_password'],
            [value.name for value in warrior.config_manager])

    def test_writes_a_config_file_into_the_projects_directory(self):
        self.make_warrior()

        self.assertTrue(os.path.exists(
            os.path.join(self.projects_dir, 'config.json')))

    def test_disables_git_password_prompts(self):
        warrior = self.make_warrior()

        self.assertEqual('echo', warrior.gitenv['GIT_ASKPASS'])
        self.assertEqual('echo', warrior.gitenv['SSH_ASKPASS'])

    def test_bandwidth_stats_do_not_raise(self):
        warrior = self.make_warrior()

        # Whether eth0 exists depends on the machine; either answer is fine.
        self.assertIn(warrior.bandwidth_stats(),
                      (None, warrior.bandwidth_stats()))

    def test_find_lat_lng_is_a_no_op(self):
        warrior = self.make_warrior()

        warrior.find_lat_lng()

        self.assertEqual(None, warrior.lat_lng)


class WarriorStatusTest(WarriorTestCase):
    def test_starts_uninitialized(self):
        warrior = self.make_warrior()

        self.assertEqual(Warrior.Status.UNINITIALIZED,
                         warrior.warrior_status())

    def test_reports_invalid_settings_once_registered(self):
        warrior = self.make_warrior()
        warrior.config_manager.set_value('warrior_id', 'the-id')

        self.assertEqual(Warrior.Status.INVALID_SETTINGS,
                         warrior.warrior_status())

    def test_reports_no_project_when_everything_is_configured(self):
        warrior = self.configured_warrior()

        self.assertEqual(Warrior.Status.NO_PROJECT, warrior.warrior_status())

    def test_reports_starting_then_running_a_project(self):
        warrior = self.configured_warrior()
        warrior.selected_project = 'someproject'

        self.assertEqual(Warrior.Status.STARTING_PROJECT,
                         warrior.warrior_status())

        warrior.current_project_name = 'someproject'

        self.assertEqual(Warrior.Status.RUNNING_PROJECT,
                         warrior.warrior_status())

    def test_reports_stopping_when_a_project_is_being_wound_down(self):
        warrior = self.configured_warrior()
        warrior.current_project_name = 'someproject'

        self.assertEqual(Warrior.Status.STOPPING_PROJECT,
                         warrior.warrior_status())

    def test_shutting_down_wins_over_everything(self):
        warrior = self.configured_warrior()
        warrior.selected_project = 'someproject'
        warrior.shut_down_flag = True

        self.assertEqual(Warrior.Status.SHUTTING_DOWN,
                         warrior.warrior_status())

    def test_rebooting_is_reported(self):
        warrior = self.configured_warrior()
        warrior.reboot_flag = True

        self.assertEqual(Warrior.Status.REBOOTING, warrior.warrior_status())

    def test_fire_status_announces_the_status(self):
        warrior = self.configured_warrior()
        statuses = []
        warrior.on_status += lambda w, status: statuses.append(status)

        warrior.fire_status()

        self.assertEqual([Warrior.Status.NO_PROJECT], statuses)

    def configured_warrior(self):
        warrior = self.make_warrior()
        warrior.config_manager.set_value('warrior_id', 'the-id')
        warrior.config_manager.set_value('downloader', 'someone')
        return warrior


class WarriorLifecycleTest(WarriorTestCase):
    def test_reboot_gracefully_sets_the_flags(self):
        warrior = self.make_warrior()

        warrior.reboot_gracefully()

        self.assertTrue(warrior.reboot_flag)
        self.assertFalse(warrior.shut_down_flag)

    def test_stop_gracefully_sets_the_flags(self):
        warrior = self.make_warrior()

        warrior.stop_gracefully()

        self.assertTrue(warrior.shut_down_flag)
        self.assertFalse(warrior.reboot_flag)

    def test_a_reboot_with_work_in_flight_drops_the_pipeline(self):
        warrior = self.make_warrior()
        warrior.runner.active_items.add(object())

        warrior.reboot_gracefully()

        self.assertEqual(None, warrior.runner.pipeline)

    def test_scheduling_a_forced_reboot_needs_real_shutdown(self):
        warrior = self.make_warrior()

        warrior.schedule_forced_reboot()

        self.assertEqual(None, warrior.forced_reboot_timeout)

    def test_a_forced_reboot_without_real_shutdown_does_nothing(self):
        warrior = self.make_warrior()

        warrior.forced_reboot()
        warrior.max_age_reached()

        self.assertFalse(warrior.reboot_flag)

    def test_the_runner_finishing_clears_the_current_project(self):
        warrior = self.make_warrior()
        warrior.current_project_name = 'someproject'
        refreshes = []
        warrior.on_project_refresh += \
            lambda w, project, runner: refreshes.append(project)

        warrior.handle_runner_finish(warrior.runner)

        self.assertEqual(None, warrior.current_project_name)
        self.assertEqual(None, warrior.current_project)
        self.assertEqual([None], refreshes)

    def test_collect_install_output_strips_control_characters(self):
        warrior = self.make_warrior()
        warrior.install_output = []

        warrior.collect_install_output('a\x00b\x08c\x0bd\x0ce')

        self.assertEqual(['abcde'], warrior.install_output)

    def test_collect_install_output_decodes_bytes(self):
        warrior = self.make_warrior()
        warrior.install_output = []

        warrior.collect_install_output(b'plain bytes')

        self.assertEqual(['plain bytes'], warrior.install_output)

    def test_handle_lat_lng_pulls_the_coordinates_out(self):
        warrior = self.make_warrior()

        class Response(object):
            body = ('geoip-demo-results-tbodyLatitude/Longitude</td>'
                    '<td>12.3/45.6</td>')

        warrior.handle_lat_lng(Response())

        self.assertEqual('12.3/45.6', warrior.lat_lng)

    def test_handle_lat_lng_ignores_an_unrecognised_page(self):
        warrior = self.make_warrior()

        class Response(object):
            body = 'nothing useful here'

        warrior.handle_lat_lng(Response())

        self.assertEqual(None, warrior.lat_lng)


class WarriorHQTest(WarriorTestCase):
    def test_registers_and_stores_the_warrior_id(self):
        warrior = self.make_warrior()

        self.run_coroutine(warrior.update_warrior_hq)

        self.assertEqual('the-warrior-id', warrior.warrior_id.value)
        self.assertEqual('register', self.state.requests[0])

    def test_does_not_register_twice(self):
        warrior = self.make_warrior()
        warrior.config_manager.set_value('warrior_id', 'already-known')

        self.run_coroutine(warrior.update_warrior_hq)

        self.assertNotIn('register', self.state.requests)

    def test_reports_itself_to_hq(self):
        warrior = self.make_warrior()
        warrior.config_manager.set_value('warrior_id', 'already-known')
        warrior.config_manager.set_value('downloader', 'someone')

        self.run_coroutine(warrior.update_warrior_hq)

        body = self.state.requests[0][1]['warrior']
        self.assertEqual('already-known', body['warrior_id'])
        self.assertEqual('someone', body['downloader'])
        self.assertEqual('none', body['selected_project'])

    def test_loads_the_offered_projects(self):
        self.state.update_response['projects'] = [
            {'name': 'first', 'title': 'First'},
            {'name': 'second', 'title': 'Second'},
        ]
        warrior = self.make_warrior()
        loaded = []
        warrior.on_projects_loaded += \
            lambda w, projects: loaded.append(projects)

        self.run_coroutine(warrior.update_warrior_hq)

        self.assertEqual(['first', 'second'], list(warrior.projects))
        self.assertEqual(1, len(loaded))

    def test_parses_a_project_deadline(self):
        self.state.update_response['projects'] = [
            {'name': 'first', 'deadline': '2030-01-02T03:04:05Z'},
        ]
        warrior = self.make_warrior()

        self.run_coroutine(warrior.update_warrior_hq)

        self.assertTrue(warrior.projects['first']['deadline_int'] > 0)

    def test_passes_on_the_broadcast_message(self):
        warrior = self.make_warrior()
        messages = []
        warrior.on_broadcast_message_received += \
            lambda w, message: messages.append(message)

        self.run_coroutine(warrior.update_warrior_hq)

        self.assertEqual('hello everyone', warrior.broadcast_message)
        self.assertEqual(['hello everyone'], messages)

    def test_a_newer_seesaw_release_triggers_a_reboot(self):
        self.state.update_response['warrior']['seesaw_version'] = '999.0'
        warrior = self.make_warrior()

        self.run_coroutine(warrior.update_warrior_hq)

        self.assertTrue(warrior.reboot_flag)
        self.assertEqual({}, warrior.projects)

    def test_an_hq_error_is_raised_to_the_caller(self):
        # AsyncHTTPClient.fetch raises on a non-2xx response, so the status
        # code checks inside update_warrior_hq never see an error status;
        # the failure comes out of the coroutine instead.
        self.state.update_code = 500
        warrior = self.make_warrior()

        self.assertRaises(HTTPError, self.run_coroutine,
                          warrior.update_warrior_hq)

    def test_an_hq_error_leaves_the_known_projects_alone(self):
        warrior = self.make_warrior()
        warrior.projects = {'first': {}, 'second': {}}
        warrior.selected_project = 'second'
        self.state.update_code = 500

        self.assertRaises(HTTPError, self.run_coroutine,
                          warrior.update_warrior_hq)

        self.assertEqual(['first', 'second'], sorted(warrior.projects))

    def test_a_registration_error_stops_the_update(self):
        self.state.register_code = 500
        warrior = self.make_warrior()

        self.assertRaises(HTTPError, self.run_coroutine,
                          warrior.update_warrior_hq)

        self.assertEqual(['register'], self.state.requests)
        self.assertEqual(None, warrior.warrior_id.value)

    def test_a_successful_contact_clears_the_failure_flag(self):
        warrior = self.make_warrior()
        warrior.contacting_hq_failed = True

        self.run_coroutine(warrior.update_warrior_hq)

        self.assertFalse(warrior.contacting_hq_failed)


@unittest.skipUnless(HAS_GIT, 'git is not installed')
class WarriorProjectTest(WarriorTestCase):
    '''Installs a project from a real local git repository.'''

    def setUp(self):
        super(WarriorProjectTest, self).setUp()

        self.repo_dir = os.path.join(self.temp_dir, 'repo')
        os.makedirs(self.repo_dir)
        self.write_repo_file('pipeline.py', PIPELINE_SOURCE)
        self.git('init')
        self.git('config', 'user.email', 'test@example.com')
        self.git('config', 'user.name', 'Test')
        self.git('add', '.')
        self.git('commit', '-m', 'first')

        self.project_data = {
            'name': 'testproject',
            'title': 'A test project',
            'repository': self.repo_dir,
        }

    def git(self, *args):
        subprocess.check_call(['git'] + list(args), cwd=self.repo_dir,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def write_repo_file(self, name, contents, mode=None):
        path = os.path.join(self.repo_dir, name)
        with open(path, 'w') as file_obj:
            file_obj.write(contents)
        if mode is not None:
            os.chmod(path, mode)
        return path

    def make_project_warrior(self):
        warrior = self.make_warrior()
        warrior.projects = {'testproject': self.project_data}
        return warrior

    def test_installs_a_project_by_cloning_it(self):
        warrior = self.make_project_warrior()
        installed = []
        warrior.on_project_installed += \
            lambda w, project, output: installed.append(project)

        result = self.run_coroutine(warrior.install_project, 'testproject')

        self.assertTrue(result)
        self.assertIn('testproject', warrior.installed_projects)
        self.assertEqual([self.project_data], installed)
        self.assertTrue(os.path.exists(os.path.join(
            self.projects_dir, 'testproject', 'pipeline.py')))

    def test_announces_that_it_is_installing(self):
        warrior = self.make_project_warrior()
        installing = []
        warrior.on_project_installing += \
            lambda w, project: installing.append(project)

        self.run_coroutine(warrior.install_project, 'testproject')

        self.assertEqual([self.project_data], installing)

    def test_links_the_project_data_directory(self):
        warrior = self.make_project_warrior()

        self.run_coroutine(warrior.install_project, 'testproject')

        link = os.path.join(self.projects_dir, 'testproject', 'data')
        self.assertTrue(os.path.islink(link))
        self.assertEqual(os.path.join(self.data_dir, 'data'),
                         os.readlink(link))

    def test_installing_again_updates_the_existing_clone(self):
        warrior = self.make_project_warrior()
        self.run_coroutine(warrior.install_project, 'testproject')
        self.write_repo_file('extra.txt', 'added later')
        self.git('add', '.')
        self.git('commit', '-m', 'second')

        result = self.run_coroutine(warrior.install_project, 'testproject')

        self.assertTrue(result)
        self.assertTrue(os.path.exists(os.path.join(
            self.projects_dir, 'testproject', 'extra.txt')))

    def test_runs_a_custom_install_script(self):
        self.write_repo_file(
            'warrior-install.sh',
            '#!/bin/sh\necho custom installer ran\ntouch installed-marker\n',
            mode=0o755)
        self.git('add', '.')
        self.git('commit', '-m', 'add installer')
        warrior = self.make_project_warrior()

        result = self.run_coroutine(warrior.install_project, 'testproject')

        self.assertTrue(result)
        self.assertTrue(os.path.exists(os.path.join(
            self.projects_dir, 'testproject', 'installed-marker')))
        self.assertIn('custom installer ran',
                      ''.join(warrior.install_output))

    def test_a_non_executable_install_script_is_made_executable(self):
        path = self.write_repo_file(
            'warrior-install.sh', '#!/bin/sh\ntouch installed-marker\n',
            mode=0o644)
        self.git('add', '.')
        self.git('commit', '-m', 'add installer')
        warrior = self.make_project_warrior()

        result = self.run_coroutine(warrior.install_project, 'testproject')

        self.assertTrue(result)
        self.assertTrue(is_executable(os.path.join(
            self.projects_dir, 'testproject', 'warrior-install.sh')))
        self.assertTrue(os.path.exists(path))

    def test_a_failing_install_script_fails_the_install(self):
        self.write_repo_file('warrior-install.sh',
                             '#!/bin/sh\necho nope\nexit 3\n', mode=0o755)
        self.git('add', '.')
        self.git('commit', '-m', 'add installer')
        warrior = self.make_project_warrior()
        failures = []
        warrior.on_project_installation_failed += \
            lambda w, project, output: failures.append(output)

        result = self.run_coroutine(warrior.install_project, 'testproject')

        self.assertFalse(result)
        self.assertIn('testproject', warrior.failed_projects)
        self.assertEqual(None, warrior.installing)
        self.assertIn('Custom installer returned 3', failures[0])

    def test_a_bad_repository_fails_the_install(self):
        warrior = self.make_project_warrior()
        warrior.projects['testproject'] = dict(
            self.project_data,
            repository=os.path.join(self.temp_dir, 'no-such-repo'))
        failures = []
        warrior.on_project_installation_failed += \
            lambda w, project, output: failures.append(output)

        result = self.run_coroutine(warrior.install_project, 'testproject')

        self.assertFalse(result)
        self.assertIn('testproject', warrior.failed_projects)
        self.assertEqual(1, len(failures))

    def test_a_previously_failed_project_is_cloned_from_scratch(self):
        warrior = self.make_project_warrior()
        self.run_coroutine(warrior.install_project, 'testproject')
        project_path = os.path.join(self.projects_dir, 'testproject')
        with open(os.path.join(project_path, 'leftover.txt'), 'w') as file_obj:
            file_obj.write('junk from the failed attempt')
        warrior.failed_projects.add('testproject')

        self.run_coroutine(warrior.install_project, 'testproject')

        self.assertFalse(os.path.exists(
            os.path.join(project_path, 'leftover.txt')))
        self.assertNotIn('testproject', warrior.failed_projects)

    def test_an_unknown_project_is_not_installed(self):
        warrior = self.make_project_warrior()

        result = self.run_coroutine(warrior.install_project, 'nosuchproject')

        self.assertEqual(None, result)
        self.assertEqual(set(), warrior.installed_projects)

    def test_no_install_starts_while_another_is_running(self):
        warrior = self.make_project_warrior()
        warrior.installing = 'somethingelse'

        result = self.run_coroutine(warrior.install_project, 'testproject')

        self.assertEqual(None, result)

    def test_a_missing_checkout_counts_as_needing_an_update(self):
        warrior = self.make_project_warrior()

        needs_update = self.run_coroutine(warrior.check_project_has_update,
                                          'testproject')

        self.assertTrue(needs_update)

    def test_a_fresh_checkout_needs_no_update(self):
        warrior = self.make_project_warrior()
        self.run_coroutine(warrior.install_project, 'testproject')

        needs_update = self.run_coroutine(warrior.check_project_has_update,
                                          'testproject')

        self.assertFalse(needs_update)

    def test_a_new_upstream_commit_needs_an_update(self):
        warrior = self.make_project_warrior()
        self.run_coroutine(warrior.install_project, 'testproject')
        self.write_repo_file('extra.txt', 'added later')
        self.git('add', '.')
        self.git('commit', '-m', 'second')

        needs_update = self.run_coroutine(warrior.check_project_has_update,
                                          'testproject')

        self.assertTrue(needs_update)

    def test_an_unknown_project_never_needs_an_update(self):
        warrior = self.make_project_warrior()

        self.assertEqual(None,
                         self.run_coroutine(warrior.check_project_has_update,
                                            'nosuchproject'))

    def test_clone_project_makes_a_versioned_copy(self):
        warrior = self.make_project_warrior()
        self.run_coroutine(warrior.install_project, 'testproject')
        project_path = os.path.join(self.projects_dir, 'testproject')

        versioned_path = warrior.clone_project('testproject', project_path)

        self.assertTrue(versioned_path.startswith(
            os.path.join(self.data_dir, 'projects', 'testproject-')))
        self.assertTrue(os.path.exists(
            os.path.join(versioned_path, 'pipeline.py')))

    def test_cloning_the_same_version_twice_reuses_the_copy(self):
        warrior = self.make_project_warrior()
        self.run_coroutine(warrior.install_project, 'testproject')
        project_path = os.path.join(self.projects_dir, 'testproject')

        first = warrior.clone_project('testproject', project_path)
        second = warrior.clone_project('testproject', project_path)

        self.assertEqual(first, second)

    def test_load_pipeline_returns_the_project_and_pipeline(self):
        warrior = self.make_project_warrior()
        self.run_coroutine(warrior.install_project, 'testproject')
        pipeline_path = os.path.join(self.projects_dir, 'testproject',
                                     'pipeline.py')
        before = os.getcwd()

        project, pipeline, config_values = warrior.load_pipeline(
            pipeline_path, {'downloader': 'someone'})

        self.assertEqual('A test project', project.title)
        self.assertEqual(project, pipeline.project)
        self.assertEqual(['number_of_things'],
                         [value.name for value in config_values])
        self.assertEqual(before, os.getcwd())

    def test_load_pipeline_restores_the_directory_after_an_error(self):
        warrior = self.make_project_warrior()
        broken = os.path.join(self.temp_dir, 'broken-pipeline.py')
        with open(broken, 'w') as file_obj:
            file_obj.write('raise ValueError("boom")\n')
        before = os.getcwd()

        self.assertRaises(ValueError, warrior.load_pipeline, broken, {})
        self.assertEqual(before, os.getcwd())

    def test_starting_a_project_installs_and_loads_it(self):
        warrior = self.make_project_warrior()
        warrior.selected_project = 'testproject'
        # Stop short of handing the pipeline to the runner; the runner would
        # then work items forever, which a test cannot wait out.
        warrior.shut_down_flag = True
        refreshes = []
        warrior.on_project_refresh += \
            lambda w, project, runner: refreshes.append(project)

        self.run_coroutine(warrior.start_selected_project)

        self.assertEqual('testproject', warrior.current_project_name)
        self.assertEqual('A test project', warrior.current_project.title)
        self.assertEqual(1, len(refreshes))
        self.assertEqual(
            ['number_of_things'],
            [value.name for value in warrior.current_project.config_values])
        self.assertIn('number_of_things',
                      [value.name for value in warrior.config_manager])

    def test_starting_another_project_drops_the_previous_settings(self):
        warrior = self.make_project_warrior()
        warrior.selected_project = 'testproject'
        warrior.shut_down_flag = True
        self.run_coroutine(warrior.start_selected_project)

        self.run_coroutine(warrior.start_selected_project)

        names = [value.name for value in warrior.config_manager]
        self.assertEqual(1, names.count('number_of_things'))

    def test_starting_an_unknown_project_clears_the_pipeline(self):
        warrior = self.make_project_warrior()
        warrior.selected_project = 'nosuchproject'

        self.run_coroutine(warrior.start_selected_project)

        self.assertEqual(None, warrior.runner.pipeline)
        self.assertEqual(None, warrior.current_project_name)

    def test_a_broken_pipeline_file_stops_the_project_starting(self):
        self.write_repo_file('pipeline.py', 'raise ValueError("boom")\n')
        self.git('add', '.')
        self.git('commit', '-m', 'break it')
        warrior = self.make_project_warrior()
        warrior.selected_project = 'testproject'

        self.run_coroutine(warrior.start_selected_project)

        self.assertEqual(None, warrior.current_project_name)
        self.assertEqual(None, warrior.runner.pipeline)

    def test_a_failed_install_stops_the_project_starting(self):
        self.write_repo_file('warrior-install.sh', '#!/bin/sh\nexit 3\n',
                             mode=0o755)
        self.git('add', '.')
        self.git('commit', '-m', 'add failing installer')
        warrior = self.make_project_warrior()
        warrior.selected_project = 'testproject'

        self.run_coroutine(warrior.start_selected_project)

        self.assertEqual(None, warrior.current_project_name)
        self.assertEqual(None, warrior.runner.pipeline)

    def test_select_project_announces_the_choice(self):
        warrior = self.make_project_warrior()
        warrior.shut_down_flag = True
        selected = []
        warrior.on_project_selected += \
            lambda w, name: selected.append(name)

        self.run_coroutine(warrior.select_project, 'testproject')

        self.assertEqual(['testproject'], selected)
        self.assertEqual('testproject', warrior.selected_project)

    def test_selecting_an_unknown_project_stops_the_current_one(self):
        warrior = self.make_project_warrior()
        warrior.selected_project = 'testproject'
        selected = []
        warrior.on_project_selected += \
            lambda w, name: selected.append(name)

        self.run_coroutine(warrior.select_project, 'nosuchproject')

        self.assertEqual([None], selected)
        self.assertEqual(None, warrior.selected_project)
        self.assertEqual(None, warrior.runner.pipeline)

    def test_selecting_an_unknown_project_with_none_running_is_a_no_op(self):
        warrior = self.make_project_warrior()
        selected = []
        warrior.on_project_selected += \
            lambda w, name: selected.append(name)

        self.run_coroutine(warrior.select_project, 'nosuchproject')

        self.assertEqual([], selected)
        self.assertEqual(None, warrior.selected_project)

    def test_selecting_the_current_project_again_does_nothing(self):
        warrior = self.make_project_warrior()
        warrior.selected_project = 'testproject'
        selected = []
        warrior.on_project_selected += \
            lambda w, name: selected.append(name)

        self.run_coroutine(warrior.select_project, 'testproject')

        self.assertEqual([], selected)

    def test_update_project_reinstalls_when_upstream_moved(self):
        warrior = self.make_project_warrior()
        warrior.selected_project = 'testproject'
        warrior.shut_down_flag = True
        self.run_coroutine(warrior.start_selected_project)
        self.write_repo_file('extra.txt', 'added later')
        self.git('add', '.')
        self.git('commit', '-m', 'second')

        self.run_coroutine(warrior.update_project)

        self.assertTrue(os.path.exists(os.path.join(
            self.projects_dir, 'testproject', 'extra.txt')))

    def test_update_project_does_nothing_without_a_selected_project(self):
        warrior = self.make_project_warrior()

        self.run_coroutine(warrior.update_project)

        self.assertEqual(None, warrior.current_project_name)


class WarriorRealShutdownTest(WarriorTestCase):
    '''The shutdown paths that would otherwise power off the machine.

    ``system_shutdown`` and ``system_reboot`` run ``sudo shutdown``, so the
    tests swap in recorders. Everything leading up to the call is real.
    '''

    def setUp(self):
        super(WarriorRealShutdownTest, self).setUp()

        import seesaw.warrior as warrior_module
        self.warrior_module = warrior_module
        self.calls = []
        self.addCleanup(setattr, warrior_module, 'system_shutdown',
                        warrior_module.system_shutdown)
        self.addCleanup(setattr, warrior_module, 'system_reboot',
                        warrior_module.system_reboot)
        warrior_module.system_shutdown = lambda: self.calls.append('shutdown')
        warrior_module.system_reboot = lambda: self.calls.append('reboot')

    def make_shutdown_warrior(self):
        return self.make_warrior(real_shutdown=True)

    def test_stopping_an_idle_warrior_powers_the_machine_off(self):
        warrior = self.make_shutdown_warrior()

        warrior.stop_gracefully()

        self.assertEqual(['shutdown'], self.calls)

    def test_stopping_a_busy_warrior_waits_for_the_work(self):
        warrior = self.make_shutdown_warrior()
        warrior.runner.active_items.add(object())

        warrior.stop_gracefully()

        self.assertEqual([], self.calls)
        self.assertTrue(warrior.shut_down_flag)

    def test_rebooting_an_idle_warrior_reboots_the_machine(self):
        warrior = self.make_shutdown_warrior()

        warrior.reboot_gracefully()

        self.assertEqual(['reboot'], self.calls)

    def test_forced_stop_powers_the_machine_off(self):
        warrior = self.make_shutdown_warrior()

        warrior.forced_stop()

        self.assertEqual(['shutdown'], self.calls)

    def test_forced_reboot_reboots_the_machine(self):
        warrior = self.make_shutdown_warrior()

        warrior.forced_reboot()

        self.assertEqual(['reboot'], self.calls)

    def test_running_for_too_long_schedules_a_reboot(self):
        warrior = self.make_shutdown_warrior()

        warrior.max_age_reached()

        self.assertTrue(warrior.reboot_flag)
        self.assertIsNotNone(warrior.forced_reboot_timeout)

    def test_the_forced_reboot_is_only_scheduled_once(self):
        warrior = self.make_shutdown_warrior()

        warrior.schedule_forced_reboot()
        first = warrior.forced_reboot_timeout
        warrior.schedule_forced_reboot()

        self.assertIs(first, warrior.forced_reboot_timeout)

    def test_the_runner_finishing_during_a_shutdown_powers_off(self):
        warrior = self.make_shutdown_warrior()
        warrior.shut_down_flag = True

        warrior.handle_runner_finish(warrior.runner)

        self.assertEqual(['shutdown'], self.calls)

    def test_the_runner_finishing_during_a_reboot_reboots(self):
        warrior = self.make_shutdown_warrior()
        warrior.reboot_flag = True

        warrior.handle_runner_finish(warrior.runner)

        self.assertEqual(['reboot'], self.calls)

    def test_the_runner_finishing_normally_does_neither(self):
        warrior = self.make_shutdown_warrior()

        warrior.handle_runner_finish(warrior.runner)

        self.assertEqual([], self.calls)

    def test_start_with_real_shutdown_schedules_the_seven_day_reboot(self):
        self.state.update_response['warrior']['seesaw_version'] = '999.0'
        warrior = self.make_warrior(real_shutdown=True)
        self.addCleanup(warrior.hq_updater.stop)
        self.addCleanup(warrior.project_updater.stop)

        # Record what gets scheduled rather than counting the ioloop's
        # `_timeouts`. That list is global to the process, so it carries
        # whatever earlier tests left behind, and Tornado periodically
        # compacts cancelled entries out of it -- comparing its length
        # before and after is not a stable signal.
        io_loop = IOLoop.instance()
        original_add_timeout = io_loop.add_timeout
        scheduled_deadlines = []

        def recording_add_timeout(deadline, *args, **kwargs):
            scheduled_deadlines.append(deadline)
            return original_add_timeout(deadline, *args, **kwargs)

        io_loop.add_timeout = recording_add_timeout
        self.addCleanup(
            lambda: setattr(io_loop, 'add_timeout', original_add_timeout))

        warrior.start()

        self.assertTrue(warrior.reboot_flag)
        self.assertEqual(['reboot'], self.calls)
        # The max-age timer, plus the forced reboot the update scheduled.
        self.assertIn(datetime.timedelta(days=7), scheduled_deadlines)
        self.assertIn(datetime.timedelta(days=2), scheduled_deadlines)

    def test_keep_running_clears_the_flags(self):
        warrior = self.make_warrior()
        warrior.shut_down_flag = True
        warrior.reboot_flag = True

        warrior.keep_running()

        self.assertFalse(warrior.shut_down_flag)
        self.assertFalse(warrior.reboot_flag)


@unittest.skipUnless(HAS_GIT, 'git is not installed')
class WarriorAutoProjectTest(WarriorProjectTest):
    '''The "ArchiveTeam's choice" path, driven from the HQ response.'''

    def setUp(self):
        super(WarriorAutoProjectTest, self).setUp()

        self.state.update_response['projects'] = [self.project_data]

    def make_auto_warrior(self, choice='auto'):
        warrior = self.make_warrior()
        warrior.config_manager.set_value('warrior_id', 'the-id')
        warrior.config_manager.set_value('downloader', 'someone')
        warrior.config_manager.set_value('selected_project', choice)
        # Stop before the runner is handed the pipeline, which would work
        # items until the process ends.
        warrior.shut_down_flag = True
        return warrior

    def test_a_named_previous_choice_is_restored(self):
        warrior = self.make_auto_warrior(choice='testproject')

        self.run_coroutine(warrior.update_warrior_hq)

        self.assertEqual('testproject', warrior.selected_project)
        self.assertEqual('testproject', warrior.current_project_name)

    def test_auto_falls_back_to_the_single_named_project(self):
        self.state.update_response['auto_project'] = 'testproject'
        warrior = self.make_auto_warrior()

        self.run_coroutine(warrior.update_warrior_hq)

        self.assertEqual('testproject', warrior.selected_project)

    def test_auto_uses_the_weighted_configuration_when_present(self):
        self.state.update_response['auto_projects_config'] = [
            {'project': 'testproject', 'weight': 1},
        ]
        warrior = self.make_auto_warrior()

        self.run_coroutine(warrior.update_warrior_hq)

        self.assertEqual('testproject', warrior.selected_project)
        self.assertEqual({'testproject': 1.0},
                         warrior.previous_auto_projects)

    def test_a_useless_weighted_configuration_falls_back(self):
        self.state.update_response['auto_projects_config'] = [
            {'project': 'testproject', 'weight': 0},
        ]
        self.state.update_response['auto_project'] = 'testproject'
        warrior = self.make_auto_warrior()

        self.run_coroutine(warrior.update_warrior_hq)

        self.assertEqual('testproject', warrior.selected_project)
        self.assertEqual(None, warrior.previous_auto_projects)

    def test_auto_with_nothing_on_offer_selects_nothing(self):
        warrior = self.make_auto_warrior()

        self.run_coroutine(warrior.update_warrior_hq)

        self.assertEqual(None, warrior.selected_project)

    def test_a_project_that_vanished_from_hq_is_dropped(self):
        warrior = self.make_auto_warrior(choice='testproject')
        self.run_coroutine(warrior.update_warrior_hq)
        self.state.update_response['projects'] = []

        self.run_coroutine(warrior.update_warrior_hq)

        self.assertEqual(None, warrior.selected_project)


class WarriorPeriodicUpdateTest(WarriorTestCase):
    '''The timers the warrior arms to call home and refresh its project.'''

    def test_the_hq_timer_contacts_hq(self):
        warrior = self.make_warrior()

        self.io_loop_run(warrior.hq_updater.callback)

        self.assertEqual('the-warrior-id', warrior.warrior_id.value)

    def test_the_project_timer_checks_for_an_update(self):
        warrior = self.make_warrior()

        # No project is selected, so this is a no-op that must not raise.
        self.io_loop_run(warrior.project_updater.callback)

        self.assertEqual(None, warrior.current_project_name)

    def io_loop_run(self, callback):
        '''Run a callback that schedules work, then let the loop drain.'''
        io_loop = IOLoop.instance()
        callback()
        io_loop.add_timeout(datetime.timedelta(seconds=0.3), io_loop.stop)
        io_loop.start()

    def test_start_returns_once_the_warrior_is_asked_to_reboot(self):
        # A newer seesaw release makes update_warrior_hq reboot, which stops
        # the IO loop and so returns from start().
        self.state.update_response['warrior']['seesaw_version'] = '999.0'
        warrior = self.make_warrior()

        warrior.start()

        self.assertTrue(warrior.reboot_flag)
        self.addCleanup(warrior.hq_updater.stop)
        self.addCleanup(warrior.project_updater.stop)


class ChooseAutoProjectEdgeTest(BaseTestCase):
    def test_an_empty_configuration_chooses_nothing(self):
        warrior = Warrior.__new__(Warrior)

        self.assertEqual(None, warrior.choose_auto_project({}))


@unittest.skipUnless(HAS_GIT, 'git is not installed')
class WarriorProjectFailureTest(WarriorProjectTest):
    '''Install paths that fail before or instead of a clean exit code.'''

    def test_git_being_unavailable_fails_the_install(self):
        warrior = self.make_project_warrior()
        # git is looked up on the PATH of the environment handed to the
        # subprocess, so an empty one makes launching it fail outright.
        warrior.gitenv = {'PATH': os.path.join(self.temp_dir, 'no-bin')}
        failures = []
        warrior.on_project_installation_failed += \
            lambda w, project, output: failures.append(output)

        result = self.run_coroutine(warrior.install_project, 'testproject')

        self.assertFalse(result)
        self.assertEqual(1, len(failures))
        self.assertIn('git returned 9999', failures[0])

    def test_an_unlaunchable_install_script_fails_the_install(self):
        # The shebang points at an interpreter that does not exist, so the
        # exec fails rather than the script returning an exit code.
        self.write_repo_file('warrior-install.sh',
                             '#!/nonexistent/interpreter\n', mode=0o755)
        self.git('add', '.')
        self.git('commit', '-m', 'add unlaunchable installer')
        warrior = self.make_project_warrior()
        failures = []
        warrior.on_project_installation_failed += \
            lambda w, project, output: failures.append(output)

        result = self.run_coroutine(warrior.install_project, 'testproject')

        self.assertFalse(result)
        self.assertIn('Custom installer returned 9999', failures[0])

    def test_a_real_data_directory_in_the_project_is_replaced(self):
        os.makedirs(os.path.join(self.repo_dir, 'data'))
        self.write_repo_file(os.path.join('data', 'placeholder.txt'), 'junk')
        self.git('add', '.')
        self.git('commit', '-m', 'add a data directory')
        warrior = self.make_project_warrior()

        result = self.run_coroutine(warrior.install_project, 'testproject')

        self.assertTrue(result)
        link = os.path.join(self.projects_dir, 'testproject', 'data')
        self.assertTrue(os.path.islink(link))
        self.assertFalse(os.path.exists(
            os.path.join(link, 'placeholder.txt')))

    def test_a_failing_git_fetch_counts_as_needing_an_update(self):
        warrior = self.make_project_warrior()
        self.run_coroutine(warrior.install_project, 'testproject')
        # check_project_has_update points origin at the configured
        # repository before fetching, so a bad one makes the fetch fail.
        warrior.projects['testproject'] = dict(
            self.project_data,
            repository=os.path.join(self.temp_dir, 'no-such-repo'))

        needs_update = self.run_coroutine(warrior.check_project_has_update,
                                          'testproject')

        self.assertTrue(needs_update)

    def test_load_pipeline_accepts_a_bare_filename(self):
        warrior = self.make_project_warrior()
        self.run_coroutine(warrior.install_project, 'testproject')
        self.addCleanup(os.chdir, os.getcwd())
        project_path = os.path.join(self.projects_dir, 'testproject')
        os.chdir(project_path)

        project, dummy, dummy2 = warrior.load_pipeline('pipeline.py', {})

        self.assertEqual('A test project', project.title)
        self.assertEqual(os.path.realpath(project_path),
                         os.path.realpath(os.getcwd()))

    def test_starting_a_project_hands_the_pipeline_to_the_runner(self):
        warrior = self.make_project_warrior()
        warrior.selected_project = 'testproject'
        # The runner works items until told to stop; capping it at one is
        # enough to show the pipeline was handed over and started.
        warrior.runner.max_items = 1
        running = []
        warrior.on_project_refresh += \
            lambda w, project, runner: running.append(
                project.title if project else None)

        self.run_coroutine(warrior.start_selected_project)

        self.assertIsNotNone(warrior.runner.pipeline)
        self.assertEqual(1, warrior.runner.item_count)
        # The project is announced when it starts, and again as None once
        # the runner has worked through its allowance.
        self.assertEqual(['A test project', None], running)
        self.assertEqual(None, warrior.current_project_name)

    def test_the_runner_finishing_drops_the_project_settings(self):
        warrior = self.make_project_warrior()
        warrior.selected_project = 'testproject'
        warrior.shut_down_flag = True
        self.run_coroutine(warrior.start_selected_project)

        self.assertIn('number_of_things',
                      [value.name for value in warrior.config_manager])

        warrior.shut_down_flag = False
        warrior.handle_runner_finish(warrior.runner)

        self.assertNotIn('number_of_things',
                         [value.name for value in warrior.config_manager])
        self.assertEqual(None, warrior.current_project)

    def test_selecting_auto_asks_hq_what_to_run(self):
        self.state.update_response['projects'] = [self.project_data]
        self.state.update_response['auto_project'] = 'testproject'
        warrior = self.make_warrior()
        warrior.config_manager.set_value('warrior_id', 'the-id')
        warrior.config_manager.set_value('selected_project', 'auto')
        warrior.projects = {'testproject': self.project_data}
        warrior.shut_down_flag = True

        self.run_coroutine(warrior.select_project, 'auto')

        self.assertEqual('testproject', warrior.selected_project)
        self.assertIn('update',
                      [r[0] if isinstance(r, tuple) else r
                       for r in self.state.requests])
