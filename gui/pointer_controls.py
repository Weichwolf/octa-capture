# SPDX-License-Identifier: GPL-2.0-only
"""Preserve primary GTK gestures while forwarding secondary mouse buttons."""

import ctypes
import ctypes.util
import json
import math


def mouse_message(button, pressed, state, x, y):
    if (type(button) is not int or button not in (2, 3) or type(pressed) is not bool or
            type(state) is not int or not 0 <= state <= 0xffffffff or
            any(type(value) not in (int, float) or not math.isfinite(value) or abs(value) > 1_000_000
                for value in (x, y))):
        return None
    buttons = sum(bit for mask, bit in ((256, 1), (512, 4), (1024, 2)) if state & mask)
    bit = 4 if button == 2 else 2
    return {'type': 'mousedown' if pressed else 'mouseup', 'button': button - 1,
            'buttons': buttons | bit if pressed else buttons & ~bit, 'x': x, 'y': y,
            'shiftKey': bool(state & 1), 'ctrlKey': bool(state & 4), 'altKey': bool(state & 8),
            'metaKey': bool(state & ((1 << 26) | (1 << 28)))}


class EventReader:
    """Use the public C API when PyGObject cannot marshal the opaque GdkEvent."""
    def __init__(self, controller, kinds):
        library = ctypes.util.find_library('gtk-4')
        if not library:
            raise OSError('GTK 4 event library is unavailable.')
        self.library = ctypes.CDLL(library)
        declarations = {
            'gtk_event_controller_get_current_event': ([ctypes.c_void_p], ctypes.c_void_p),
            'gdk_event_get_event_type': ([ctypes.c_void_p], ctypes.c_int),
            'gdk_button_event_get_button': ([ctypes.c_void_p], ctypes.c_uint),
            'gdk_event_get_modifier_state': ([ctypes.c_void_p], ctypes.c_uint),
            'gdk_event_get_position': ([ctypes.c_void_p, ctypes.POINTER(ctypes.c_double),
                                       ctypes.POINTER(ctypes.c_double)], ctypes.c_int)}
        for name, (arguments, result) in declarations.items():
            function = getattr(self.library, name)
            function.argtypes, function.restype = arguments, result
        capsule_name = ctypes.pythonapi.PyCapsule_GetName
        capsule_name.argtypes, capsule_name.restype = [ctypes.py_object], ctypes.c_char_p
        capsule_pointer = ctypes.pythonapi.PyCapsule_GetPointer
        capsule_pointer.argtypes, capsule_pointer.restype = [ctypes.py_object, ctypes.c_char_p], ctypes.c_void_p
        self.pointer = capsule_pointer(controller.__gpointer__, capsule_name(controller.__gpointer__))
        self.kinds = kinds

    def read(self):
        event = self.library.gtk_event_controller_get_current_event(self.pointer)
        if not event:
            return None
        kind = self.library.gdk_event_get_event_type(event)
        if kind not in self.kinds:
            return None
        button = self.library.gdk_button_event_get_button(event)
        state = self.library.gdk_event_get_modifier_state(event)
        x, y = ctypes.c_double(), ctypes.c_double()
        if not self.library.gdk_event_get_position(event, ctypes.byref(x), ctypes.byref(y)):
            return None
        return kind, button, state, x.value, y.value


class PointerControls:
    def __init__(self, gtk, view, window):
        from gi.repository import Gdk
        self.view, self.window = view, window
        self.controller = gtk.EventControllerLegacy()
        self.controller.set_propagation_phase(gtk.PropagationPhase.CAPTURE)
        self.press = int(Gdk.EventType.BUTTON_PRESS)
        self.reader = EventReader(self.controller, (self.press, int(Gdk.EventType.BUTTON_RELEASE)))
        self.controller.connect('event', self.receive)
        view.add_controller(self.controller)

    def receive(self, _controller, _event):
        event = self.reader.read()
        if event is None or event[1] == 1:
            return False
        kind, button, state, x, y = event
        valid, bounds = self.view.compute_bounds(self.window)
        zoom = self.view.get_zoom_level()
        if not valid or not math.isfinite(zoom) or zoom <= 0:
            return True
        tx, ty = self.window.get_surface_transform()
        message = mouse_message(button, kind == self.press, state,
                                (x + tx - bounds.get_x()) / zoom, (y + ty - bounds.get_y()) / zoom)
        if message is not None:
            if kind == self.press:
                self.view.grab_focus()
            self.dispatch(message)
        # GtkGestureSingle resets its active primary sequence on another button.
        return True

    def dispatch(self, message):
        script = '''(()=>{const p=''' + json.dumps(message) + ''';
            const node=document.elementFromPoint(p.x,p.y);if(!node)return;
            node.dispatchEvent(new MouseEvent(p.type,{...p,clientX:p.x,clientY:p.y,
                bubbles:true,cancelable:true}));})();'''
        self.view.evaluate_javascript(script, -1, None, None, None, None, None)
