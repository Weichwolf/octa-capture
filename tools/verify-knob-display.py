#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Execute original rotary display-position mapping with memory-only resource getters."""

import argparse
import json
from pathlib import Path
import runpy
import struct
import subprocess

from unicorn import UC_HOOK_CODE
from unicorn import x86_const as registers

HELPERS = runpy.run_path(str(Path(__file__).with_name('verify-pcm-peaks.py')))
CONTEXT, PROPS, STOP = (HELPERS[name] for name in ('CONTEXT', 'PROPS', 'STOP'))
WINDOWS_SHA = '634c140bc4e49b0d65c70d5156be19573ad3d440ac5ea24e24d3ad59974da406'
MAC_SHA = '1ada3d6be71a6559ff84e7b21af1c964f0df2ce305c1f45fe0018cc148922092'


class Reference:
    def __init__(self, path, windows):
        self.windows = windows
        self.cpu = HELPERS['cpu_image'](path, WINDOWS_SHA if windows else MAC_SHA, windows=windows)
        self.write(CONTEXT + (0xf8 if windows else 0x48), PROPS, 'Q')
        self.write(PROPS, PROPS + 0x100, 'Q')
        for slot, value in ((0x18 if windows else 0x20, 0), (0x20 if windows else 0x28, 64)):
            address = STOP + 0x100 + slot
            self.write(PROPS + 0x100 + slot, address, 'Q')
            self.cpu.hook_add(UC_HOOK_CODE, self.getter(value), begin=address, end=address)
        if windows:
            require(self.read(0x140012f95) == 65, 'Windows rotary frame count differs.')
        else:
            require(bytes(self.cpu.mem_read(0x10002b463, 5)) == b'\xba\x41\0\0\0',
                    'macOS rotary resource constructor frame count differs.')

    def getter(self, value):
        def called(cpu, *_):
            register = registers.UC_X86_REG_RCX if self.windows else registers.UC_X86_REG_RDI
            require(cpu.reg_read(register) == PROPS, 'Unexpected display resource context.')
            stack = cpu.reg_read(registers.UC_X86_REG_RSP)
            cpu.reg_write(registers.UC_X86_REG_RAX, value)
            cpu.reg_write(registers.UC_X86_REG_RIP, self.read(stack, 'Q'))
            cpu.reg_write(registers.UC_X86_REG_RSP, stack + 8)
        return called

    def read(self, address, kind='I'):
        return struct.unpack('<' + kind, self.cpu.mem_read(address, struct.calcsize('<' + kind)))[0]

    def write(self, address, value, kind='I'):
        self.cpu.mem_write(address, struct.pack('<' + kind, value))

    def frame(self, maximum, value):
        offset = 0x11c if self.windows else 0x6c
        self.cpu.mem_write(CONTEXT + offset, struct.pack('<3i', 0, maximum, maximum))
        HELPERS['execute'](self.cpu, 0x14001f0e0 if self.windows else 0x10000777e,
                           (CONTEXT, value & 0xffffffff, 0, 0), windows=self.windows)
        return self.cpu.reg_read(registers.UC_X86_REG_RAX) & 0xffffffff


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('windows_gui', type=Path)
    parser.add_argument('--macos', type=Path, required=True)
    parser.add_argument('--save-vectors', type=Path)
    args = parser.parse_args()
    windows, mac = Reference(args.windows_gui, True), Reference(args.macos, False)
    cases = []
    for maximum in (8, 25, 45, 50, 74, 100, 200, 1024):
        for value in (-maximum, -1, *range(maximum + 1), maximum + 1, maximum * 2):
            frame = windows.frame(maximum, value)
            require(mac.frame(maximum, value) == frame, 'Original rotary display mappings differ.')
            cases.append({'maximum': maximum, 'value': value, 'frame': frame})
    root = Path(__file__).resolve().parent.parent
    script = """import {knobFrame} from './gui/web/knobs.js';
let text='';for await(const part of process.stdin)text+=part;
console.log(JSON.stringify(JSON.parse(text).map(row=>knobFrame(row.value,row.maximum))));"""
    result = subprocess.check_output(['node', '--input-type=module', '-e', script], cwd=root,
                                     input=json.dumps(cases).encode(), timeout=5)
    require(json.loads(result) == [case['frame'] for case in cases], 'Web rotary display mapping differs.')
    if args.save_vectors:
        args.save_vectors.write_text(json.dumps({'windows_sha256': WINDOWS_SHA, 'macos_sha256': MAC_SHA,
                                                'cases': cases}, separators=(',', ':')) + '\n')
    print(f'PASS {len(cases)} rotary display mappings per original; all legal indices and clamped boundaries match the web.')
    print('Resource endpoint getters are modeled; original arithmetic and frame-count instructions execute. No painting or hardware I/O.')


if __name__ == '__main__':
    main()
