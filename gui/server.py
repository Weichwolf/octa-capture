# SPDX-License-Identifier: GPL-2.0-only
"""Loopback-only authenticated HTTP transport for the local control panel."""

from __future__ import annotations

import argparse
import hmac
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import signal
import socket
import subprocess
import sys
import threading
from urllib.parse import urlsplit
import webbrowser

from .application import Application
from .backend import DeviceError, InvalidControl


ASSETS = {'/': ('index.html', 'text/html; charset=utf-8'),
          '/index.html': ('index.html', 'text/html; charset=utf-8'),
          '/app.js': ('app.js', 'text/javascript; charset=utf-8'),
          '/parameters.js': ('parameters.js', 'text/javascript; charset=utf-8'),
          '/meters.js': ('meters.js', 'text/javascript; charset=utf-8'),
          '/menus.js': ('menus.js', 'text/javascript; charset=utf-8'),
          '/warnings.js': ('warnings.js', 'text/javascript; charset=utf-8'),
          '/graphs.js': ('graphs.js', 'text/javascript; charset=utf-8'),
          '/signal-flow.svg': ('signal-flow.svg', 'image/svg+xml'),
          '/favicon.svg': ('favicon.svg', 'image/svg+xml'),
          '/style.css': ('style.css', 'text/css; charset=utf-8')}
CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
       "connect-src 'self'; font-src 'self'; base-uri 'none'; frame-ancestors 'none'; "
       "form-action 'none'; object-src 'none'")


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON field.')
        result[key] = value
    return result


def invalid_constant(_value):
    raise ValueError('Non-finite JSON number.')


class LocalServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 16

    def __init__(self, application, port=8765, token=None, asset_dir=None):
        self.application = application
        self.token = token or secrets.token_urlsafe(32)
        self.asset_dir = Path(asset_dir or Path(__file__).with_name('web')).resolve()
        self._slots = threading.BoundedSemaphore(16)
        super().__init__(('127.0.0.1', port), Handler)
        self.host = f'127.0.0.1:{self.server_port}'
        self.origin = f'http://{self.host}'

    def process_request(self, request, client_address):
        if not self._slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._slots.release()

    def get_request(self):
        connection, address = super().get_request()
        connection.settimeout(5)
        return connection, address

    def service_actions(self):
        self.application.expire_idle()


class Handler(BaseHTTPRequestHandler):
    server_version = 'OCTA-CAPTURE'
    sys_version = ''
    protocol_version = 'HTTP/1.0'

    def log_message(self, _format, *_arguments):
        # URLs and Authorization values must never enter request logs.
        pass

    def reply(self, status, data, content_type='application/json; charset=utf-8', headers=None, head=False):
        if not isinstance(data, bytes):
            data = json.dumps(data, allow_nan=False, separators=(',', ':')).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Security-Policy', CSP)
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('X-Frame-Options', 'DENY')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Permissions-Policy', 'camera=(), microphone=(), geolocation=()')
        self.send_header('Connection', 'close')
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.close_connection = True
        if not head:
            self.wfile.write(data)

    def failure(self, status, message):
        self.reply(status, {'error': message})

    def guard(self, authenticated=False, mutation=False):
        if sum(len(name) + len(value) for name, value in self.headers.items()) > 8192:
            self.failure(431, 'Request headers are too large.')
            return False
        if self.headers.get_all('Host') != [self.server.host]:
            self.failure(403, 'Invalid request host.')
            return False
        origins = self.headers.get_all('Origin', [])
        if (origins and origins != [self.server.origin]) or (mutation and not origins):
            self.failure(403, 'Invalid request origin.')
            return False
        if authenticated:
            authorization = self.headers.get_all('Authorization', [])
            expected = ('Bearer ' + self.server.token).encode('ascii')
            if (len(authorization) != 1 or
                    not hmac.compare_digest(authorization[0].encode('latin-1'), expected)):
                self.failure(403, 'Open the control panel using its private launch link.')
                return False
        return True

    def request_path(self):
        target = self.requestline.split()[1]
        parsed = urlsplit(target)
        if (not target.startswith('/') or target.startswith('//') or parsed.netloc or
                parsed.scheme or parsed.query or parsed.fragment or len(target) > 2048):
            raise InvalidControl('Invalid request path.')
        return parsed.path

    def do_GET(self):
        self.read_request()

    def do_HEAD(self):
        self.read_request(head=True)

    def read_request(self, head=False):
        try:
            path = self.request_path()
            if not self.guard(authenticated=path.startswith('/api/')):
                return
            if path in ASSETS:
                filename, content_type = ASSETS[path]
                self.reply(200, (self.server.asset_dir / filename).read_bytes(), content_type, head=head)
                return
            if head:
                self.failure(405, 'This API requires GET.')
                return
            application = self.server.application
            if path == '/api/state':
                self.reply(200, application.snapshot())
            elif path == '/api/meters':
                self.reply(200, application.meters())
            elif path == '/api/devices':
                self.reply(200, application.devices())
            elif path == '/api/preferences':
                self.reply(200, application.preferences())
            elif path == '/api/preset':
                self.reply(200, application.export_preset(), 'application/octet-stream',
                           {'Content-Disposition': 'attachment; filename="OCTA-CAPTURE.octa"'})
            else:
                self.failure(404, 'Not found.')
        except (InvalidControl, ValueError):
            self.failure(400, 'Invalid request.')
        except DeviceError as error:
            self.failure(503, str(error))
        except (FileNotFoundError, IsADirectoryError):
            self.failure(404, 'Not found.')
        except (BrokenPipeError, ConnectionResetError, socket.timeout):
            pass

    def body(self, expected_type, maximum=4096):
        if self.headers.get_all('Transfer-Encoding'):
            raise InvalidControl('Transfer encoding is not supported.')
        lengths = self.headers.get_all('Content-Length', [])
        if (len(lengths) != 1 or not lengths[0] or
                any(value not in '0123456789' for value in lengths[0])):
            raise InvalidControl('A bounded request body is required.')
        length = int(lengths[0])
        if length > maximum:
            self.failure(413, 'Request body is too large.')
            return None
        if self.headers.get_all('Content-Type') != [expected_type]:
            self.failure(415, 'Unsupported content type.')
            return None
        data = self.rfile.read(length)
        if len(data) != length:
            raise InvalidControl('Incomplete request body.')
        return data

    def do_POST(self):
        try:
            path = self.request_path()
            if not self.guard(authenticated=True, mutation=True):
                return
            application = self.server.application
            if path == '/api/preset':
                data = self.body('application/octet-stream')
                if data is not None:
                    self.reply(200, application.import_preset(data))
                return
            if path == '/api/disconnect':
                data = self.body('application/json')
                if data is not None:
                    if data not in (b'{}', b''):
                        raise InvalidControl('Invalid disconnect request.')
                    self.reply(200, application.disconnect())
                return
            operations = {'/api/control': application.change, '/api/action': application.action,
                          '/api/device': application.select_device,
                          '/api/preferences': application.set_preferences,
                          '/api/auto-sense/start': application.start_auto_sense,
                          '/api/auto-sense/stop': application.stop_auto_sense}
            if path not in operations:
                self.failure(404, 'Not found.')
                return
            data = self.body('application/json')
            if data is None:
                return
            body = json.loads(data.decode('utf-8'), object_pairs_hook=unique_object,
                              parse_constant=invalid_constant)
            result = operations[path](body)
            self.reply(200, result)
        except (InvalidControl, ValueError, UnicodeError, RecursionError):
            self.failure(400, 'Invalid control request.')
        except DeviceError as error:
            self.failure(503, str(error))
        except subprocess.SubprocessError as error:
            print(str(error), file=sys.stderr)
            self.failure(503, 'The device operation did not finish.')
        except (BrokenPipeError, ConnectionResetError, socket.timeout):
            pass
        except OSError as error:
            print(str(error), file=sys.stderr)
            self.failure(503, 'The local device backend is not available.')

    def do_OPTIONS(self):
        self.failure(405, 'Cross-origin access is not supported.')


def main(*, default_bin_dir=None):
    root = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bin-dir', type=Path, default=default_bin_dir or root / 'build/release')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--midi-port', default='hw:CARD=OCTACAPTURE,DEV=0,SUBDEV=1')
    parser.add_argument('--data-dir', type=Path,
                        default=Path(os.environ.get('XDG_STATE_HOME', Path.home() / '.local/state')) / 'octa-capture')
    parser.add_argument('--open', action='store_true', help='open the private link in the default browser')
    args = parser.parse_args()
    if not 0 <= args.port <= 65535:
        parser.error('Port must be 0..65535.')
    application = Application(args.bin_dir, args.data_dir, args.midi_port)
    server = LocalServer(application, args.port)
    url = f'{server.origin}/#token={server.token}'
    print(f'OCTA-CAPTURE Control Panel: {url}', flush=True)
    print(f'Recovery files: {application.data_dir}', flush=True)
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, lambda _signal, _frame: threading.Thread(target=server.shutdown, daemon=True).start())
    if args.open:
        webbrowser.open(url)
    try:
        server.serve_forever(poll_interval=0.1)
    finally:
        application.close()
        server.server_close()


if __name__ == '__main__':
    main()
