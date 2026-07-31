# encoding=utf8
'''Tracker tests.

These drive the tracker tasks against a real HTTP server standing in for the
Universal Tracker, so the request building, response handling and retry
scheduling are all exercised for real.
'''
from __future__ import unicode_literals

import datetime
import json
import os
import os.path
import shutil
import tempfile
import unittest

from tornado.httpserver import HTTPServer
from tornado.ioloop import IOLoop
from tornado.testing import bind_unused_port
from tornado.web import Application, RequestHandler

import seesaw
from seesaw.item import Item, ItemInterpolation, ItemValue
from seesaw.pipeline import Pipeline
from seesaw.runner import SimpleRunner
from seesaw.task import SetItemKey
from tests.test_base import BaseTestCase
from seesaw.tracker import GetItemFromTracker, PrepareStatsForTracker, \
    ReleaseItemsToTracker, SendDoneToTracker, TrackerRequest, \
    UploadWithTracker


class TrackerState(object):
    '''What the fake tracker should do next.

    ``responses`` is consumed one entry per request to a command; once it
    runs out the last entry is reused.
    '''
    def __init__(self):
        self.responses = {}
        self.requests = []

    def set(self, command, responses):
        self.responses[command] = list(responses)

    def next_response(self, command):
        responses = self.responses.get(command)

        if not responses:
            return (200, '{}')

        if len(responses) > 1:
            return responses.pop(0)

        return responses[0]


class CommandHandler(RequestHandler):
    def initialize(self, state):
        self.state = state

    def post(self, command):
        # A tracker URL may carry a "/multi=N" segment; the command is
        # always the final path element.
        command = command.split('/')[-1]

        body = self.request.body.decode('utf-8')
        self.state.requests.append({
            'command': command,
            'body': json.loads(body) if body else None,
            'user_agent': self.request.headers.get('User-Agent'),
            'content_type': self.request.headers.get('Content-Type'),
        })

        code, response_body = self.state.next_response(command)

        # The tracker uses codes outside the IANA registry (420, 455), which
        # tornado will not set without an explicit reason phrase.
        self.set_status(code, reason='Tracker response')
        self.finish(response_body)


class UploadTargetHandler(RequestHandler):
    '''Accepts a curl ``--upload-file`` PUT and keeps what it received.'''
    def initialize(self, state):
        self.state = state

    def put(self, filename):
        self.state.uploads.append((filename, self.request.body))
        self.finish('stored')


class TrackerTestCase(BaseTestCase):
    def setUp(self):
        super(TrackerTestCase, self).setUp()

        # Retries are scheduled on the IO loop. The real backoff starts at a
        # minute and grows; shrink it so the tests exercise the retry paths
        # without waiting them out. A 200 response resets the delay to
        # DEFAULT_RETRY_DELAY, so the instance attribute alone is not enough.
        self._original_delays = (TrackerRequest.DEFAULT_RETRY_DELAY,
                                 TrackerRequest.RETRY_DELAY_INCREMENT,
                                 TrackerRequest.MAX_RETRY_DELAY)
        TrackerRequest.DEFAULT_RETRY_DELAY = 0.01
        TrackerRequest.RETRY_DELAY_INCREMENT = 0.01
        TrackerRequest.MAX_RETRY_DELAY = 0.05
        self.addCleanup(self.restore_retry_delays)

        self.state = TrackerState()
        self.state.uploads = []

        sock, self.port = bind_unused_port()
        application = Application([
            (r'/upload-target/(.*)$', UploadTargetHandler,
             {'state': self.state}),
            (r'/(.+)$', CommandHandler, {'state': self.state}),
        ])
        self.server = HTTPServer(application)
        self.server.add_sockets([sock])
        self.addCleanup(self.server.stop)

        self.tracker_url = 'http://localhost:%d' % self.port

    def restore_retry_delays(self):
        (TrackerRequest.DEFAULT_RETRY_DELAY,
         TrackerRequest.RETRY_DELAY_INCREMENT,
         TrackerRequest.MAX_RETRY_DELAY) = self._original_delays

    def run_task(self, task, extra_tasks=(), max_items=1):
        '''Run a task in a pipeline and report what happened to the item.'''
        pipeline = Pipeline(*(tuple(extra_tasks) + (task,)))
        pipeline.completed_items = []
        pipeline.failed_items = []
        pipeline.output = []
        pipeline.on_complete_item += \
            lambda p, item: pipeline.completed_items.append(item)
        pipeline.on_fail_item += \
            lambda p, item: pipeline.failed_items.append(item)

        runner = SimpleRunner(pipeline, max_items=max_items)
        runner.on_create_item += \
            lambda r, item: item.on_output.handle(
                lambda i, data: pipeline.output.append(data))
        runner.start()

        return pipeline

    def assertCompleted(self, pipeline):
        self.assertEqual([], pipeline.failed_items)
        self.assertEqual(1, len(pipeline.completed_items))

        return pipeline.completed_items[0]


class TrackerRequestUnitTest(BaseTestCase):
    '''Covers the parts of TrackerRequest that need no tracker.'''

    def make_task(self):
        return TrackerRequest('Whatever', 'http://example.com', 'request')

    def test_strips_a_trailing_slash_from_the_url(self):
        task = TrackerRequest('Whatever', 'http://example.com/tracker/',
                              'request')

        self.assertEqual('http://example.com/tracker', task.tracker_url)

    def test_data_is_empty_by_default(self):
        self.assertEqual({}, self.make_task().data(None))

    def test_process_body_must_be_implemented(self):
        self.assertRaises(NotImplementedError,
                          self.make_task().process_body, '', None)

    def test_starts_at_the_default_retry_delay(self):
        self.assertEqual(TrackerRequest.DEFAULT_RETRY_DELAY,
                         self.make_task().retry_delay)

    def test_retry_delay_grows_by_the_increment(self):
        task = self.make_task()

        task.increment_retry_delay()

        self.assertEqual(
            TrackerRequest.DEFAULT_RETRY_DELAY +
            TrackerRequest.RETRY_DELAY_INCREMENT,
            task.retry_delay)

    def test_retry_delay_is_capped(self):
        task = self.make_task()

        for dummy in range(100):
            task.increment_retry_delay()

        self.assertEqual(TrackerRequest.MAX_RETRY_DELAY, task.retry_delay)

    def test_retry_delay_respects_a_custom_maximum(self):
        task = self.make_task()

        for dummy in range(100):
            task.increment_retry_delay(max_delay=90)

        self.assertEqual(90, task.retry_delay)

    def test_reset_retry_delay_restores_the_default(self):
        task = self.make_task()
        task.retry_delay = 250

        task.reset_retry_delay()

        self.assertEqual(TrackerRequest.DEFAULT_RETRY_DELAY, task.retry_delay)

    def test_a_text_response_body_is_passed_through_undecoded(self):
        class RecordingRequest(TrackerRequest):
            bodies = []

            def process_body(self, body, item):
                self.bodies.append(body)

        class Response(object):
            code = 200
            body = 'already text'

        task = RecordingRequest('Whatever', 'http://example.com', 'request')

        task.handle_response(None, Response())

        self.assertEqual(['already text'], RecordingRequest.bodies)


class TrackerRequestTest(TrackerTestCase):
    def test_a_canceled_item_sends_no_request(self):
        task = SendDoneToTracker(self.tracker_url, {})
        item = Item(Pipeline(task), 'the-id', 1, keep_data=True,
                    prepare_data_directory=False)
        item.cancel()

        task.send_request(item)

        self.assertEqual([], self.state.requests)

    def test_sends_the_seesaw_user_agent_and_content_type(self):
        self.state.set('done', [(200, 'OK')])

        self.run_task(SendDoneToTracker(self.tracker_url, {'a': 1}),
                      extra_tasks=[SetItemKey('item_name', 'thing')])

        request = self.state.requests[0]
        self.assertIn(seesaw.__version__, request['user_agent'])
        self.assertIn('ArchiveTeam Warrior', request['user_agent'])
        self.assertEqual('application/json', request['content_type'])

    def test_posts_the_task_data_as_json(self):
        self.state.set('done', [(200, 'OK')])

        self.run_task(SendDoneToTracker(self.tracker_url, {'a': 1, 'b': 'c'}),
                      extra_tasks=[SetItemKey('item_name', 'thing')])

        self.assertEqual({'a': 1, 'b': 'c'}, self.state.requests[0]['body'])

    def test_retries_until_the_tracker_answers(self):
        self.state.set('done', [(500, 'nope'), (500, 'nope'), (200, 'OK')])

        pipeline = self.run_task(
            SendDoneToTracker(self.tracker_url, {}),
            extra_tasks=[SetItemKey('item_name', 'thing')])

        self.assertCompleted(pipeline)
        self.assertEqual(3, len(self.state.requests))

    def test_reports_a_helpful_message_for_each_status_code(self):
        cases = [
            (404, 'There aren\'t any items available'),
            (420, 'Tracker rate limiting is active'),
            (429, 'Tracker rate limiting is active'),
            (455, 'Project code is out of date'),
            (500, 'Tracker returned status code 500'),
        ]

        for code, expected in cases:
            self.state.set('done', [(code, 'nope'), (200, 'OK')])

            pipeline = self.run_task(
                SendDoneToTracker(self.tracker_url, {}),
                extra_tasks=[SetItemKey('item_name', 'thing')])

            self.assertCompleted(pipeline)
            self.assertIn(expected, ''.join(pipeline.output))

    def test_reports_an_unreachable_tracker(self):
        sock, dead_port = bind_unused_port()
        sock.close()

        task = SendDoneToTracker('http://localhost:%d' % dead_port, {})
        pipeline = Pipeline(SetItemKey('item_name', 'thing'), task)
        pipeline.output = []
        runner = SimpleRunner(pipeline, max_items=1)
        runner.on_create_item += \
            lambda r, item: item.on_output.handle(
                lambda i, data: pipeline.output.append(data))

        # The connection never succeeds, so end the run after a few attempts.
        IOLoop.instance().add_timeout(datetime.timedelta(seconds=0.3),
                                      IOLoop.instance().stop)
        runner.start()

        self.assertIn('Retrying after', ''.join(pipeline.output))

    def test_a_successful_response_resets_the_grown_retry_delay(self):
        self.state.set('done', [(500, 'nope'), (500, 'nope'), (200, 'OK')])
        task = SendDoneToTracker(self.tracker_url, {})

        self.run_task(task, extra_tasks=[SetItemKey('item_name', 'thing')])

        self.assertEqual(TrackerRequest.DEFAULT_RETRY_DELAY, task.retry_delay)


class GetItemFromTrackerTest(TrackerTestCase):
    def test_puts_the_tracker_response_onto_the_item(self):
        self.state.set('request', [(200, json.dumps({
            'item_name': 'thing', 'items': ['thing'], 'queues': ['q'],
        }))])

        pipeline = self.run_task(
            GetItemFromTracker(self.tracker_url, 'someone', '1'))

        item = self.assertCompleted(pipeline)
        self.assertEqual('thing', item['item_name'])
        self.assertEqual(['thing'], item['items'])

    def test_sends_the_downloader_and_version(self):
        self.state.set('request', [(200, json.dumps({'item_name': 'thing'}))])

        self.run_task(GetItemFromTracker(self.tracker_url, 'someone', '1'))

        self.assertEqual({'downloader': 'someone', 'version': '1'},
                         self.state.requests[0]['body'])

    def test_realizes_the_downloader_and_version(self):
        self.state.set('request', [(200, json.dumps({'item_name': 'thing'}))])

        self.run_task(
            GetItemFromTracker(self.tracker_url, ItemValue('downloader'),
                               ItemInterpolation('v%(version)s')),
            extra_tasks=[SetItemKey('downloader', 'someone'),
                         SetItemKey('version', '2')])

        self.assertEqual({'downloader': 'someone', 'version': 'v2'},
                         self.state.requests[0]['body'])

    def test_retries_an_empty_response(self):
        self.state.set('request', [
            (200, json.dumps({})),
            (200, json.dumps({'item_name': 'thing'})),
        ])

        pipeline = self.run_task(
            GetItemFromTracker(self.tracker_url, 'someone', '1'))

        self.assertCompleted(pipeline)
        self.assertIn('Tracker responded with empty response',
                      ''.join(pipeline.output))

    def test_marks_the_item_cancellable_while_waiting(self):
        self.state.set('request', [
            (404, 'no items'),
            (200, json.dumps({'item_name': 'thing'})),
        ])
        task = GetItemFromTracker(self.tracker_url, 'someone', '1')
        seen = []
        task.on_complete_item += \
            lambda t, item: seen.append(item.may_be_canceled)

        pipeline = self.run_task(task)

        self.assertCompleted(pipeline)
        # The flag is raised while retrying and lowered before each attempt.
        self.assertEqual([False], seen)

    def test_an_item_filter_that_accepts_everything(self):
        self.state.set('request', [(200, json.dumps({
            'item_name': 'a\x00b', 'items': ['a', 'b'], 'queues': ['q1', 'q2'],
        }))])

        pipeline = self.run_task(GetItemFromTracker(
            self.tracker_url, 'someone', '1',
            item_filter=lambda items: [True] * len(items)))

        item = self.assertCompleted(pipeline)
        self.assertEqual(['a', 'b'], item['items'])
        self.assertEqual('a\x00b', item['item_name'])
        self.assertEqual(['request'],
                         [r['command'] for r in self.state.requests])

    def test_an_item_filter_sees_the_items_and_queues(self):
        self.state.set('request', [(200, json.dumps({
            'item_name': 'a\x00b', 'items': ['a', 'b'], 'queues': ['q1', 'q2'],
        }))])
        seen = []

        def item_filter(items):
            seen.append(items)
            return [True] * len(items)

        self.run_task(GetItemFromTracker(self.tracker_url, 'someone', '1',
                                         item_filter=item_filter))

        self.assertEqual([[{'item': 'a', 'queue': 'q1'},
                           {'item': 'b', 'queue': 'q2'}]], seen)

    def test_rejected_items_are_released_and_the_rest_accepted(self):
        self.state.set('request', [(200, json.dumps({
            'item_name': 'a\x00b', 'items': ['a', 'b'], 'queues': ['q1', 'q2'],
        }))])
        self.state.set('release', [(200, json.dumps({'items': ['b']}))])

        pipeline = self.run_task(GetItemFromTracker(
            self.tracker_url, 'someone', '1',
            item_filter=lambda items: [item['item'] == 'a'
                                       for item in items]))

        item = self.assertCompleted(pipeline)
        self.assertEqual(['a'], item['items'])
        self.assertEqual(['q1'], item['queues'])
        self.assertEqual('a', item['item_name'])

        release_request = [r for r in self.state.requests
                           if r['command'] == 'release'][0]
        self.assertEqual({'downloader': 'someone', 'version': '1',
                          'items': ['b']},
                         release_request['body'])
        self.assertIn('Releasing items: b.', ''.join(pipeline.output))

    def test_rejecting_everything_asks_the_tracker_again(self):
        self.state.set('request', [
            (200, json.dumps({'item_name': 'a', 'items': ['a'],
                              'queues': ['q1']})),
            (200, json.dumps({'item_name': 'b', 'items': ['b'],
                              'queues': ['q2']})),
        ])
        self.state.set('release', [(200, json.dumps({'items': ['a']}))])

        accepted = [False, True]

        pipeline = self.run_task(GetItemFromTracker(
            self.tracker_url, 'someone', '1',
            item_filter=lambda items: [accepted.pop(0)]))

        item = self.assertCompleted(pipeline)
        self.assertEqual('b', item['item_name'])
        self.assertEqual(2, len([r for r in self.state.requests
                                 if r['command'] == 'request']))

    def test_an_item_filter_returning_the_wrong_length_fails_the_item(self):
        self.state.set('request', [(200, json.dumps({
            'item_name': 'a\x00b', 'items': ['a', 'b'], 'queues': ['q1', 'q2'],
        }))])

        pipeline = self.run_task(GetItemFromTracker(
            self.tracker_url, 'someone', '1',
            item_filter=lambda items: [True]))

        self.assertEqual(1, len(pipeline.failed_items))
        self.assertIn('item_filter returned invalid data',
                      ''.join(pipeline.output))

    def test_a_failed_release_fails_the_item(self):
        self.state.set('request', [(200, json.dumps({
            'item_name': 'a\x00b', 'items': ['a', 'b'], 'queues': ['q1', 'q2'],
        }))])
        # ReleaseItemsToTracker retries a bad body rather than failing, so
        # make the inner task raise instead.
        self.state.set('release', [(200, 'not json at all')])

        pipeline = self.run_task(GetItemFromTracker(
            self.tracker_url, 'someone', '1',
            item_filter=lambda items: [item['item'] == 'a'
                                       for item in items]))

        self.assertEqual(1, len(pipeline.failed_items))

    def test_the_release_url_drops_the_multi_suffix(self):
        self.state.set('request', [(200, json.dumps({
            'item_name': 'a\x00b', 'items': ['a', 'b'], 'queues': ['q1', 'q2'],
        }))])
        self.state.set('release', [(200, json.dumps({'items': ['b']}))])

        # A multi-item tracker URL ends in "/multi=N", but "release" lives on
        # the project root rather than under that suffix.
        pipeline = self.run_task(GetItemFromTracker(
            self.tracker_url + '/multi=3', 'someone', '1',
            item_filter=lambda items: [item['item'] == 'a'
                                       for item in items]))

        item = self.assertCompleted(pipeline)
        self.assertEqual('a', item['item_name'])

        release = [r for r in self.state.requests
                   if r['command'] == 'release']
        self.assertEqual(1, len(release))
        self.assertEqual(['b'], release[0]['body']['items'])


class ReleaseItemsToTrackerTest(TrackerTestCase):
    def test_completes_on_a_valid_response(self):
        self.state.set('release', [(200, json.dumps({'items': ['a']}))])

        pipeline = self.run_task(ReleaseItemsToTracker(
            self.tracker_url, 'someone', '1', ['a']))

        self.assertCompleted(pipeline)
        self.assertEqual({'downloader': 'someone', 'version': '1',
                          'items': ['a']},
                         self.state.requests[0]['body'])

    def test_retries_an_unexpected_response(self):
        self.state.set('release', [
            (200, json.dumps({'items': ['a'], 'extra': 1})),
            (200, json.dumps({'items': ['a']})),
        ])

        pipeline = self.run_task(ReleaseItemsToTracker(
            self.tracker_url, 'someone', '1', ['a']))

        self.assertCompleted(pipeline)
        self.assertIn('Tracker responded with invalid release response',
                      ''.join(pipeline.output))

    def test_retries_a_response_without_an_items_list(self):
        self.state.set('release', [
            (200, json.dumps({'items': 'not a list'})),
            (200, json.dumps({'items': []})),
        ])

        pipeline = self.run_task(ReleaseItemsToTracker(
            self.tracker_url, 'someone', '1', ['a']))

        self.assertCompleted(pipeline)


class SendDoneToTrackerTest(TrackerTestCase):
    def test_completes_when_the_tracker_says_ok(self):
        self.state.set('done', [(200, 'OK\n')])

        pipeline = self.run_task(
            SendDoneToTracker(self.tracker_url, {'item': 'thing'}),
            extra_tasks=[SetItemKey('item_name', 'thing')])

        self.assertCompleted(pipeline)
        self.assertIn("Tracker confirmed item 'thing'",
                      ''.join(pipeline.output))

    def test_retries_an_unexpected_body(self):
        self.state.set('done', [(200, 'NOPE'), (200, 'OK')])

        pipeline = self.run_task(
            SendDoneToTracker(self.tracker_url, {}),
            extra_tasks=[SetItemKey('item_name', 'thing')])

        self.assertCompleted(pipeline)
        self.assertIn("Tracker responded with unexpected 'NOPE'",
                      ''.join(pipeline.output))

    def test_realizes_the_stats(self):
        self.state.set('done', [(200, 'OK')])

        self.run_task(
            SendDoneToTracker(self.tracker_url,
                              {'item': ItemValue('item_name')}),
            extra_tasks=[SetItemKey('item_name', 'thing')])

        self.assertEqual({'item': 'thing'}, self.state.requests[0]['body'])


class PrepareStatsForTrackerTest(BaseTestCase):
    def setUp(self):
        super(PrepareStatsForTrackerTest, self).setUp()
        self.temp_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.temp_dir, True)

    def write_file(self, name, contents):
        path = os.path.join(self.temp_dir, name)
        with open(path, 'wb') as file_obj:
            file_obj.write(contents)
        return path

    def run_task(self, task):
        pipeline = Pipeline(SetItemKey('item_name', 'thing'), task)
        pipeline.items = []
        pipeline.on_complete_item += \
            lambda p, item: pipeline.items.append(item)
        SimpleRunner(pipeline, max_items=1).start()

        self.assertEqual(1, len(pipeline.items))
        return pipeline.items[0]

    def test_reports_the_item_name_and_no_bytes_by_default(self):
        item = self.run_task(PrepareStatsForTracker())

        self.assertEqual({'item': 'thing', 'bytes': {}}, item['stats'])

    def test_sums_the_size_of_each_file_group(self):
        first = self.write_file('a.txt', b'12345')
        second = self.write_file('b.txt', b'123')
        third = self.write_file('c.warc', b'1234567')

        item = self.run_task(PrepareStatsForTracker(
            file_groups={'data': [first, second], 'warc': [third]}))

        self.assertEqual({'data': 8, 'warc': 7}, item['stats']['bytes'])

    def test_includes_the_defaults(self):
        item = self.run_task(PrepareStatsForTracker(
            defaults={'downloader': 'someone', 'version': '1'}))

        self.assertEqual('someone', item['stats']['downloader'])
        self.assertEqual('1', item['stats']['version'])

    def test_the_item_name_wins_over_a_default(self):
        item = self.run_task(PrepareStatsForTracker(
            defaults={'item': 'ignored'}))

        self.assertEqual('thing', item['stats']['item'])

    def test_uses_the_id_function(self):
        item = self.run_task(PrepareStatsForTracker(
            id_function=lambda item: 'id-for-%s' % item['item_name']))

        self.assertEqual('id-for-thing', item['stats']['id'])

    def test_realizes_the_file_paths_and_defaults(self):
        path = self.write_file('thing.txt', b'1234')

        item = self.run_task(PrepareStatsForTracker(
            defaults={'downloader': ItemValue('item_name')},
            file_groups={'data': [ItemInterpolation(
                os.path.join(self.temp_dir, '%(item_name)s.txt'))]}))

        self.assertEqual({'data': 4}, item['stats']['bytes'])
        self.assertEqual('thing', item['stats']['downloader'])
        self.assertTrue(os.path.exists(path))


class CapturingUploadWithTracker(UploadWithTracker):
    '''Records the inner upload task instead of running it.

    The choice of uploader is what matters here; whether rsync or curl is
    installed on the machine running the tests is not.
    '''
    def __init__(self, *args, **kwargs):
        UploadWithTracker.__init__(self, *args, **kwargs)
        self.inner_tasks = []

    def _enqueue_inner_task_with_except(self, inner_task, item):
        self.inner_tasks.append(inner_task)
        self.complete_item(item)


class UploadWithTrackerTest(TrackerTestCase):
    def test_sends_the_downloader_and_item_name(self):
        self.state.set('upload', [(200, json.dumps({
            'upload_target': 'rsync://example.com/target/'}))])

        self.run_task(
            CapturingUploadWithTracker(self.tracker_url, 'someone',
                                       ['a.txt']),
            extra_tasks=[SetItemKey('item_name', 'thing')])

        self.assertEqual({'downloader': 'someone', 'item_name': 'thing'},
                         self.state.requests[0]['body'])

    def test_includes_the_version_when_given(self):
        self.state.set('upload', [(200, json.dumps({
            'upload_target': 'rsync://example.com/target/'}))])

        self.run_task(
            CapturingUploadWithTracker(self.tracker_url, 'someone',
                                       ['a.txt'], version='1'),
            extra_tasks=[SetItemKey('item_name', 'thing')])

        self.assertEqual('1', self.state.requests[0]['body']['version'])

    def test_chooses_rsync_for_an_rsync_target(self):
        self.state.set('upload', [(200, json.dumps({
            'upload_target': 'rsync://example.com/target/'}))])
        task = CapturingUploadWithTracker(
            self.tracker_url, 'someone', ['/data/a.txt'],
            rsync_target_source_path='/data/', rsync_bwlimit='512',
            rsync_extra_args=['--partial'])

        pipeline = self.run_task(
            task, extra_tasks=[SetItemKey('item_name', 'thing')])

        self.assertCompleted(pipeline)
        self.assertEqual(1, len(task.inner_tasks))
        inner = task.inner_tasks[0]
        self.assertEqual('RsyncUpload', inner.name)
        self.assertEqual('rsync://example.com/target/', inner.args[-1])
        self.assertIn('512', inner.args)
        self.assertIn('--partial', inner.args)
        self.assertIn('Uploading with Rsync', ''.join(pipeline.output))

    def test_chooses_curl_for_an_http_target(self):
        self.state.set('upload', [(200, json.dumps({
            'upload_target': 'http://example.com/target/'}))])
        task = CapturingUploadWithTracker(
            self.tracker_url, 'someone', ['/data/a.txt'],
            curl_connect_timeout='30', curl_speed_limit='2',
            curl_speed_time='60')

        pipeline = self.run_task(
            task, extra_tasks=[SetItemKey('item_name', 'thing')])

        self.assertCompleted(pipeline)
        inner = task.inner_tasks[0]
        self.assertEqual('CurlUpload', inner.name)
        self.assertEqual('http://example.com/target/', inner.args[-1])
        self.assertIn('/data/a.txt', inner.args)
        self.assertIn('X-Curl-Limits: inf,2,60', inner.args)
        self.assertIn('Uploading with Curl', ''.join(pipeline.output))

    def test_curl_refuses_more_than_one_file(self):
        self.state.set('upload', [
            (200, json.dumps({'upload_target': 'http://example.com/target/'})),
            (200, json.dumps({'upload_target': 'rsync://example.com/x/'})),
        ])
        task = CapturingUploadWithTracker(self.tracker_url, 'someone',
                                          ['a.txt', 'b.txt'])

        pipeline = self.run_task(
            task, extra_tasks=[SetItemKey('item_name', 'thing')])

        self.assertCompleted(pipeline)
        self.assertIn('Curl expects to upload a single file',
                      ''.join(pipeline.output))
        self.assertEqual('RsyncUpload', task.inner_tasks[0].name)

    def test_retries_an_unrecognised_upload_target(self):
        self.state.set('upload', [
            (200, json.dumps({'upload_target': 'ftp://example.com/target/'})),
            (200, json.dumps({'upload_target': 'rsync://example.com/x/'})),
        ])
        task = CapturingUploadWithTracker(self.tracker_url, 'someone',
                                          ['a.txt'])

        pipeline = self.run_task(
            task, extra_tasks=[SetItemKey('item_name', 'thing')])

        self.assertCompleted(pipeline)
        self.assertIn('Received invalid upload URI ftp://example.com/target/',
                      ''.join(pipeline.output))

    def test_retries_when_no_upload_target_is_offered(self):
        self.state.set('upload', [
            (200, json.dumps({})),
            (200, json.dumps({'upload_target': 'rsync://example.com/x/'})),
        ])
        task = CapturingUploadWithTracker(self.tracker_url, 'someone',
                                          ['a.txt'])

        pipeline = self.run_task(
            task, extra_tasks=[SetItemKey('item_name', 'thing')])

        self.assertCompleted(pipeline)
        self.assertIn('Tracker did not provide an upload target',
                      ''.join(pipeline.output))

    @unittest.skipUnless(shutil.which('curl'), 'curl is not installed')
    def test_uploads_a_file_end_to_end_with_curl(self):
        temp_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, temp_dir, True)
        path = os.path.join(temp_dir, 'payload.txt')
        with open(path, 'wb') as file_obj:
            file_obj.write(b'the uploaded bytes')

        self.state.set('upload', [(200, json.dumps({
            'upload_target': '%s/upload-target/' % self.tracker_url}))])

        pipeline = self.run_task(
            UploadWithTracker(self.tracker_url, 'someone', [path]),
            extra_tasks=[SetItemKey('item_name', 'thing')])

        self.assertCompleted(pipeline)
        self.assertEqual([('payload.txt', b'the uploaded bytes')],
                         self.state.uploads)

    def test_a_failing_inner_task_is_retried(self):
        self.state.set('upload', [(200, json.dumps({
            'upload_target': 'rsync://example.com/target/'}))])

        class FailingOnceUpload(UploadWithTracker):
            attempts = 0

            def _enqueue_inner_task_with_except(self, inner_task, item):
                FailingOnceUpload.attempts += 1
                if FailingOnceUpload.attempts == 1:
                    self._inner_task_fail_item(inner_task, item)
                else:
                    self._inner_task_complete_item(inner_task, item)

        task = FailingOnceUpload(self.tracker_url, 'someone', ['a.txt'])

        pipeline = self.run_task(
            task, extra_tasks=[SetItemKey('item_name', 'thing')])

        self.assertCompleted(pipeline)
        self.assertEqual(2, FailingOnceUpload.attempts)
