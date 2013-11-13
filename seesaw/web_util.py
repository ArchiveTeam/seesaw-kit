import base64
import functools
import re

import tornado


class AuthenticationErrorHandler(tornado.web.RequestHandler):
    def initialize(self, realm="Restricted"):
        self.realm = realm

    def prepare(self):
        self.set_status(401)
        self.set_header('WWW-Authenticate', 'Basic realm=' + self.realm)
        self.finish()


class AuthenticatedApplication(tornado.web.Application):
    def __init__(self, *args, **kwargs):
        super(AuthenticatedApplication, self).__init__(*args, **kwargs)
        self.auth_enabled = kwargs.get("auth_enabled", True)
        self.auth_realm = kwargs.get("auth_realm", "Restricted")
        self.check_auth = kwargs.get("check_auth")
        self.skip_auth = [re.compile(pattern) for pattern in kwargs.get("skip_auth", [])]

    def __call__(self, request):
        if not self.auth_enabled or (callable(self.auth_enabled) and not self.auth_enabled()):
            return super(AuthenticatedApplication, self).__call__(request)

        for pattern in self.skip_auth:
            if pattern.match(request.uri):
                return super(AuthenticatedApplication, self).__call__(request)

        auth_header = request.headers.get('Authorization')
        if auth_header and auth_header.startswith('Basic '):
            auth_decoded = base64.decodestring(auth_header[6:])
            username, password = auth_decoded.split(':', 2)
            request.basicauth_user, request.basicauth_pass = username, password

            if self.check_auth and self.check_auth(request, username, password):
                return super(AuthenticatedApplication, self).__call__(request)

        handler = AuthenticationErrorHandler(self, request, realm=self.auth_realm)
        handler._execute([])
        return handler
