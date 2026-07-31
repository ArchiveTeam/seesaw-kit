# encoding=utf8
'''Tests for the shared web admin handler.'''

import base64
import re
import time

from tornado.httpclient import AsyncHTTPClient, HTTPRequest
from tornado.httpserver import HTTPServer
from tornado.ioloop import IOLoop
from tornado.testing import bind_unused_port
from tornado.web import Application

from tests.test_base import BaseTestCase
from seesaw.web_util import BaseWebAdminHandler


class ProtectedHandler(BaseWebAdminHandler):
    def get(self):
        self.write('the protected page')


class BaseWebAdminHandlerTest(BaseTestCase):
    def start_server(self, **settings):
        sock, port = bind_unused_port()

        defaults = {
            'auth_enabled': True,
            'check_auth': lambda request, username, password:
                (username, password) == ('someone', 'secret'),
            'auth_realm': 'The Realm',
            'skip_auth': [],
        }
        defaults.update(settings)

        application = Application([
            (r'/open/.*$', ProtectedHandler),
            (r'/.*$', ProtectedHandler),
        ], **defaults)
        server = HTTPServer(application)
        server.add_sockets([sock])
        self.addCleanup(server.stop)

        self.base_url = 'http://localhost:%d' % port

    def fetch(self, path, headers=None):
        result = {}
        client = AsyncHTTPClient()

        def handle_response(response):
            result['response'] = response
            IOLoop.instance().stop()

        client.fetch(HTTPRequest(self.base_url + path, headers=headers),
                     handle_response)

        deadline = time.time() + 10
        while 'response' not in result and time.time() < deadline:
            IOLoop.instance().start()

        return result['response']

    def basic_auth(self, username, password):
        raw = ('%s:%s' % (username, password)).encode('ascii')
        return {'Authorization':
                'Basic ' + base64.b64encode(raw).decode('ascii')}

    def test_authentication_can_be_turned_off(self):
        self.start_server(auth_enabled=False)

        self.assertEqual(200, self.fetch('/anything').code)

    def test_a_request_without_credentials_is_challenged(self):
        self.start_server()

        response = self.fetch('/anything')

        self.assertEqual(401, response.code)
        self.assertEqual('Basic realm=The Realm',
                         response.headers.get('WWW-Authenticate'))

    def test_correct_credentials_are_let_through(self):
        self.start_server()

        response = self.fetch('/anything',
                              headers=self.basic_auth('someone', 'secret'))

        self.assertEqual(200, response.code)
        self.assertEqual(b'the protected page', response.body)

    def test_wrong_credentials_are_challenged(self):
        self.start_server()

        response = self.fetch('/anything',
                              headers=self.basic_auth('someone', 'wrong'))

        self.assertEqual(401, response.code)

    def test_a_non_basic_authorization_header_is_treated_as_empty(self):
        self.start_server()

        response = self.fetch('/anything',
                              headers={'Authorization': 'Bearer atoken'})

        self.assertEqual(401, response.code)

    def test_a_skipped_path_needs_no_credentials(self):
        self.start_server(skip_auth=[re.compile(r'^/open/')])

        self.assertEqual(200, self.fetch('/open/page').code)
        self.assertEqual(401, self.fetch('/closed/page').code)

    def test_the_first_matching_skip_pattern_wins(self):
        self.start_server(skip_auth=[re.compile(r'^/nope/'),
                                     re.compile(r'^/open/')])

        self.assertEqual(200, self.fetch('/open/page').code)
