# encoding=utf8
'''Web interface tests.

The handlers are exercised through a real tornado server over real HTTP.
The only stand-in is the SockJS session object, which normally comes from a
live WebSocket transport; the connection class itself is the real one.
'''
from __future__ import unicode_literals

import base64
import json
import logging
import os
import os.path
import shutil
import tempfile
import time

from tornado.httpclient import AsyncHTTPClient, HTTPRequest
from tornado.ioloop import IOLoop
from tornado.testing import bind_unused_port

import seesaw
from seesaw.item import Item
from seesaw.pipeline import Pipeline
from seesaw.project import Project
from seesaw.runner import SimpleRunner
from seesaw.task import PrintItem
from tests.test_base import BaseTestCase
from seesaw.warrior import Warrior
from seesaw.web import ItemMonitor, SeesawConnection, hash_string, \
    start_runner_server, start_warrior_server


class RecordingSession(object):
    '''Stands in for the SockJS session behind a connection.'''
    counter = 0

    def __init__(self):
        RecordingSession.counter += 1
        self.session_id = 'session-%d' % RecordingSession.counter
        self.messages = []
        self.is_closed = False

    def send_message(self, message, binary=False):
        self.messages.append(json.loads(message))


def make_connection():
    connection = SeesawConnection(RecordingSession())
    return connection


class WebTestCase(BaseTestCase):
    def setUp(self):
        super(WebTestCase, self).setUp()

        # SeesawConnection keeps its clients and monitors on the class, so
        # leftovers from one test would leak into the next.
        self._original_state = (SeesawConnection.clients,
                                SeesawConnection.item_monitors,
                                SeesawConnection.warrior,
                                SeesawConnection.project,
                                SeesawConnection.runner)
        SeesawConnection.clients = set()
        SeesawConnection.item_monitors = dict()
        SeesawConnection.warrior = None
        SeesawConnection.project = None
        SeesawConnection.runner = None
        self.addCleanup(self.restore_connection_state)

    def restore_connection_state(self):
        (SeesawConnection.clients,
         SeesawConnection.item_monitors,
         SeesawConnection.warrior,
         SeesawConnection.project,
         SeesawConnection.runner) = self._original_state


class HTTPTestCase(WebTestCase):
    '''Starts a real runner web interface on a free port.'''

    def start_server(self, **kwargs):
        sock, port = bind_unused_port()
        sock.close()

        self.project = Project(title='Example project')
        self.pipeline = Pipeline(PrintItem())
        self.pipeline.project = self.project
        self.runner = SimpleRunner(self.pipeline, max_items=1)

        server = start_runner_server(self.project, self.runner,
                                     bind_address='localhost',
                                     port_number=port, **kwargs)
        self.addCleanup(server.stop)

        self.base_url = 'http://localhost:%d' % port

        return server

    def fetch(self, path, **kwargs):
        '''Fetch a URL, running the IO loop until the response arrives.'''
        result = {}
        client = AsyncHTTPClient()

        def handle_response(response):
            result['response'] = response
            IOLoop.instance().stop()

        client.fetch(HTTPRequest(self.base_url + path, **kwargs),
                     handle_response)

        # A handler may stop the loop itself (the "stop now" command does),
        # so keep pumping until the response is in hand.
        deadline = time.time() + 10
        while 'response' not in result and time.time() < deadline:
            IOLoop.instance().start()

        self.assertIn('response', result)

        return result['response']


class RunnerServerTest(HTTPTestCase):
    def test_serves_the_index_page(self):
        self.start_server()

        response = self.fetch('/')

        self.assertEqual(200, response.code)
        self.assertIn(b'<html', response.body.lower())

    def test_serves_static_files(self):
        self.start_server()

        response = self.fetch('/style.css')

        self.assertEqual(200, response.code)
        self.assertIn(b'body', response.body)

    def test_unknown_paths_are_not_found(self):
        self.start_server()

        response = self.fetch('/no/such/thing')

        self.assertEqual(404, response.code)

    def test_stop_asks_the_runner_to_stop(self):
        self.start_server()

        response = self.fetch('/api/stop', method='POST', body='')

        self.assertEqual(200, response.code)
        self.assertEqual(b'OK', response.body)
        self.assertTrue(self.runner.stop_flag)

    def test_keep_running_clears_the_stop_flag(self):
        self.start_server()
        self.fetch('/api/stop', method='POST', body='')

        response = self.fetch('/api/keep_running', method='POST', body='')

        self.assertEqual(200, response.code)
        self.assertFalse(self.runner.stop_flag)

    def test_stop_now_is_accepted(self):
        self.start_server()

        response = self.fetch('/api/stop_now', method='POST', body='')

        self.assertEqual(200, response.code)
        self.assertEqual(b'OK', response.body)


class RunnerServerAuthTest(HTTPTestCase):
    def basic_auth(self, username, password):
        raw = ('%s:%s' % (username, password)).encode('ascii')
        return {'Authorization':
                'Basic ' + base64.b64encode(raw).decode('ascii')}

    def test_no_password_means_no_authentication(self):
        self.start_server(http_password='')

        self.assertEqual(200, self.fetch('/').code)

    def test_a_blank_password_means_no_authentication(self):
        self.start_server(http_password='   ')

        self.assertEqual(200, self.fetch('/').code)

    def test_a_password_is_required_when_one_is_set(self):
        self.start_server(http_password='secret')

        response = self.fetch('/')

        self.assertEqual(401, response.code)
        self.assertIn('Basic realm=',
                      response.headers.get('WWW-Authenticate'))
        self.assertIn(b'401 Authentication Required', response.body)

    def test_the_right_password_is_accepted(self):
        self.start_server(http_password='secret')

        response = self.fetch('/', headers=self.basic_auth('', 'secret'))

        self.assertEqual(200, response.code)

    def test_the_wrong_password_is_rejected(self):
        self.start_server(http_password='secret')

        response = self.fetch('/', headers=self.basic_auth('', 'wrong'))

        self.assertEqual(401, response.code)

    def test_the_username_is_checked_when_one_is_set(self):
        self.start_server(http_username='someone', http_password='secret')

        self.assertEqual(
            200,
            self.fetch('/', headers=self.basic_auth('someone', 'secret')).code)
        self.assertEqual(
            401,
            self.fetch('/', headers=self.basic_auth('other', 'secret')).code)

    def test_any_username_is_allowed_when_none_is_set(self):
        self.start_server(http_password='secret')

        response = self.fetch('/', headers=self.basic_auth('anyone', 'secret'))

        self.assertEqual(200, response.code)


class HashStringTest(BaseTestCase):
    def test_hashes_a_message(self):
        self.assertEqual(hash_string('hello'), hash_string('hello'))
        self.assertNotEqual(hash_string('hello'), hash_string('goodbye'))

    def test_treats_none_as_empty(self):
        self.assertEqual(hash_string(''), hash_string(None))

    def test_replaces_non_ascii_characters(self):
        # Should not raise, and should still produce a digest.
        self.assertEqual(32, len(hash_string('héllo')))


class SeesawConnectionTest(WebTestCase):
    def setUp(self):
        super(SeesawConnectionTest, self).setUp()

        self.pipeline = Pipeline(PrintItem())
        self.project = Project(title='Example project')
        self.pipeline.project = self.project
        self.runner = SimpleRunner(self.pipeline, max_items=1)

    def make_item(self, item_id='the-id', number=1):
        return Item(self.pipeline, item_id, number, keep_data=True,
                    prepare_data_directory=False)

    def events(self, connection):
        return [message['event_name']
                for message in connection.session.messages]

    def message_for(self, connection, event_name):
        '''The payload of the most recent message with this event name.'''
        for message in reversed(connection.session.messages):
            if message['event_name'] == event_name:
                return message['message']

        raise AssertionError('No %s message was sent' % event_name)

    def test_opening_registers_the_client_and_sends_the_instance_id(self):
        connection = make_connection()

        connection.on_open(None)

        self.assertIn(connection, SeesawConnection.clients)
        self.assertEqual('instance_id', self.events(connection)[0])
        self.assertEqual(SeesawConnection.instance_id,
                         self.message_for(connection, 'instance_id'))

    def test_closing_removes_the_client(self):
        connection = make_connection()
        connection.on_open(None)

        connection.on_close()

        self.assertNotIn(connection, SeesawConnection.clients)

    def test_reports_no_project_when_none_is_selected(self):
        connection = make_connection()

        connection.on_open(None)

        self.assertEqual(None, self.message_for(connection,
                                                'project.refresh'))

    def test_reports_the_current_project_on_open(self):
        SeesawConnection.project = self.project
        SeesawConnection.runner = self.runner
        connection = make_connection()

        connection.on_open(None)

        refresh = self.message_for(connection, 'project.refresh')
        self.assertEqual('Example project', refresh['project']['title'])
        self.assertEqual('running', refresh['status'])
        self.assertEqual([], refresh['items'])

    def test_reports_a_stopping_project(self):
        SeesawConnection.project = self.project
        SeesawConnection.runner = self.runner
        self.runner.stop_flag = True
        connection = make_connection()

        connection.on_open(None)

        self.assertEqual('stopping',
                         self.message_for(connection,
                                          'project.refresh')['status'])

    def test_on_message_is_ignored(self):
        connection = make_connection()

        connection.on_message('anything')

        self.assertEqual([], connection.session.messages)

    def test_broadcast_reaches_every_client(self):
        first = make_connection()
        second = make_connection()
        first.on_open(None)
        second.on_open(None)

        SeesawConnection.broadcast('the.event', {'a': 1})

        self.assertIn('the.event', self.events(first))
        self.assertIn('the.event', self.events(second))

    def test_broadcast_stamps_the_session_id(self):
        connection = make_connection()
        connection.on_open(None)

        SeesawConnection.broadcast('the.event', {'a': 1})

        message = self.message_for(connection, 'the.event')
        self.assertEqual(connection.session.session_id, message['session_id'])

    def test_broadcast_of_an_empty_message_is_sent_as_is(self):
        connection = make_connection()
        connection.on_open(None)

        SeesawConnection.broadcast('the.event', None)

        self.assertEqual(None, self.message_for(connection, 'the.event'))

    def test_broadcast_timestamp(self):
        connection = make_connection()
        connection.on_open(None)

        SeesawConnection.broadcast_timestamp()

        self.assertTrue(self.message_for(connection, 'timestamp')['timestamp'])

    def test_runner_status_is_broadcast(self):
        connection = make_connection()
        connection.on_open(None)

        SeesawConnection.handle_runner_status(self.runner, 'anything')

        self.assertEqual('running',
                         self.message_for(connection,
                                          'runner.status')['status'])

    def test_project_refresh_updates_the_stored_project(self):
        connection = make_connection()
        connection.on_open(None)

        SeesawConnection.handle_project_refresh(None, self.project,
                                                self.runner)

        self.assertEqual(self.project, SeesawConnection.project)
        self.assertEqual(self.runner, SeesawConnection.runner)
        self.assertEqual('Example project',
                         self.message_for(connection,
                                          'project.refresh')['project']
                         ['title'])

    def test_project_refresh_without_a_project_broadcasts_none(self):
        connection = make_connection()
        connection.on_open(None)

        SeesawConnection.handle_project_refresh(None, None, None)

        self.assertEqual(None, self.message_for(connection,
                                                'project.refresh'))

    def test_project_selected_is_broadcast(self):
        connection = make_connection()
        connection.on_open(None)

        SeesawConnection.handle_project_selected(None, 'someproject')

        self.assertEqual(
            'someproject',
            self.message_for(connection,
                             'warrior.project_selected')['project'])

    def test_project_installing_is_broadcast(self):
        connection = make_connection()
        connection.on_open(None)

        SeesawConnection.handle_project_installing(None, 'someproject')

        self.assertEqual(
            'someproject',
            self.message_for(connection,
                             'warrior.project_installing')['project'])

    def test_install_output_line_endings_are_normalised(self):
        connection = make_connection()
        connection.on_open(None)

        SeesawConnection.handle_project_installed(None, 'someproject',
                                                  'a\r\nb\rc')

        self.assertEqual(
            'a\nb\nc',
            self.message_for(connection,
                             'warrior.project_installed')['output'])

    def test_failed_install_output_is_broadcast(self):
        connection = make_connection()
        connection.on_open(None)

        SeesawConnection.handle_project_installation_failed(
            None, 'someproject', 'it broke\r\n')

        message = self.message_for(connection,
                                   'warrior.project_installation_failed')
        self.assertEqual('someproject', message['project'])
        self.assertEqual('it broke\n', message['output'])

    def test_a_broadcast_message_carries_its_hash(self):
        connection = make_connection()
        connection.on_open(None)

        SeesawConnection.handle_broadcast_message(None, 'hello everyone')

        message = self.message_for(connection, 'warrior.broadcast_message')
        self.assertEqual('hello everyone', message['message'])
        self.assertEqual(hash_string('hello everyone'), message['hash'])

    def test_warrior_status_is_broadcast(self):
        connection = make_connection()
        connection.on_open(None)

        SeesawConnection.handle_warrior_status(None, 'RUNNING_PROJECT')

        self.assertEqual('RUNNING_PROJECT',
                         self.message_for(connection,
                                          'warrior.status')['status'])

    def test_starting_and_finishing_an_item_tracks_a_monitor(self):
        item = self.make_item()

        SeesawConnection.handle_start_item(self.runner, self.pipeline, item)

        self.assertIn(item, SeesawConnection.item_monitors)

        SeesawConnection.handle_finish_item(self.runner, self.pipeline, item)

        self.assertNotIn(item, SeesawConnection.item_monitors)

    def test_open_includes_the_items_already_running(self):
        SeesawConnection.project = self.project
        SeesawConnection.runner = self.runner
        item = self.make_item()
        item['item_name'] = 'thing'
        SeesawConnection.handle_start_item(self.runner, self.pipeline, item)

        connection = make_connection()
        connection.on_open(None)

        items = self.message_for(connection, 'project.refresh')['items']
        self.assertEqual(1, len(items))
        self.assertEqual('Item thing', items[0]['name'])


class ItemMonitorTest(WebTestCase):
    def setUp(self):
        super(ItemMonitorTest, self).setUp()

        self.task = PrintItem()
        self.pipeline = Pipeline(self.task)
        self.pipeline.project = Project(title='Example project')
        self.item = Item(self.pipeline, 'the-id', 3, keep_data=True,
                         prepare_data_directory=False)
        self.connection = make_connection()
        self.connection.on_open(None)
        self.connection.session.messages = []

    def messages_named(self, event_name):
        return [message['message']
                for message in self.connection.session.messages
                if message['event_name'] == event_name]

    def test_announces_the_item_when_created(self):
        ItemMonitor(self.item)

        started = self.messages_named('pipeline.start_item')
        self.assertEqual(1, len(started))
        self.assertEqual('the-id', started[0]['item']['id'])
        self.assertEqual(3, started[0]['item']['number'])
        self.assertEqual('Example project', started[0]['item']['project'])

    def test_describes_a_new_item_without_a_name(self):
        monitor = ItemMonitor(self.item)

        self.assertEqual('New item', monitor.item_for_broadcast()['name'])

    def test_lists_the_pipeline_tasks(self):
        monitor = ItemMonitor(self.item)

        tasks = monitor.item_for_broadcast()['tasks']

        self.assertEqual(1, len(tasks))
        self.assertEqual('PrintItem', tasks[0]['name'])
        self.assertEqual(None, tasks[0]['status'])

    def test_reports_a_task_status_once_it_is_set(self):
        monitor = ItemMonitor(self.item)
        self.item.set_task_status(self.task, Item.TaskStatus.running)

        self.assertEqual('running',
                         monitor.item_for_broadcast()['tasks'][0]['status'])

    def test_reports_no_project_when_the_pipeline_has_none(self):
        self.pipeline.project = None
        monitor = ItemMonitor(self.item)

        self.assertEqual(None, monitor.item_for_broadcast()['project'])

    def test_forwards_item_output(self):
        monitor = ItemMonitor(self.item)

        self.item.log_output('some output')

        messages = self.messages_named('item.output')
        self.assertEqual([{'item_id': 'the-id', 'data': 'some output\n',
                           'session_id': self.connection.session.session_id}],
                         messages)
        self.assertEqual('some output\n',
                         monitor.item_for_broadcast()['output'])

    def test_keeps_only_the_most_recent_output(self):
        monitor = ItemMonitor(self.item)

        for number in range(600):
            self.item.log_output('line %d' % number)

        self.assertEqual(500, len(monitor.collected_data))
        self.assertNotIn('line 0\n', monitor.item_for_broadcast()['output'])
        self.assertIn('line 599\n', monitor.item_for_broadcast()['output'])

    def test_forwards_a_task_status_change(self):
        ItemMonitor(self.item)

        self.item.set_task_status(self.task, Item.TaskStatus.running)

        messages = self.messages_named('item.task_status')
        self.assertEqual(1, len(messages))
        self.assertEqual('running', messages[0]['new_status'])
        self.assertEqual(None, messages[0]['old_status'])
        self.assertEqual(id(self.task), messages[0]['task_id'])

    def test_forwards_a_rename(self):
        ItemMonitor(self.item)

        self.item['item_name'] = 'thing'

        self.assertEqual([{'item_id': 'the-id', 'new_name': 'Item thing',
                           'session_id': self.connection.session.session_id}],
                         self.messages_named('item.update_name'))

    def test_ignores_other_property_changes(self):
        ItemMonitor(self.item)

        self.item['something_else'] = 'value'

        self.assertEqual([], self.messages_named('item.update_name'))

    def test_forwards_completion(self):
        monitor = ItemMonitor(self.item)

        self.item.complete()

        self.assertEqual(1, len(self.messages_named('item.complete')))
        self.assertEqual('completed', monitor.item_status())

    def test_forwards_failure(self):
        monitor = ItemMonitor(self.item)

        self.item.fail()

        self.assertEqual(1, len(self.messages_named('item.fail')))
        self.assertEqual('failed', monitor.item_status())

    def test_forwards_cancellation(self):
        monitor = ItemMonitor(self.item)

        self.item.cancel()

        self.assertEqual(1, len(self.messages_named('item.cancel')))
        self.assertEqual('canceled', monitor.item_status())

    def test_a_running_item_reports_running(self):
        monitor = ItemMonitor(self.item)

        self.assertEqual('running', monitor.item_status())
        self.assertEqual('running',
                         monitor.item_for_broadcast()['status'])


class WarriorServerTest(WebTestCase):
    '''Drives the warrior web interface over real HTTP.'''

    def setUp(self):
        super(WarriorServerTest, self).setUp()

        self.addCleanup(self.clear_pending_io_loop_stop)

        self.temp_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.temp_dir, True)
        projects_dir = os.path.join(self.temp_dir, 'projects')
        data_dir = os.path.join(self.temp_dir, 'data')
        os.makedirs(projects_dir)
        os.makedirs(data_dir)

        self.warrior = Warrior(projects_dir, data_dir,
                               'http://localhost:1/')
        self.addCleanup(logging.getLogger().removeHandler,
                        self.warrior.internal_log_handler)
        self.warrior.projects = {
            'someproject': {
                'name': 'someproject',
                'title': 'Some project',
                'description': 'A project for the tests',
                'repository': '/tmp/nowhere',
                'logo': '',
                'marker_html': 'hi',
                'lat_lng': [0.0, 0.0],
                'leaderboard': 'http://example.com/',
            },
        }

    def clear_pending_io_loop_stop(self):
        # forced_stop and friends stop the shared IO loop on purpose.
        io_loop = IOLoop.instance()
        io_loop._stopped = False
        io_loop._running = False
        io_loop._callbacks.clear()

    def start_server(self, **kwargs):
        sock, port = bind_unused_port()
        sock.close()

        server = start_warrior_server(self.warrior, bind_address='localhost',
                                      port_number=port, **kwargs)
        self.addCleanup(server.stop)
        self.base_url = 'http://localhost:%d' % port

        return server

    def fetch(self, path, **kwargs):
        result = {}
        client = AsyncHTTPClient()

        def handle_response(response):
            result['response'] = response
            IOLoop.instance().stop()

        client.fetch(HTTPRequest(self.base_url + path, **kwargs),
                     handle_response)

        deadline = time.time() + 10
        while 'response' not in result and time.time() < deadline:
            IOLoop.instance().start()

        self.assertIn('response', result)

        return result['response']

    def post(self, path, body=''):
        return self.fetch(path, method='POST', body=body)

    def test_wires_the_warrior_events_to_the_connection(self):
        self.start_server()

        self.assertEqual(self.warrior, SeesawConnection.warrior)
        self.assertTrue(len(self.warrior.on_status) > 0)
        self.assertTrue(len(self.warrior.on_projects_loaded) > 0)
        self.assertTrue(len(self.warrior.on_project_refresh) > 0)

    def test_serves_the_index_page(self):
        self.start_server()

        response = self.fetch('/')

        self.assertEqual(200, response.code)
        self.assertIn(b'<html', response.body.lower())

    def test_renders_the_help_page(self):
        self.start_server()

        response = self.fetch('/api/help')

        self.assertEqual(200, response.code)
        self.assertIn(seesaw.__version__.encode('ascii'), response.body)
        self.assertIn(b'Debug log', response.body)

    def test_the_help_page_shows_the_debug_log(self):
        self.start_server()
        logging.getLogger('seesaw.web_test').warning('a logged message')

        response = self.fetch('/api/help')

        self.assertIn(b'a logged message', response.body)

    def test_renders_the_settings_page(self):
        self.start_server()

        response = self.fetch('/api/settings')

        self.assertEqual(200, response.code)
        self.assertIn(b'Your nickname', response.body)
        self.assertIn(b'must be configured', response.body)

    def test_renders_the_project_list(self):
        self.start_server()

        response = self.fetch('/api/all-projects')

        self.assertEqual(200, response.code)
        self.assertIn(b'Some project', response.body)

    def test_posting_settings_stores_the_valid_ones(self):
        self.start_server()

        response = self.post('/api/settings', body='downloader=someone')

        self.assertEqual(200, response.code)
        self.assertEqual('someone', self.warrior.downloader.value)

    def test_posting_an_invalid_setting_reports_it_back(self):
        self.start_server()

        response = self.post('/api/settings', body='downloader=%21%21')

        self.assertEqual(200, response.code)
        self.assertEqual(None, self.warrior.downloader.value)
        self.assertIn(b'Invalid value for your nickname', response.body)

    def test_selecting_a_project_records_the_choice(self):
        self.start_server()

        response = self.post('/api/select-project',
                             body='project_name=someproject')

        self.assertEqual(200, response.code)
        self.assertEqual(b'OK', response.body)
        self.assertEqual('someproject',
                         self.warrior.selected_project_config_value.value)

    def test_deselecting_a_project_records_none(self):
        self.start_server()
        self.post('/api/select-project', body='project_name=someproject')

        response = self.post('/api/deselect-project', body='')

        self.assertEqual(200, response.code)
        self.assertEqual('none',
                         self.warrior.selected_project_config_value.value)

    def test_stop_asks_the_warrior_to_shut_down(self):
        self.start_server()

        response = self.post('/api/stop')

        self.assertEqual(b'OK', response.body)
        self.assertTrue(self.warrior.shut_down_flag)

    def test_keep_running_clears_the_shutdown_flag(self):
        self.start_server()
        self.post('/api/stop')

        response = self.post('/api/keep_running')

        self.assertEqual(b'OK', response.body)
        self.assertFalse(self.warrior.shut_down_flag)

    def test_stop_now_is_accepted(self):
        self.start_server()

        response = self.post('/api/stop_now')

        self.assertEqual(b'OK', response.body)

    def test_a_password_protects_the_interface(self):
        self.start_server(http_password='secret')

        self.assertEqual(401, self.fetch('/').code)

        raw = base64.b64encode(b':secret').decode('ascii')
        response = self.fetch('/', headers={'Authorization': 'Basic ' + raw})

        self.assertEqual(200, response.code)

    def test_the_warrior_settings_supply_the_password(self):
        self.warrior.config_manager.set_value('http_password', 'fromconfig')
        self.start_server()

        self.assertEqual(401, self.fetch('/').code)

        raw = base64.b64encode(b':fromconfig').decode('ascii')
        response = self.fetch('/', headers={'Authorization': 'Basic ' + raw})

        self.assertEqual(200, response.code)

    def test_a_connection_sees_the_warrior_state_on_open(self):
        self.start_server()
        connection = make_connection()

        connection.on_open(None)

        events = [message['event_name']
                  for message in connection.session.messages]
        self.assertIn('warrior.projects_loaded', events)
        self.assertIn('warrior.status', events)
        self.assertIn('warrior.broadcast_message', events)

    def test_broadcast_bandwidth_reaches_the_clients(self):
        self.start_server()
        connection = make_connection()
        connection.on_open(None)
        before = len(connection.session.messages)

        SeesawConnection.broadcast_bandwidth()

        # Whether there are bandwidth stats depends on the machine having the
        # interface the warrior watches; either way it must not raise.
        self.assertGreaterEqual(len(connection.session.messages), before)

    def test_broadcast_projects_lists_the_known_projects(self):
        self.start_server()
        connection = make_connection()
        connection.on_open(None)

        SeesawConnection.broadcast_projects()

        for message in reversed(connection.session.messages):
            if message['event_name'] == 'warrior.projects_loaded':
                self.assertIn('someproject', message['message']['projects'])
                return

        raise AssertionError('No projects were broadcast')

    def test_projects_loaded_is_forwarded_to_the_clients(self):
        self.start_server()
        connection = make_connection()
        connection.on_open(None)

        SeesawConnection.handle_projects_loaded(self.warrior,
                                                self.warrior.projects)

        events = [message['event_name']
                  for message in connection.session.messages]
        self.assertEqual('warrior.projects_loaded', events[-1])
