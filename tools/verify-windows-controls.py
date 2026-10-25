#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Execute legal Windows kernel control requests and compare our encoders offline."""

import argparse
from collections import Counter
import json
from pathlib import Path
import runpy
import struct
import subprocess

from unicorn import x86_const as registers

ROOT = Path(__file__).resolve().parent.parent
MONITOR = runpy.run_path(str(Path(__file__).with_name('verify-input-monitor.py')))
DSP = runpy.run_path(str(Path(__file__).with_name('verify-dsp-transmitters.py')))
HELPERS = MONITOR['HELPERS']
CONTEXT, PROPS, DATA = (HELPERS[name] for name in ('CONTEXT', 'PROPS', 'DATA'))


class Kernel(MONITOR['Kernel']):
    def sent(self, cpu, _address, _size, _context):
        size = MONITOR['read'](cpu, cpu.reg_read(registers.UC_X86_REG_R9))
        MONITOR['require'](size in (14, 17, 19), 'Unexpected original scalar MIDI length.')
        self.frames.append(bytes(cpu.mem_read(cpu.reg_read(registers.UC_X86_REG_R8), size)))
        MONITOR['returned'](cpu, 1)

    def control(self, command, arguments, gate=True, ready=True):
        self.frames.clear()
        self.cpu.mem_write(CONTEXT, bytes(0xc00))
        HELPERS['write'](self.cpu, CONTEXT + 0xa70, int(gate))
        HELPERS['write'](self.cpu, CONTEXT + 0xa48, PROPS, 'Q')
        HELPERS['write'](self.cpu, PROPS + 0x38, int(ready))
        request = bytearray(40)
        struct.pack_into('<' + 'I' * (1 + len(arguments)), request, 0x10, command, *arguments)
        self.cpu.mem_write(DATA, bytes(request))
        HELPERS['execute'](self.cpu, 0x140006550, (CONTEXT, DATA + 0x1000, DATA, 0), windows=True)
        MONITOR['require'](self.cpu.reg_read(registers.UC_X86_REG_RAX) == 1,
                           'Original scalar dispatcher did not accept its request.')
        return list(self.frames)


def preamps():
    # Windows request selectors differ from the wire parameter enumeration.
    parameters = (('phantom', 0x1c, 1), ('low-cut', 0x1f, 1), ('phase', 0x1d, 1),
                  ('hi-z', 0x1e, 1), ('sensitivity', 0x20, 100), ('stereo-link', 0x14, 1),
                  ('compressor', 0x15, 1), ('gate', 0x16, 50), ('attack', 0x17, 25),
                  ('release', 0x18, 45), ('threshold', 0x19, 50), ('ratio', 0x1a, 8), ('gain', 0x1b, 74))
    for parameter, (name, command, maximum) in enumerate(parameters):
        for channel in range(8):
            if (name == 'hi-z' and channel >= 2) or (name == 'stereo-link' and channel % 2):
                continue
            for value in range(maximum + 1):
                index = channel // 2 if name == 'stereo-link' else channel
                yield (f'preamp/{name}', command, (index, value), 'octa-control',
                       [str(channel + 1), name, str(value)], DSP['frame']((0, 5, channel, parameter), value))


def dsp_cases():
    commands = {'link': 0x21, 'mute': 0x23, 'solo': 0x24, 'pan': 0x26, 'volume': 0x27,
                'send': 0x25, 'master-link': 0x2a, 'master-volume': 0x2c, 'io-link': 0x28,
                'return': 0x2b, 'type': 0x11, 'pre-delay': 0x12, 'time': 0x13, 'route': 0x10}
    for name, arguments, cli, expected in (*DSP['mixer_cases'](), *DSP['master_cases'](), *DSP['effect_route_cases']()):
        arguments = arguments if isinstance(arguments, tuple) else (arguments,)
        yield f'dsp/{name}', commands[name], arguments, 'octa-dsp', cli, expected
    for source, name in enumerate(('input', 'playback')):
        for bus, letter in enumerate('ABCD'):
            for pair in range(5):
                for value in (0, 1):
                    yield ('dsp/mono', 0x22, (source, bus, pair, value), 'octa-dsp',
                           ['mixer', name, letter, str(pair * 2 + 1), 'mono', str(value)],
                           DSP['frame']((0, 6 + source, bus * 16 + pair * 2, 1), value))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('windows_kernel', type=Path, help='official Windows 1.5.3 x64 RDWM1110.SYS')
    parser.add_argument('--bin-dir', type=Path, default=ROOT / 'build/strict')
    args = parser.parse_args()
    kernel = Kernel(args.windows_kernel)
    coverage, gates, seen = Counter(), {}, set()
    for name, command, arguments, executable, cli, expected in (*preamps(), *dsp_cases()):
        key = command, arguments
        MONITOR['require'](key not in seen, f'Duplicate scalar request: {name}, {arguments}.')
        seen.add(key)
        original = kernel.control(command, arguments)
        MONITOR['require'](original == [expected], f'Windows frame differs: {name}, {arguments}.')
        output = subprocess.run([str(args.bin_dir / executable), '--dry-run', *cli],
                                check=True, capture_output=True, text=True, timeout=5)
        MONITOR['require'](bytes.fromhex(json.loads(output.stdout)['frame_hex']) == expected,
                           f'C++ frame differs: {name}, {arguments}.')
        coverage[name] += 1
        gates.setdefault(name, (command, arguments, expected))
    MONITOR['require'](sum(count for name, count in coverage.items() if name.startswith('preamp/')) == 2948 and
                       sum(count for name, count in coverage.items() if name.startswith('dsp/')) == 4012 and
                       len(coverage) == 28, 'Incomplete legal scalar coverage.')
    for name, (command, arguments, expected) in gates.items():
        for gate, ready in ((False, False), (False, True), (True, False), (True, True)):
            MONITOR['require'](kernel.control(command, arguments, gate, ready) ==
                               ([expected] if gate and ready else []), f'Windows transport gate differs: {name}.')
    print(json.dumps({'cases': sum(coverage.values()), 'coverage': dict(sorted(coverage.items())),
                      'transport_gate_cases': len(gates) * 4, 'source': 'Windows x64 1.5.3 kernel scalar dispatcher',
                      'host_dependencies': 'memory-only final MIDI send', 'hardware_validation': False}))
    print('PASS original dispatch/state helpers, MIDI builders/checksums and C++ dry runs. '
          'Outer IOCTL validation, coupling semantics, physical effects and complete driver parity are not established.')


if __name__ == '__main__':
    main()
