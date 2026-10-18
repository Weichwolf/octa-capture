# SPDX-License-Identifier: GPL-2.0-only
"""Preference persistence and failure boundaries without any audio/device access."""

import json
from pathlib import Path
import stat
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from gui.application import Application
from gui.backend import InvalidControl
from gui.preferences import Preferences


class Storage(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / 'settings'

    def test_private_atomic_persistence_and_independent_documents(self):
        preferences = Preferences(self.root)
        self.assertEqual(preferences.document(), {'show_port_warning': True})
        self.assertFalse(self.root.exists())
        self.assertEqual(preferences.update({'show_port_warning': False}), {'show_port_warning': False})
        self.assertEqual(stat.S_IMODE(self.root.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(preferences.path.stat().st_mode), 0o600)
        self.assertEqual(list(self.root.iterdir()), [preferences.path])
        self.assertEqual(Preferences(self.root).document(), {'show_port_warning': False})
        preferences.document()['show_port_warning'] = True
        self.assertFalse(preferences.document()['show_port_warning'])

    def test_invalid_shapes_do_not_create_files(self):
        preferences = Preferences(self.root)
        for value in (None, [], {}, {'show_port_warning': 1}, {'show_port_warning': 'false'},
                      {'show_port_warning': False, 'path': '/tmp/other'}):
            with self.subTest(value=value), self.assertRaises(InvalidControl):
                preferences.update(value)
        self.assertFalse(self.root.exists())

    def test_corrupt_duplicate_and_oversized_files_fall_back_to_original_default(self):
        self.root.mkdir()
        for value in ('not json', '[]', '{"show_port_warning":false,"show_port_warning":true}',
                      '{"show_port_warning":0}', '{"unknown":false}', ' ' * 257, '\udcff'):
            with self.subTest(value=value):
                (self.root / 'preferences.json').write_bytes(value.encode('utf-8', errors='surrogateescape'))
                self.assertEqual(Preferences(self.root).document(), {'show_port_warning': True})

    def test_failed_replace_preserves_prior_file_and_memory(self):
        preferences = Preferences(self.root)
        preferences.update({'show_port_warning': False})
        original = preferences.path.read_bytes()
        with patch('gui.preferences.os.replace', side_effect=OSError('Injected disk failure')):
            with self.assertRaises(OSError):
                preferences.update({'show_port_warning': True})
        self.assertEqual(preferences.path.read_bytes(), original)
        self.assertEqual(preferences.document(), {'show_port_warning': False})
        self.assertEqual(list(self.root.iterdir()), [preferences.path])

    def test_concurrent_updates_always_leave_a_complete_document(self):
        preferences = Preferences(self.root)
        failures = []
        def change(value):
            try:
                for _ in range(10):
                    preferences.update({'show_port_warning': value})
                    self.assertIs(type(json.loads(preferences.path.read_text())['show_port_warning']), bool)
            except Exception as error:
                failures.append(error)
        threads = [threading.Thread(target=change, args=(value,)) for value in (False, True)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=3)
            self.assertFalse(thread.is_alive())
        self.assertEqual(failures, [])
        self.assertEqual(json.loads(preferences.path.read_text()), preferences.document())

    def test_application_preferences_remain_usable_without_an_audio_device(self):
        application = Application('/missing/bin', self.root)
        application.bridge = Mock()
        application.bridge.auto_sense_activity.return_value = None
        self.assertTrue(application.preferences()['show_port_warning'])
        self.assertFalse(application.set_preferences({'show_port_warning': False})['show_port_warning'])
        application.bridge.request.assert_not_called()
        application.bridge.close.assert_not_called()


if __name__ == '__main__':
    unittest.main()
