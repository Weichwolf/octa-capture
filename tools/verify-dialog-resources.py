#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Compare public dialog-keyboard fixtures with the original Windows resources."""

import argparse
import hashlib
import json
from pathlib import Path
import runpy
import struct

HELPERS = runpy.run_path(str(Path(__file__).with_name('verify-gui-parameters.py')))
require = HELPERS['require']


def resource_dialogs(image):
    pe = struct.unpack_from('<I', image, 0x3c)[0]
    address, size = struct.unpack_from('<II', image, pe + 24 + 128)
    root = HELPERS['windows_data'](image, 0x140000000 + address, size)

    def entries(offset):
        count = sum(struct.unpack_from('<HH', root, offset + 12))
        return [struct.unpack_from('<II', root, offset + 16 + index * 8) for index in range(count)]

    result = {}
    for kind, directory in entries(0):
        if kind != 5:
            continue
        for name, languages in entries(directory & 0x7fffffff):
            localized = dict(entries(languages & 0x7fffffff))
            english = localized.get(1033, localized.get(0))
            require(english is not None, 'Expected an English or neutral dialog resource.')
            address, size = struct.unpack_from('<II', root, english)
            result[name] = HELPERS['windows_data'](image, 0x140000000 + address, size)
    return result


def field(blob, position):
    if struct.unpack_from('<H', blob, position)[0] == 0xffff:
        return struct.unpack_from('<H', blob, position + 2)[0], position + 4
    end = position
    while struct.unpack_from('<H', blob, end)[0]:
        end += 2
    return blob[position:end].decode('utf-16le'), end + 2


def controls(blob):
    extended = struct.unpack_from('<HH', blob) == (1, 0xffff)
    if extended:
        _, _, _, _, style, count, *_ = struct.unpack_from('<HHIIIHhhhh', blob)
        position = 26
    else:
        style, _, count, *_ = struct.unpack_from('<IIHhhhh', blob)
        position = 18
    for _ in range(3):
        _, position = field(blob, position)
    if style & 0x40:
        _, position = field(blob, position + (6 if extended else 2))
    result = []
    for _ in range(count):
        position = (position + 3) & ~3
        if extended:
            _, _, style, _, _, _, _, identifier = struct.unpack_from('<IIIhhhhI', blob, position)
            position += 24
        else:
            style, _, _, _, _, _, identifier = struct.unpack_from('<IIhhhhH', blob, position)
            position += 18
        kind, position = field(blob, position)
        _, position = field(blob, position)
        extra = struct.unpack_from('<H', blob, position)[0]
        position += 2 + extra
        result.append((identifier, bool(style & 0x10000), kind == 0x80 and style & 0xf == 1))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('windows_gui', type=Path)
    args = parser.parse_args()
    image = args.windows_gui.read_bytes()
    require(hashlib.sha256(image).hexdigest() == HELPERS['WINDOWS_SHA256'], 'Unexpected GUI reference SHA-256.')
    dialogs = resource_dialogs(image)
    fixtures = json.loads((Path(__file__).resolve().parent.parent / 'test/data/dialog-keyboard.json').read_text())
    for name, fixture in fixtures.items():
        items = controls(dialogs[fixture['resource']])
        require([identifier for identifier, tab, _ in items if tab] == [identifier for identifier, _ in fixture['controls']],
                f'{name}: original WS_TABSTOP order differs.')
        require([identifier for identifier, _, default in items if default] == [fixture['default_id']],
                f'{name}: original default pushbutton differs.')
    print(json.dumps({'dialogs': len(fixtures), 'tab_stops': sum(len(item['controls']) for item in fixtures.values()),
                      'source': 'Windows x64 English/neutral RT_DIALOG resources', 'hardware_validation': False}))


if __name__ == '__main__':
    main()
