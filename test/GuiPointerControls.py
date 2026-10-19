# SPDX-License-Identifier: GPL-2.0-only
"""Offline native-mouse projection and GTK event-boundary regression cases."""

import math
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from gui.pointer_controls import PointerControls, mouse_message


class Messages(unittest.TestCase):
    def test_button_state_after_each_chord_transition(self):
        for button, bit in ((2, 4), (3, 2)):
            for held, mask in ((0, 0), (1, 256), (4, 512), (2, 1024), (7, 1792)):
                with self.subTest(button=button, held=held):
                    self.assertEqual(mouse_message(button, True, mask, 20, 30)['buttons'], held | bit)
                    self.assertEqual(mouse_message(button, False, mask, 20, 30)['buttons'], held & ~bit)
        message = mouse_message(3, False, 1280, -5, 12.5)
        self.assertEqual((message['type'], message['button'], message['buttons'], message['x']),
                         ('mouseup', 2, 1, -5))

    def test_modifier_mapping_and_no_unrequested_fields(self):
        for mask, name in ((1, 'shiftKey'), (4, 'ctrlKey'), (8, 'altKey'),
                           (1 << 26, 'metaKey'), (1 << 28, 'metaKey')):
            message = mouse_message(2, True, mask, 0, 0)
            self.assertTrue(message[name])
            self.assertEqual(sum(message[key] for key in ('shiftKey', 'ctrlKey', 'altKey', 'metaKey')), 1)
        self.assertEqual(set(message), {'type', 'button', 'buttons', 'x', 'y',
                                        'shiftKey', 'ctrlKey', 'altKey', 'metaKey'})

    def test_invalid_parameters_are_never_serialized_into_javascript(self):
        for button in (None, True, 0, 1, 4, 8, '3'):
            self.assertIsNone(mouse_message(button, True, 0, 1, 2))
        for pressed in (None, 0, 1, 'up'):
            self.assertIsNone(mouse_message(3, pressed, 0, 1, 2))
        for state in (-1, 1 << 32, True, '0', None):
            self.assertIsNone(mouse_message(3, False, state, 1, 2))
        for position in (None, True, '1', math.nan, math.inf, -math.inf, 1_000_001, -1_000_001):
            self.assertIsNone(mouse_message(3, False, 0, position, 2))
            self.assertIsNone(mouse_message(3, False, 0, 1, position))


class Boundary(unittest.TestCase):
    def setUp(self):
        self.controls = PointerControls.__new__(PointerControls)
        self.controls.press = 10
        self.controls.reader = Mock()
        self.controls.view, self.controls.window = Mock(), Mock()
        bounds = Mock()
        bounds.get_x.return_value, bounds.get_y.return_value = 5, 7
        self.controls.view.compute_bounds.return_value = (True, bounds)
        self.controls.view.get_zoom_level.return_value = 2
        self.controls.window.get_surface_transform.return_value = (2, 3)
        self.controls.dispatch = Mock()

    def test_primary_and_nonbutton_events_retain_native_dispatch(self):
        for event in (None, (10, 1, 0, 20, 30), (11, 1, 256, 20, 30)):
            self.controls.reader.read.return_value = event
            self.assertFalse(self.controls.receive(None, None))
        self.controls.dispatch.assert_not_called()

    def test_secondary_events_use_surface_translation_zoom_and_postrelease_state(self):
        self.controls.reader.read.return_value = (10, 3, 256, 13, 24)
        self.assertTrue(self.controls.receive(None, None))
        message = self.controls.dispatch.call_args.args[0]
        self.assertEqual((message['x'], message['y'], message['buttons']), (5, 10, 3))
        self.controls.view.grab_focus.assert_called_once()
        self.controls.reader.read.return_value = (11, 3, 1280, 13, 24)
        self.controls.receive(None, None)
        self.assertEqual(self.controls.dispatch.call_args.args[0]['buttons'], 1)
        self.assertEqual(self.controls.view.grab_focus.call_count, 1)

    def test_unsupported_buttons_or_unavailable_geometry_do_not_reach_the_page(self):
        for button in (0, 4, 8, 100):
            self.controls.reader.read.return_value = (10, button, 0, 20, 30)
            self.assertTrue(self.controls.receive(None, None))
        self.controls.dispatch.assert_not_called()
        self.controls.reader.read.return_value = (10, 3, 0, 20, 30)
        for zoom in (0, -1, math.nan, math.inf):
            self.controls.view.get_zoom_level.return_value = zoom
            self.assertTrue(self.controls.receive(None, None))
        self.controls.view.get_zoom_level.return_value = 1
        self.controls.view.compute_bounds.return_value = (False, None)
        self.assertTrue(self.controls.receive(None, None))
        self.controls.dispatch.assert_not_called()


if __name__ == '__main__':
    unittest.main()
