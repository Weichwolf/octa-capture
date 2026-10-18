# SPDX-License-Identifier: GPL-2.0-only
"""Narrow WebKit bridge for the panel's own window, separate from device I/O."""

from __future__ import annotations

import json
import sys

from .navigation import local_document
from .preferences import WindowPreferences
from .window_state import WindowState


def window_request(value):
    if type(value) is not dict:
        raise ValueError('Expected a window-state request.')
    if value == {'action': 'state'}:
        return None
    if (set(value) == {'action', 'value'} and value['action'] == 'set' and
            type(value['value']) is bool):
        return value['value']
    raise ValueError('Unsupported window-state request.')


class WindowControls:
    def __init__(self, window, directory, glib):
        self.window = window
        self.GLib = glib
        self.preferences = WindowPreferences(directory)
        self.native = WindowState()
        self.pending = None
        window.connect('map', self.mapped)

    def mapped(self, _window):
        self.native.close()
        try:
            self.native = WindowState.for_window(self.window)
        except ImportError:
            print('Always on top requires the Debian package python3-xlib on X11.', file=sys.stderr)
        if self.preferences.document()['always_on_top'] and self.native.state()['supported']:
            try:
                self.change(True, lambda _state, error: print(error, file=sys.stderr) if error else None, save=False)
            except ValueError as error:
                print(error, file=sys.stderr)

    def change(self, above, finished, *, save=True):
        if self.pending is not None:
            raise ValueError('A window-state change is already pending.')
        self.native.request(above)
        attempts = 0

        def inspect():
            nonlocal attempts
            attempts += 1
            state = self.native.state()
            if state['supported'] and state['above'] == above:
                self.pending = None
                try:
                    if save:
                        self.preferences.update({'always_on_top': above})
                except OSError:
                    finished(None, 'Window state changed, but its preference could not be saved.')
                else:
                    finished(state, None)
                return False
            if not state['supported'] or attempts >= 25:
                self.pending = None
                finished(None, 'The window manager did not confirm Always on top.')
                return False
            return True
        self.pending = self.GLib.timeout_add(20, inspect)

    def message(self, view, origin, value, reply):
        def finished(state, error):
            if error:
                reply.return_error_message(error)
            else:
                from gi.repository import JavaScriptCore
                reply.return_value(JavaScriptCore.Value.new_from_json(value.get_context(), json.dumps(state)))
        try:
            if not local_document(view.get_uri(), origin):
                raise ValueError('Window controls require the local panel document.')
            encoded = value.to_json(0)
            if not encoded or len(encoded) > 128:
                raise ValueError('Invalid window-state request.')
            requested = window_request(json.loads(encoded))
            if requested is None:
                finished(self.native.state(), None)
            else:
                self.change(requested, finished)
        except (ValueError, TypeError, self.GLib.Error) as error:
            finished(None, str(error))
        return True

    def close(self):
        if self.pending is not None:
            self.GLib.source_remove(self.pending)
            self.pending = None
        self.native.close()
