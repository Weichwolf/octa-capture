#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Execute original Windows/macOS output-jack selectors offline and compare HTML logic."""

import argparse
import json
from pathlib import Path
import runpy
import struct
import subprocess

from unicorn import x86_const as registers

HELPERS = runpy.run_path(str(Path(__file__).with_name('verify-pcm-peaks.py')))
CONTEXT, PROPS = (HELPERS[name] for name in ('CONTEXT', 'PROPS'))
WINDOWS_SHA = '634c140bc4e49b0d65c70d5156be19573ad3d440ac5ea24e24d3ad59974da406'
MAC_SHA = '1ada3d6be71a6559ff84e7b21af1c964f0df2ce305c1f45fe0018cc148922092'
STATES = ['other', 'assigned', 'unavailable']


class Reference:
    def __init__(self, path, windows):
        self.windows = windows
        self.cpu = HELPERS['cpu_image'](path, WINDOWS_SHA if windows else MAC_SHA, windows=windows)
        self.cpu.mem_write(CONTEXT + 0x48, struct.pack('<Q', PROPS))

    def result(self, rate, bus, pair, routes):
        for offset, value in ((0x21c if self.windows else 0x5c, rate),
                              (0x224 if self.windows else 0x64, bus)):
            self.cpu.mem_write(PROPS + offset, struct.pack('<I', value))
        self.cpu.mem_write(PROPS + (0x248 if self.windows else 0x70), struct.pack('<5I', *routes))
        self.cpu.mem_write(CONTEXT + 0x54, struct.pack('<I', pair))
        HELPERS['execute'](self.cpu, 0x14001a860 if self.windows else 0x10001bb4e,
                           (CONTEXT, 0, 0, 0), windows=self.windows)
        tile = self.cpu.reg_read(registers.UC_X86_REG_RAX) & 0xffffffff
        selected = struct.unpack('<I', self.cpu.mem_read(CONTEXT + 0x50, 4))[0]
        if tile // 3 != pair or selected != (0 if rate == 3 else bus):
            raise RuntimeError(f'Original output-jack tile/selected mixer differs: {tile}/{selected}')
        return tile % 3


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('windows_gui', type=Path)
    parser.add_argument('--macos', required=True, type=Path)
    parser.add_argument('--save-vectors', type=Path)
    args = parser.parse_args()
    references = [Reference(args.windows_gui, True), Reference(args.macos, False)]
    cases, expected = [], []
    vectors = [[[[0 for _route in range(9)] for _pair in range(5)] for _bus in range(4)] for _rate in range(4)]
    for rate in range(4):
        for bus in range(4):
            for pair in range(5):
                for route in range(9):
                    routes = [(index + route) % 9 for index in range(5)]
                    routes[pair] = route
                    original = [reference.result(rate, bus, pair, routes) for reference in references]
                    if original[0] != original[1]:
                        raise RuntimeError(f'Original Windows/macOS selection differs: {rate}/{bus}/{pair}/{routes}')
                    vectors[rate][bus][pair][route] = original[0]
                    cases.append([rate, bus, routes, pair])
                    expected.append(STATES[original[0]])
    script = """
import {outputJackStates} from './gui/web/parameters.js';
let text='';for await(const part of process.stdin)text+=part;
console.log(JSON.stringify(JSON.parse(text).map(([rate,bus,routes,pair])=>outputJackStates(rate,bus,routes)[pair])));
"""
    actual = json.loads(subprocess.check_output(['node', '--input-type=module', '-e', script],
                                              input=json.dumps(cases).encode(), cwd=Path(__file__).resolve().parent.parent))
    if actual != expected:
        index = next(index for index, (a, b) in enumerate(zip(actual, expected, strict=True)) if a != b)
        raise RuntimeError(f'HTML selection differs: {cases[index]}: {actual[index]} != {expected[index]}')
    if args.save_vectors:
        args.save_vectors.write_text(json.dumps({'windows_sha256': WINDOWS_SHA, 'macos_sha256': MAC_SHA,
                                                'states': vectors}, separators=(',', ':')) + '\n')
    print('PASS 720 output-jack selectors per original: all rates/mixers, five pairs and nine routes; HTML logic matches.')
    print('Only original selection code executes; painting, notifications and device transport are outside this comparison.')


if __name__ == '__main__':
    main()
