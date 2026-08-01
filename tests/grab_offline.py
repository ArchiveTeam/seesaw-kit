'''Offline sandbox for exercising grab pipelines.

Two pieces:

* :func:`block_network` makes every outbound socket raise, so a pipeline
  that tries to reach the network fails loudly instead of silently
  depending on someone else's server being up.
* :func:`stub_http` serves the handful of HTTP calls the pipelines make
  before wget is launched. The responses are synthesized, not recorded:
  the zstd dictionary handshake is satisfied with bytes generated here and
  a sha256 computed over them, so there is nothing to keep in sync with
  production.

Any URL that is not stubbed raises :class:`UnstubbedRequest`, which the
callers report as "needs a fixture" rather than as a pipeline bug.
'''

from __future__ import print_function

import hashlib
import socket


DICT_URL = 'https://seesaw-ci.invalid/dictionary-payload'

# Deliberately does not start with the zstd magic (28 B5 2F FD), so the
# pipelines skip their decompression branch and use these bytes as-is.
DICT_BYTES = b'seesaw-ci synthetic zstd dictionary payload'
DICT_SHA256 = hashlib.sha256(DICT_BYTES).hexdigest()
DICT_ID = 1


class NetworkBlocked(OSError):
    '''Raised when a pipeline attempts a real connection.

    Subclasses OSError so that libraries which catch OSError (dnspython,
    urllib, requests) treat it as an ordinary connection failure.
    '''


class UnstubbedRequest(Exception):
    '''Raised for an HTTP call this sandbox does not know how to answer.'''


def block_network():
    '''Cut off real network access for this process.

    Name resolution is faked rather than blocked: several pipelines call
    socket.gethostbyname() in their CheckIP task and require distinct
    answers for distinct hosts, and that logic is worth exercising.
    '''
    def fake_gethostbyname(host):
        digest = hashlib.sha1(host.encode('utf-8')).digest()
        return '203.0.113.%d' % (digest[0] % 254 + 1)

    def blocked(*args, **kwargs):
        raise NetworkBlocked(
            'Outbound network access is blocked in this test sandbox')

    socket.gethostbyname = fake_gethostbyname
    socket.gethostbyname_ex = lambda host: (host, [], [fake_gethostbyname(host)])
    socket.getaddrinfo = blocked
    socket.create_connection = blocked
    socket.socket.connect = blocked
    socket.socket.connect_ex = blocked


class FakeResponse(object):
    '''Minimal stand-in for requests.Response.'''

    def __init__(self, url, json_data=None, content=b''):
        self.url = url
        self.status_code = 200
        self.reason = 'OK'
        self.headers = {}
        self.content = content
        self._json = json_data

    @property
    def text(self):
        return self.content.decode('utf-8', 'replace')

    def json(self):
        if self._json is None:
            raise ValueError('Stubbed response for %s has no JSON body'
                             % self.url)
        return self._json

    def raise_for_status(self):
        return None


_extra_routes = {}


def _dispatch(url, params=None):
    path = url.split('?', 1)[0]

    for fragment, response in _extra_routes.items():
        if fragment in url:
            return FakeResponse(url,
                                json_data=response.get('json'),
                                content=response.get('text', '').encode('utf-8'))

    # The zstd dictionary handshake, shared verbatim by 13 projects. The
    # host differs between projects, so match on the path.
    if path.endswith('/dictionary'):
        return FakeResponse(url, json_data={
            'id': DICT_ID,
            'url': DICT_URL,
            'sha256': DICT_SHA256,
        })

    if path == DICT_URL:
        return FakeResponse(url, content=DICT_BYTES)

    # A plain epoch timestamp; github-grab reads it as item['start_time'].
    if path.endswith('/now'):
        return FakeResponse(url, content=b'1750000000.000000')

    # recursive-grab asks for the config of the job it is working on and
    # indexes the reply by the requested property name. An empty config is
    # valid and exercises the same code path.
    if path.endswith('/props') and params and 'prop' in params:
        return FakeResponse(url, json_data={params['prop']: '{}'})

    raise UnstubbedRequest(url)


def add_routes(routes):
    '''Register per-project responses from a fixture file.

    Each key is matched as a substring of the request URL; each value is
    {"json": ...} or {"text": "..."}.
    '''
    _extra_routes.update(routes or {})


def stub_http():
    '''Replace requests' entry points with the synthetic dispatcher.'''
    import requests

    def fake_get(url, *args, **kwargs):
        return _dispatch(url, kwargs.get('params'))

    def fake_post(url, *args, **kwargs):
        return _dispatch(url, kwargs.get('params'))

    def fake_request(method, url, *args, **kwargs):
        return _dispatch(url, kwargs.get('params'))

    requests.get = fake_get
    requests.post = fake_post
    requests.request = fake_request
    requests.Session.get = \
        lambda self, url, *a, **kw: _dispatch(url, kw.get('params'))
    requests.Session.post = \
        lambda self, url, *a, **kw: _dispatch(url, kw.get('params'))
    requests.Session.request = \
        lambda self, method, url, *a, **kw: _dispatch(url, kw.get('params'))
