#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Execute original switch clip selectors; compare the web's numeric frame layout."""

import argparse
import json
from pathlib import Path
import runpy
import struct
import subprocess

from unicorn import UC_HOOK_CODE
from unicorn import x86_const as registers

HELPERS = runpy.run_path(str(Path(__file__).with_name('verify-pcm-peaks.py')))
CONTEXT, PROPS = (HELPERS[name] for name in ('CONTEXT', 'PROPS'))
WINDOWS_SHA = '634c140bc4e49b0d65c70d5156be19573ad3d440ac5ea24e24d3ad59974da406'
MAC_SHA = '1ada3d6be71a6559ff84e7b21af1c964f0df2ce305c1f45fe0018cc148922092'


class Reference:
    def __init__(self, path, windows):
        self.windows = windows
        self.cpu = HELPERS['cpu_image'](path, WINDOWS_SHA if windows else MAC_SHA, windows=windows)
        self.host_enabled = None
        if windows:
            self.write(CONTEXT + 0xf0, PROPS, 'Q')
            self.write(PROPS + 0x64, 4)
            self.write(0x1401eff58, PROPS + 0x800, 'Q')
            self.stub(PROPS + 0x800, lambda: 0)  # InvalidateRect.
            self.stub(0x14003b8b8, self.enable_window)  # MFC host call.
        else:
            self.write(CONTEXT + 0x48, PROPS, 'Q')
            self.write(PROPS + 0x1c, 4)

    def write(self, address, value, code='I'):
        self.cpu.mem_write(address, struct.pack('<' + code, value))

    def read(self, address):
        return struct.unpack('<I', self.cpu.mem_read(address, 4))[0]

    def stub(self, address, callback):
        def hook(cpu, _address, _size, _data):
            stack = cpu.reg_read(registers.UC_X86_REG_RSP)
            cpu.reg_write(registers.UC_X86_REG_RAX, callback())
            cpu.reg_write(registers.UC_X86_REG_RIP, struct.unpack('<Q', cpu.mem_read(stack, 8))[0])
            cpu.reg_write(registers.UC_X86_REG_RSP, stack + 8)
        self.cpu.hook_add(UC_HOOK_CODE, hook, begin=address, end=address)

    def enable_window(self):
        self.host_enabled = bool(self.cpu.reg_read(registers.UC_X86_REG_RDX))
        return 1

    def call(self, address, pressed=0):
        HELPERS['execute'](self.cpu, address, (CONTEXT, pressed, 0, 0), windows=self.windows)
        return self.cpu.reg_read(registers.UC_X86_REG_RAX) & 0xffffffff

    def frame(self, lit, pressed, disabled):
        self.write(CONTEXT + (0xe8 if self.windows else 0x40), int(lit))
        self.write(CONTEXT + (0xec if self.windows else 0x44), int(disabled))
        return self.call(0x140018df0 if self.windows else 0x100001be8, int(pressed))

    def availability(self, disabled):
        self.host_enabled = None
        self.call(0x140016b30 if disabled else 0x140018940)
        if self.read(CONTEXT + 0xec) != int(disabled) or self.host_enabled != (not disabled):
            raise RuntimeError('Original Windows switch availability transition differs.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('windows_gui', type=Path)
    parser.add_argument('--macos', required=True, type=Path)
    parser.add_argument('--save-vectors', type=Path)
    args = parser.parse_args()
    windows, mac = Reference(args.windows_gui, True), Reference(args.macos, False)
    cases = []
    for disabled in (False, True):
        for lit in (False, True):
            for pressed in (False, True):
                results = [reference.frame(lit, pressed, disabled) for reference in (windows, mac)]
                if results[0] != results[1]:
                    raise RuntimeError(f'Original switch selectors differ: {lit}/{pressed}/{disabled}: {results}')
                cases.append({'lit': lit, 'pressed': pressed, 'disabled': disabled, 'expected': results[0]})
    for disabled in (False, True, True, False):
        windows.availability(disabled)
    script = """import {switchFrame} from './gui/web/knobs.js';
let input='';for await(const part of process.stdin)input+=part;
console.log(JSON.stringify(JSON.parse(input).map(t=>switchFrame(t.lit,t.pressed,t.disabled))));"""
    actual = json.loads(subprocess.check_output(['node', '--input-type=module', '-e', script],
                                              input=json.dumps(cases).encode(),
                                              cwd=Path(__file__).resolve().parent.parent))
    if actual != [case['expected'] for case in cases]:
        raise RuntimeError('Web switch frames differ from original execution.')
    if args.save_vectors:
        args.save_vectors.write_text(json.dumps({'windows_sha256': WINDOWS_SHA, 'macos_sha256': MAC_SHA,
                                                'cases': cases}, separators=(',', ':')) + '\n')
    print('PASS eight clip selectors per original and four Windows availability transitions; web frames match both.')
    print('Window invalidation/enabling are host stubs. Input tracking, raster painting and hardware are outside this check.')


if __name__ == '__main__':
    main()
