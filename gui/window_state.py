# SPDX-License-Identifier: GPL-2.0-only
"""EWMH state of our own X11 window; no device or process-control operations."""

from __future__ import annotations


class WindowState:
    def __init__(self, xid=None):
        self.connection = None
        if xid is None:
            return
        from Xlib import X, Xatom, display, error
        from Xlib.protocol.event import ClientMessage
        self.X = X
        self.Xatom = Xatom
        self.ClientMessage = ClientMessage
        self.errors = (OSError, error.XError, error.DisplayError, error.ConnectionClosedError)
        try:
            self.connection = display.Display()
            self.root = self.connection.screen().root
            self.window = self.connection.create_resource_object('window', xid)
            self.supported_atom = self.connection.intern_atom('_NET_SUPPORTED')
            self.state_atom = self.connection.intern_atom('_NET_WM_STATE')
            self.above_atom = self.connection.intern_atom('_NET_WM_STATE_ABOVE')
        except self.errors:
            self.close()

    @classmethod
    def for_window(cls, window):
        # GdkWayland surfaces have no XID and cannot use the EWMH protocol.
        surface = window.get_surface()
        return cls(surface.get_xid() if surface and hasattr(surface, 'get_xid') else None)

    def atoms(self, window, name):
        value = window.get_property(name, self.Xatom.ATOM, 0, 4096, delete=False)
        if value is None:
            return []
        if value.format != 32 or value.bytes_after:
            raise ValueError('Invalid window-manager state property.')
        return value.value

    def state(self):
        if self.connection:
            try:
                if self.above_atom in self.atoms(self.root, self.supported_atom):
                    return {'supported': True, 'above': self.above_atom in self.atoms(self.window, self.state_atom)}
            except self.errors + (ValueError,):
                self.close()
        return {'supported': False, 'above': False}

    def request(self, above):
        if type(above) is not bool:
            raise ValueError('Expected a boolean window state.')
        if not self.state()['supported']:
            raise ValueError('Always on top is unavailable on this desktop.')
        message = self.ClientMessage(window=self.window.id, client_type=self.state_atom,
                                     data=(32, [int(above), self.above_atom, 0, 1, 0]))
        try:
            self.root.send_event(message, event_mask=self.X.SubstructureRedirectMask | self.X.SubstructureNotifyMask)
            self.connection.flush()
        except self.errors as error:
            self.close()
            raise ValueError('Could not contact the window manager.') from error

    def close(self):
        if self.connection:
            try:
                self.connection.close()
            except self.errors:
                pass
            self.connection = None
