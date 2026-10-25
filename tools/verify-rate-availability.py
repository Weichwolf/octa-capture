#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Compare original Windows/macOS rate availability with the Linux GUI/backend."""

import argparse
import itertools
import json
from pathlib import Path
import runpy
import struct
import subprocess
import sys

from unicorn import UC_HOOK_CODE
from unicorn import x86_const as registers

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from gui.application import available_sample_rates

STATUS = runpy.run_path(str(Path(__file__).with_name('verify-status-poll.py')))
HELPERS = STATUS['HELPERS']
CONTEXT, PROPS, DATA, STOP, RATES = (STATUS[name] for name in ('CONTEXT', 'PROPS', 'DATA', 'STOP', 'RATES'))
read, write, require, returned = (STATUS[name] for name in ('read', 'write', 'require', 'returned'))


class Mac:
    def __init__(self, path):
        self.cpu = HELPERS['cpu_image'](path, HELPERS['MAC_SHA256'])
        self.cpu.hook_add(UC_HOOK_CODE, self.capability, begin=STOP + 0x100, end=STOP + 0x100)
        write(self.cpu, CONTEXT, PROPS, 'Q')
        write(self.cpu, PROPS + 0x10, STOP + 0x100, 'Q')
        write(self.cpu, CONTEXT + 0xd0, PROPS + 0x400, 'Q')

    def capability(self, cpu, *_):
        require(cpu.reg_read(registers.UC_X86_REG_RDI) == CONTEXT and
                cpu.reg_read(registers.UC_X86_REG_RSI) == 1, 'Unexpected macOS rate-capability selector.')
        returned(cpu, int(cpu.reg_read(registers.UC_X86_REG_RDX) in RATES))

    def allowed(self, rate, source, active):
        write(self.cpu, CONTEXT + 8, rate)
        write(self.cpu, CONTEXT + 0xc, source, 'B')
        write(self.cpu, CONTEXT + 0xd, active, 'B')
        write(self.cpu, PROPS + 0x430, rate)
        self.cpu.mem_write(DATA, bytes([0xa5]) * 256)
        HELPERS['execute'](self.cpu, 0xd64a, (CONTEXT, DATA, 0, 0))
        require(self.cpu.reg_read(registers.UC_X86_REG_RAX) & 0xff == 1 and read(self.cpu, DATA + 0x10) == 4,
                'Original macOS properties did not enumerate four supported rates.')
        expected = bytearray([0xa5] * 256)
        struct.pack_into('<5I', expected, 0, active, source, rate, rate, 4)
        allowed = []
        for index, candidate in enumerate(RATES):
            offset = 0x14 + index * 12
            actual, enabled = struct.unpack('<II', self.cpu.mem_read(DATA + offset, 8))
            require(actual == candidate and enabled in (0, 1), 'Original macOS rate-property layout differs.')
            struct.pack_into('<II', expected, offset, candidate, enabled)
            if enabled:
                allowed.append(candidate)
        require(bytes(self.cpu.mem_read(DATA, 256)) == expected, 'Original macOS properties changed reserved bytes.')
        return allowed


def windows_allowed(windows, rate, source):
    windows.reset([])
    write(windows.cpu, windows.manager + 0x2c, rate)
    write(windows.cpu, windows.manager + 0x30, source, 'B')
    write(windows.cpu, PROPS + 0x38, rate)
    allowed = []
    for candidate in RATES:
        windows.cpu.mem_write(STATUS['OUTPUT'], struct.pack('<III', 0, candidate, 0xa5a5a5a5))
        require(windows.ioctl(0x22232c) == 0, 'Original Windows rate-availability request failed.')
        if read(windows.cpu, STATUS['OUTPUT'] + 8):
            allowed.append(candidate)
    require(windows.transfers == 0, 'Original Windows rate-availability getter performed USB I/O.')
    return allowed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('windows_kernel', type=Path)
    parser.add_argument('--macos', type=Path, required=True)
    parser.add_argument('--save-vectors', type=Path)
    args = parser.parse_args()
    windows, mac = STATUS['Windows'](args.windows_kernel), Mac(args.macos)
    cases = []
    for rate, source, active in itertools.product(RATES, (0, 1, 2, 255), (0, 1, 2, 255)):
        packet = bytes([1, 0, 0]) + rate.to_bytes(3, 'little') + bytes([source, active])
        status = {'sample_rate_hz': rate, 'raw_status_hex': packet.hex(),
                  'clock_source': 'external' if source & 1 else 'internal', 'clock_active': active != 0}
        expected = windows_allowed(windows, rate, source)
        require(mac.allowed(rate, source, active) == expected, 'Original Windows/macOS availability differs.')
        require(list(available_sample_rates(status)) == expected, 'Linux backend availability differs.')
        cases.append({'status': status, 'allowed': expected})
    script = """import {availableSampleRates} from './gui/web/parameters.js';
let input='';for await(const chunk of process.stdin)input+=chunk;
console.log(JSON.stringify(JSON.parse(input).map(row=>availableSampleRates(row.status))));"""
    output = subprocess.check_output(['node', '--input-type=module', '-e', script], cwd=ROOT,
                                     input=json.dumps(cases).encode(), timeout=10)
    require(json.loads(output) == [row['allowed'] for row in cases], 'Web rate availability differs.')
    if args.save_vectors:
        args.save_vectors.write_text(json.dumps({'windows_sha256': HELPERS['WINDOWS_SHA256'],
                                                'macos_sha256': HELPERS['MAC_SHA256'], 'cases': cases},
                                               separators=(',', ':')) + '\n')
    print('PASS 64 original rate-availability cases per reference; Linux backend and web choices match.')
    print('Steady-state single-unit clocks only. Capability callbacks/host I/O are stubs; no physical clock changes.')


if __name__ == '__main__':
    main()
