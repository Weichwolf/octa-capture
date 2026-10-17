# SPDX-License-Identifier: GPL-2.0-only
"""Exercise loopback HTTP authentication, origin policy and input boundaries offline."""

import http.client
from contextlib import closing
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from gui.application import Application
from gui.backend import InvalidControl, validate_control
from gui.server import LocalServer


class TestApplication:
    def __init__(self):
        self.calls = []

    def snapshot(self):
        self.calls.append('state')
        return {'connected': True, 'state': {'test': True}}

    def change(self, body):
        command = validate_control(body)
        self.calls.append(command)
        return {'ok': True}

    def action(self, body):
        self.calls.append(('action', body))
        return {'ok': True}

    def meters(self):
        return {'meters': None}

    def export_preset(self):
        return bytes(1639)

    def import_preset(self, data):
        if len(data) != 1639:
            raise InvalidControl('Wrong preset size.')
        self.calls.append('import')
        return {'ok': True}

    def disconnect(self):
        self.calls.append('disconnect')
        return {'connected': False}

    def expire_idle(self):
        pass


class Http(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = TestApplication()
        cls.server = LocalServer(cls.application, port=0, token='private-test-token')
        cls.thread = threading.Thread(target=cls.server.serve_forever, kwargs={'poll_interval': 0.02})
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=3)

    def setUp(self):
        self.application.calls.clear()

    def request(self, method, path, body=None, headers=None, authorized=True):
        values = {'Host': self.server.host}
        if authorized:
            values['Authorization'] = 'Bearer private-test-token'
        if method == 'POST':
            values['Origin'] = self.server.origin
            values['Content-Type'] = 'application/json'
        values.update(headers or {})
        with closing(http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=3)) as client:
            client.request(method, path, body=body, headers=values)
            response = client.getresponse()
            return response.status, dict(response.headers), response.read()

    def test_static_policy(self):
        status, headers, body = self.request('GET', '/', authorized=False)
        self.assertEqual(status, 200)
        self.assertIn(b'OCTA-CAPTURE Control Panel', body)
        self.assertEqual(headers['X-Content-Type-Options'], 'nosniff')
        self.assertIn("frame-ancestors 'none'", headers['Content-Security-Policy'])
        self.assertNotIn('Access-Control-Allow-Origin', headers)
        self.assertEqual(headers['Cache-Control'], 'no-store')
        self.assertEqual(self.application.calls, [])
        self.assertEqual(self.request('HEAD', '/')[2], b'')

    def test_auth_host_origin(self):
        self.assertEqual(self.request('GET', '/api/state', authorized=False)[0], 403)
        self.assertEqual(self.request('GET', '/api/state', headers={'Authorization': 'Bearer wrong'})[0], 403)
        self.assertEqual(self.request('GET', '/api/state', headers={'Authorization': 'Bearer \xff'})[0], 403)
        self.assertEqual(self.request('GET', '/', headers={'Host': 'evil.example'})[0], 403)
        self.assertEqual(self.request('GET', '/api/state', headers={'Origin': 'http://evil.example'})[0], 403)
        self.assertEqual(self.request('POST', '/api/control', b'{}', headers={'Origin': 'null'})[0], 403)
        self.assertEqual(self.application.calls, [])

    def test_body_and_json_limits(self):
        examples = [(b'{}', {'Content-Type': 'text/plain'}, 415),
                    (b' ' * 4097, {}, 413), (b'{}', {'Transfer-Encoding': 'chunked'}, 400),
                    (b'\xff', {}, 400), (b'{"value":NaN}', {}, 400),
                    (b'{"kind":"preamp","kind":"route"}', {}, 400),
                    (b'[' * 1100 + b']' * 1100, {}, 400)]
        for body, headers, expected in examples:
            with self.subTest(body=body[:40]):
                self.assertEqual(self.request('POST', '/api/control', body, headers)[0], expected)
        self.assertEqual(self.application.calls, [])

    def test_typed_control_and_readback(self):
        body = {'kind': 'preamp', 'channel': 0, 'parameter': 'attack', 'value': 25}
        self.assertEqual(self.request('POST', '/api/control', json.dumps(body))[0], 200)
        self.assertEqual(self.application.calls, [('preamp', [0, 8, 25])])
        self.application.calls.clear()
        body['value'] = True
        self.assertEqual(self.request('POST', '/api/control', json.dumps(body))[0], 400)
        self.assertEqual(self.application.calls, [])
        self.assertEqual(self.request('GET', '/api/state')[0], 200)
        self.assertEqual(self.application.calls, ['state'])

    def test_routes_are_fixed(self):
        for path in ('/../README.md', '/%2e%2e/README.md', '/gui/backend.py', '/package.json'):
            self.assertEqual(self.request('GET', path)[0], 404)
        for path in ('http://evil.example/api/state', '//evil.example/api/state', '/api/state?token=x'):
            self.assertEqual(self.request('GET', path)[0], 400)
        self.assertEqual(self.request('OPTIONS', '/api/control')[0], 405)
        self.assertEqual(self.application.calls, [])

    def test_duplicate_headers_and_missing_origin(self):
        for duplicate in ('Host', 'Authorization', 'Content-Length'):
            with closing(http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=3)) as client:
                client.putrequest('POST', '/api/control', skip_host=True)
                for name, value in [('Host', self.server.host), ('Authorization', 'Bearer private-test-token'),
                                    ('Origin', self.server.origin), ('Content-Type', 'application/json'),
                                    ('Content-Length', '2')]:
                    client.putheader(name, value)
                    if name == duplicate:
                        client.putheader(name, value)
                client.endheaders(b'{}')
                response = client.getresponse()
                self.assertEqual(response.status, 400 if duplicate == 'Content-Length' else 403)
                response.read()
        with closing(http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=3)) as client:
            client.request('POST', '/api/control', b'{}',
                           {'Authorization': 'Bearer private-test-token', 'Content-Type': 'application/json'})
            response = client.getresponse()
            self.assertEqual(response.status, 403)
            response.read()
        self.assertEqual(self.application.calls, [])

    def test_presets_and_disconnect(self):
        status, headers, body = self.request('GET', '/api/preset')
        self.assertEqual((status, len(body)), (200, 1639))
        self.assertIn('.octa', headers['Content-Disposition'])
        self.assertEqual(self.request('POST', '/api/preset', bytes(1639),
                                      {'Content-Type': 'application/octet-stream'})[0], 200)
        self.assertEqual(self.request('POST', '/api/disconnect', b'{}')[0], 200)
        self.assertEqual(self.application.calls, ['import', 'disconnect'])


class Operations(unittest.TestCase):
    def test_short_queries_do_not_lock_the_panel(self):
        application = Application('/missing/bin', '/unused')
        application.bridge = Mock()
        application.bridge.meters.return_value = None
        application._remember({'test': True})
        entered, release = threading.Event(), threading.Event()

        def operation():
            with application._operation:
                entered.set()
                release.wait(timeout=3)
        thread = threading.Thread(target=operation)
        thread.start()
        try:
            self.assertTrue(entered.wait(timeout=1))
            self.assertFalse(application.snapshot()['busy'])
            application._set_transaction(True)
            self.assertTrue(application.snapshot()['busy'])
            application.bridge.request.assert_not_called()
        finally:
            release.set()
            thread.join(timeout=3)

    def test_invalid_operation_never_starts_device_or_creates_recovery(self):
        with tempfile.TemporaryDirectory() as directory:
            application = Application('/missing/bin', Path(directory) / 'recovery')
            application.bridge = Mock()
            for body in ({'action': 'shell', 'command': 'id'}, {'action': 'initialize', 'target': '../all'},
                         {'action': 'routing', 'preset': None}, {'action': 'memory-store', 'extra': True},
                         {'action': 'sample-rate', 'rate': True}, {'action': 'sample-rate', 'rate': 50000}):
                with self.subTest(body=body), self.assertRaises(InvalidControl):
                    application.action(body)
            application.bridge.close.assert_not_called()
            self.assertFalse(application.data_dir.exists())


if __name__ == '__main__':
    unittest.main()
