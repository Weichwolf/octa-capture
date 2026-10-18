# SPDX-License-Identifier: GPL-2.0-only
"""Small persistent control-panel preferences, independent of device access."""

from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import threading

from .backend import InvalidControl


def _unique_object(pairs):
    result = dict(pairs)
    if len(result) != len(pairs):
        raise ValueError('Duplicate preference field.')
    return result


class Preferences:
    defaults = {'show_port_warning': True}
    filename = 'preferences.json'

    def __init__(self, directory):
        self.directory = Path(directory)
        self.path = self.directory / self.filename
        self._lock = threading.RLock()
        self._value = dict(self.defaults)
        try:
            with self.path.open('r', encoding='utf-8') as source:
                data = source.read(257)
            if len(data) <= 256:
                value = json.loads(data, object_pairs_hook=_unique_object)
                self._validate(value)
                self._value = value
        except (OSError, ValueError, UnicodeError):
            pass

    @classmethod
    def _validate(cls, value):
        if (type(value) is not dict or set(value) != set(cls.defaults) or
                any(type(item) is not bool for item in value.values())):
            raise InvalidControl('Expected the supported boolean preferences.')

    def document(self):
        with self._lock:
            return dict(self._value)

    def update(self, value):
        self._validate(value)
        with self._lock:
            self.directory.mkdir(parents=True, mode=0o700, exist_ok=True)
            descriptor, name = tempfile.mkstemp(prefix='.preferences-', dir=self.directory)
            temporary = Path(name)
            try:
                with os.fdopen(descriptor, 'w', encoding='utf-8') as output:
                    json.dump(value, output)
                    output.write('\n')
                    output.flush()
                    os.fsync(output.fileno())
                os.replace(temporary, self.path)
                self._value = dict(value)
            finally:
                temporary.unlink(missing_ok=True)
            return self.document()


class WindowPreferences(Preferences):
    defaults = {'always_on_top': False}
    filename = 'window.json'
