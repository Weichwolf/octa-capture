# SPDX-License-Identifier: GPL-2.0-only
"""Check installed launchers/assets from a foreign directory without device I/O."""

import http.client
import json
from pathlib import Path
import select
import subprocess
import sys
import tempfile
import unittest
from urllib.parse import parse_qs, urlsplit


class Installation(unittest.TestCase):
    def test_staged_launchers_assets_and_private_preferences(self):
        cmake, build = sys.argv[1:3]
        with tempfile.TemporaryDirectory(prefix='octa install ') as directory:
            root = Path(directory)
            prefix = root / 'prefix with spaces'
            foreign = root / 'foreign working directory'
            foreign.mkdir()
            subprocess.run([cmake, '--install', build, '--prefix', str(prefix)], check=True,
                           capture_output=True, timeout=10)
            binary = prefix / 'bin'
            for name in ('octa-capture', 'octa-capture-http'):
                result = subprocess.run([str(binary / name), '--help'], cwd=foreign,
                                        check=True, capture_output=True, text=True, timeout=5)
                self.assertIn('--bin-dir', result.stdout)
            for name in ('octa-bridge', 'octa-preset', 'octa-status', 'octa-audio-check',
                         'octa-auto-sense', 'octa-panel', 'octa-state', 'octa-dsp', 'octa-control'):
                self.assertTrue((binary / name).is_file(), name)
            data = root / 'private state'
            with subprocess.Popen([str(binary / 'octa-capture-http'), '--port', '0', '--data-dir', str(data)],
                                  cwd=foreign, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) as server:
                try:
                    self.assertTrue(select.select([server.stdout], [], [], 5)[0], 'Installed server startup')
                    line = server.stdout.readline()
                    url = urlsplit(line.split(' ', 3)[-1].strip())
                    self.assertEqual(url.hostname, '127.0.0.1')
                    token = parse_qs(url.fragment)['token'][0]
                    def request(path, body=None):
                        headers = {'Authorization': f'Bearer {token}'}
                        method = 'GET'
                        if body is not None:
                            method = 'POST'
                            headers.update({'Origin': f'http://127.0.0.1:{url.port}',
                                            'Content-Type': 'application/json'})
                        client = http.client.HTTPConnection('127.0.0.1', url.port, timeout=3)
                        try:
                            client.request(method, path, body=body, headers=headers)
                            response = client.getresponse()
                            return response.status, response.read()
                        finally:
                            client.close()
                    for asset in ('/', '/app.js', '/graphs.js', '/warnings.js', '/menus.js', '/style.css', '/favicon.svg', '/signal-flow.svg'):
                        self.assertEqual(request(asset)[0], 200, asset)
                    self.assertEqual(request('/api/preferences')[0], 200)
                    status, body = request('/api/preferences', '{"show_port_warning":false}')
                    self.assertEqual(status, 200)
                    self.assertFalse(json.loads(body)['show_port_warning'])
                    self.assertEqual(json.loads((data / 'preferences.json').read_text()), {'show_port_warning': False})
                    self.assertEqual(request('/gui/application.py')[0], 404)
                    # No /api/state, MIDI or PCM request is made by this offline check.
                finally:
                    server.terminate()
                    try:
                        server.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        server.kill()
                        server.wait(timeout=3)
                self.assertEqual(server.returncode, 0)


if __name__ == '__main__':
    unittest.main(argv=[sys.argv[0]])
