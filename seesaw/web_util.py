import base64
import binascii
import re

import tornado
import tornado.web


try:
    callable
except NameError:
    from seesaw.six import callable


class BaseWebAdminHandler(tornado.web.RequestHandler):
    def prepare(self):
        if not self.application.settings['auth_enabled']:
            return

        for pattern in self.application.settings['skip_auth']:
            if pattern.match(self.request.uri):
                return

        username = ''
        password = ''

        auth_header = self.request.headers.get('Authorization')
        if auth_header and auth_header.startswith('Basic '):
            try:
                auth_decoded = base64.b64decode(
                    auth_header[6:].encode('ascii')).decode('utf-8')
                # RFC 7617: only the first colon separates the two fields.
                username, password = auth_decoded.split(':', 1)
            except (ValueError, UnicodeError, binascii.Error):
                # Unusable credentials are a failed login, not a 500.
                username = ''
                password = ''

        if self.application.settings['check_auth'](self.request, username, password):
            return

        self.set_status(401)
        self.set_header('WWW-Authenticate', 'Basic realm=' + self.application.settings['auth_realm'])
        self.write('401 Authentication Required')
        self.finish()
