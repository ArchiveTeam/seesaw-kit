import base64
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

        auth_header = self.request.headers.get('Authorization')
        if auth_header and auth_header.startswith('Basic '):
            auth_decoded = base64.b64decode(auth_header[6:].encode('ascii')).decode('ascii')
            username, password = auth_decoded.split(':', 2)
            # request.basicauth_user, request.basicauth_pass = username, password
        else:
            username = ''
            password = ''

        if self.application.settings['check_auth'](self.request, username, password):
            return

        self.set_status(401)
        self.set_header('WWW-Authenticate', 'Basic realm=' + self.application.settings['auth_realm'])
        self.write('401 Authentication Required')
        self.finish()
