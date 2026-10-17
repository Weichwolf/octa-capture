# SPDX-License-Identifier: GPL-2.0-only
"""Check desktop navigation and settings download origin boundaries offline."""

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from gui.navigation import local_document, local_download


class Navigation(unittest.TestCase):
    origin = 'http://127.0.0.1:8765'

    def test_local_document(self):
        for path in ('/', '/index.html', '/#token=private', '/index.html#section'):
            self.assertTrue(local_document(self.origin + path, self.origin))

    def test_external_or_non_document_navigation_is_denied(self):
        for uri in ('https://example.com/', 'http://127.0.0.1:8766/', 'http://localhost:8765/',
                    'http://127.0.0.1:8765@evil.test/', 'http://user@127.0.0.1:8765/',
                    'file:///etc/passwd', 'data:text/html,hello', 'javascript:alert(1)',
                    self.origin + '/api/state', self.origin + '/?next=evil',
                    self.origin + '/index.html/other', '\n' + self.origin + '/',
                    'http://[invalid/', None, 1):
            with self.subTest(uri=uri):
                self.assertFalse(local_document(uri, self.origin))

    def test_downloads_only_from_our_blob_origin(self):
        self.assertTrue(local_download('blob:' + self.origin + '/unique-id', self.origin))
        for uri in ('blob:https://evil.test/id', 'blob:' + self.origin + '.evil.test/id',
                    'blob:http://127.0.0.1:8766/id', 'blob:file:///etc/passwd',
                    self.origin + '/api/preset', 'file:///tmp/settings.octa',
                    'blob:' + self.origin + '/id\n', None, 1):
            with self.subTest(uri=uri):
                self.assertFalse(local_download(uri, self.origin))


if __name__ == '__main__':
    unittest.main()
