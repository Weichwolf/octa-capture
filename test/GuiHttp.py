# SPDX-License-Identifier: GPL-2.0-only
"""Exercise loopback HTTP authentication, origin policy and input boundaries offline."""

import http.client
from contextlib import closing
import json
from pathlib import Path
import sys
import socket
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from gui.application import Application
from gui.backend import DeviceError, InvalidControl, validate_control
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

    def start_auto_sense(self, body):
        self.calls.append(('auto-start', body))
        return {'ok': True}

    def stop_auto_sense(self, body):
        self.calls.append(('auto-stop', body))
        return {'ok': True}

    def meters(self):
        return {'meters': None}

    def devices(self):
        return {'devices': [{'id': '1-1', 'name': 'OCTA-CAPTURE', 'selected': True}]}

    def select_device(self, body):
        self.calls.append(('device', body))
        return {'connected': True}

    def preferences(self):
        return {'show_port_warning': True}

    def set_preferences(self, body):
        self.calls.append(('preferences', body))
        return body

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
        status, headers, body = self.request('GET', '/signal-flow.svg', authorized=False)
        self.assertEqual(status, 200)
        self.assertEqual(headers['Content-Type'], 'image/svg+xml')
        self.assertIn(b'OCTA-CAPTURE signal flow', body)
        for asset, mime in [('faders.js', 'text/javascript; charset=utf-8'),
                            ('fader-white.svg', 'image/svg+xml'), ('fader-red.svg', 'image/svg+xml')]:
            with self.subTest(asset=asset):
                status, headers, body = self.request('GET', '/' + asset, authorized=False)
                self.assertEqual(status, 200)
                self.assertEqual(headers['Content-Type'], mime)
                self.assertIn(b'GPL-2.0-only', body)

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

    def test_device_selection_is_authenticated_and_origin_checked(self):
        self.assertEqual(self.request('GET', '/api/devices', authorized=False)[0], 403)
        status, _, body = self.request('GET', '/api/devices')
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)['devices'][0]['id'], '1-1')
        self.assertEqual(self.request('POST', '/api/device', '{"id":"1-1"}',
                                      headers={'Origin': 'http://evil.example'})[0], 403)
        self.assertEqual(self.application.calls, [])
        self.assertEqual(self.request('POST', '/api/device', '{"id":"1-1"}')[0], 200)
        self.assertEqual(self.application.calls, [('device', {'id': '1-1'})])

    def test_preferences_require_authentication_and_the_local_origin(self):
        self.assertEqual(self.request('GET', '/api/preferences', authorized=False)[0], 403)
        status, _, body = self.request('GET', '/api/preferences')
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(body)['show_port_warning'])
        value = '{"show_port_warning":false}'
        self.assertEqual(self.request('POST', '/api/preferences', value,
                                      headers={'Origin': 'http://evil.example'})[0], 403)
        self.assertEqual(self.application.calls, [])
        self.assertEqual(self.request('POST', '/api/preferences', value)[0], 200)
        self.assertEqual(self.application.calls, [('preferences', {'show_port_warning': False})])

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

    def test_slow_headers_and_bodies_cannot_renew_the_input_deadline(self):
        with patch.object(self.server, 'request_input_timeout', 0.25):
            for prefix in (b'GET /api/state HTTP/1.0\r\nX-Slow: ',
                           (f'POST /api/preferences HTTP/1.0\r\nHost: {self.server.host}\r\n'
                            f'Authorization: Bearer private-test-token\r\nOrigin: {self.server.origin}\r\n'
                            'Content-Type: application/json\r\nContent-Length: 100\r\n\r\n').encode()):
                with self.subTest(prefix=prefix[:25]), socket.create_connection(self.server.server_address, timeout=2) as client:
                    started = time.monotonic()
                    client.sendall(prefix)
                    for _ in range(8):
                        time.sleep(0.06)
                        try:
                            client.sendall(b' ')
                        except (BrokenPipeError, ConnectionResetError):
                            break
                    try:
                        self.assertEqual(client.recv(1), b'')
                    except ConnectionResetError:
                        pass
                    self.assertLess(time.monotonic() - started, 1.5)
                self.assertEqual(self.application.calls, [])
            self.assertEqual(self.request('GET', '/api/preferences')[0], 200)

    def test_oversized_unfinished_headers_are_bounded_before_dispatch(self):
        with socket.create_connection(self.server.server_address, timeout=2) as client:
            try:
                client.sendall(b'GET /api/state HTTP/1.0\r\nX-Oversized: ' + b'x' * 60000)
                self.assertEqual(client.recv(1), b'')
            except (BrokenPipeError, ConnectionResetError):
                pass
        self.assertEqual(self.application.calls, [])
        self.assertEqual(self.request('GET', '/api/preferences')[0], 200)

    def test_complete_input_does_not_time_out_a_slower_backend_operation(self):
        def operation(body):
            time.sleep(0.2)
            return body
        with patch.object(self.server, 'request_input_timeout', 0.1), \
                patch.object(self.application, 'set_preferences', side_effect=operation):
            self.assertEqual(self.request('POST', '/api/preferences', '{"show_port_warning":false}')[0], 200)


class Operations(unittest.TestCase):
    def test_observed_activity_is_separate_from_owned_calibration(self):
        application = Application('/missing/bin', '/unused')
        application.bridge = Mock()
        application.bridge.meters.return_value = None
        application._remember({'test': True})
        for activity in (None, {'channels': 1, 'active': True, 'observed_age_ms': 0},
                         {'channels': 0, 'active': False, 'observed_age_ms': 1000}):
            application.bridge.auto_sense_activity.return_value = activity
            document = json.loads(json.dumps(application._document()))
            self.assertEqual(document['auto_sense_activity'], activity)
            self.assertIsNone(application._auto_job)
            if activity:
                self.assertEqual(document['auto_sense']['origin'], 'device')
                self.assertEqual(document['auto_sense']['active'], activity['active'])
            self.assertEqual(document['busy'], bool(activity and activity['active']))
            if activity and activity['active']:
                with self.assertRaises(DeviceError):
                    application.change({'kind': 'preamp', 'channel': 0, 'parameter': 'sensitivity', 'value': 20})
                with self.assertRaises(DeviceError):
                    application.start_auto_sense({'channels': 1})
        application.bridge.request.assert_not_called()

    def test_short_queries_do_not_lock_the_panel(self):
        application = Application('/missing/bin', '/unused')
        application.bridge = Mock()
        application.bridge.auto_sense_activity.return_value = None
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
            application.bridge.auto_sense_activity.return_value = None
            for body in ({'action': 'shell', 'command': 'id'}, {'action': 'initialize', 'target': '../all'},
                         {'action': 'routing', 'preset': None}, {'action': 'memory-store', 'extra': True},
                         {'action': 'sample-rate', 'rate': True}, {'action': 'sample-rate', 'rate': 50000}):
                with self.subTest(body=body), self.assertRaises(InvalidControl):
                    application.action(body)
            application.bridge.close.assert_not_called()
            self.assertFalse(application.data_dir.exists())


if __name__ == '__main__':
    unittest.main()
