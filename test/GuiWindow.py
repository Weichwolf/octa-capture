# SPDX-License-Identifier: GPL-2.0-only
"""Window bridge boundaries, deferred WM confirmation and private persistence."""

from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from gui.preferences import Preferences, WindowPreferences
from gui.window_controls import WindowControls, window_request
from gui.window_state import WindowState


class Window(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name)
        self.glib = Mock(Error=RuntimeError)
        self.controls = WindowControls(Mock(), self.path, self.glib)
        self.controls.native = Mock()
        self.finished = Mock()

    def test_only_typed_state_or_boolean_set_requests(self):
        self.assertIsNone(window_request({'action': 'state'}))
        self.assertIs(window_request({'action': 'set', 'value': False}), False)
        self.assertIs(window_request({'action': 'set', 'value': True}), True)
        for value in (None, [], {}, 'state', {'action': 'set'}, {'action': 'set', 'value': 1},
                      {'action': 'set', 'value': 'true'}, {'action': 'state', 'xid': 1},
                      {'action': 'set', 'value': True, 'path': '/tmp/other'},
                      {'action': 'exec', 'value': 'xdotool'}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                window_request(value)

    def test_external_document_cannot_query_or_change_our_window(self):
        for uri in ('https://example.com/', 'file:///etc/passwd', 'http://127.0.0.1:1235/'):
            view, value, reply = Mock(), Mock(), Mock()
            view.get_uri.return_value = uri
            self.controls.message(view, 'http://127.0.0.1:1234', value, reply)
            reply.return_error_message.assert_called_once()
            value.to_json.assert_not_called()
        self.controls.native.request.assert_not_called()
        self.controls.native.state.assert_not_called()

    def test_preference_changes_only_after_window_manager_confirmation(self):
        self.controls.change(True, self.finished)
        poll = self.glib.timeout_add.call_args.args[1]
        self.controls.native.state.return_value = {'supported': True, 'above': False}
        self.assertTrue(poll())
        self.assertFalse((self.path / 'window.json').exists())
        with self.assertRaises(ValueError):
            self.controls.change(False, self.finished)
        self.controls.native.state.return_value = {'supported': True, 'above': True}
        self.assertFalse(poll())
        self.finished.assert_called_once_with({'supported': True, 'above': True}, None)
        self.assertTrue(WindowPreferences(self.path).document()['always_on_top'])
        Preferences(self.path).update({'show_port_warning': False})
        self.assertTrue(WindowPreferences(self.path).document()['always_on_top'])
        self.assertEqual((self.path / 'window.json').stat().st_mode & 0o777, 0o600)

    def test_unconfirmed_request_times_out_and_retains_persistent_preference(self):
        WindowPreferences(self.path).update({'always_on_top': True})
        self.controls.change(False, self.finished)
        poll = self.glib.timeout_add.call_args.args[1]
        self.controls.native.state.return_value = {'supported': True, 'above': True}
        for _ in range(24):
            self.assertTrue(poll())
        self.assertFalse(poll())
        self.assertIsNone(self.controls.pending)
        self.assertTrue(WindowPreferences(self.path).document()['always_on_top'])
        self.assertIn('did not confirm', self.finished.call_args.args[1])

    def test_window_manager_disappearance_does_not_save_requested_state(self):
        self.controls.change(True, self.finished)
        self.controls.native.state.return_value = {'supported': False, 'above': False}
        self.assertFalse(self.glib.timeout_add.call_args.args[1]())
        self.assertFalse((self.path / 'window.json').exists())
        self.assertIn('did not confirm', self.finished.call_args.args[1])

    def test_disk_failure_reports_changed_state_without_overwriting_saved_preference(self):
        WindowPreferences(self.path).update({'always_on_top': False})
        self.controls.change(True, self.finished)
        self.controls.native.state.return_value = {'supported': True, 'above': True}
        with patch('gui.preferences.os.replace', side_effect=OSError('Injected failure')):
            self.assertFalse(self.glib.timeout_add.call_args.args[1]())
        self.assertFalse(WindowPreferences(self.path).document()['always_on_top'])
        self.assertIn('could not be saved', self.finished.call_args.args[1])

    def test_corrupt_window_preference_does_not_affect_warning_preference(self):
        Preferences(self.path).update({'show_port_warning': False})
        for data in ('{"always_on_top":1}', '{"always_on_top":true,"always_on_top":false}', ' ' * 257):
            (self.path / 'window.json').write_text(data)
            self.assertFalse(WindowPreferences(self.path).document()['always_on_top'])
            self.assertFalse(Preferences(self.path).document()['show_port_warning'])

    def test_no_x11_support_does_not_attempt_a_window_change(self):
        native = WindowState()
        self.assertEqual(native.state(), {'supported': False, 'above': False})
        with self.assertRaises(ValueError):
            native.request(True)
        native.close()


if __name__ == '__main__':
    unittest.main()
