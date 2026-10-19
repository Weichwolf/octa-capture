#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Compare selected legacy Windows x86 descriptor/monitor paths offline."""

import argparse
import itertools
import json
from pathlib import Path
import runpy
import struct

from unicorn import UC_HOOK_CODE
from unicorn import x86_const as registers

import reference_x86 as x86

MONITOR = runpy.run_path(str(Path(__file__).with_name('verify-input-monitor.py')))
INTERFACE = runpy.run_path(str(Path(__file__).with_name('verify-interface-selection.py')))


class Kernel:
    def __init__(self, path):
        self.cpu = x86.cpu_image(path)
        self.frames = []
        self.monitor_object = x86.CONTEXT + 0x586d8
        x86.write(self.cpu, self.monitor_object + 0xa50, 1)
        x86.write(self.cpu, self.monitor_object + 0xa3c, x86.PROPS)
        x86.write(self.cpu, self.monitor_object + 0xa40, x86.URB)
        x86.write(self.cpu, x86.PROPS + 0x1c, 1)
        # The final MIDI transport is memory-only; all callers/builders execute.
        self.cpu.hook_add(UC_HOOK_CODE, self.sent, begin=0x1eeb0, end=0x1eeb0)

    def sent(self, cpu, _address, _size, _context):
        x86.require(cpu.reg_read(registers.UC_X86_REG_ECX) == x86.URB and
                    x86.argument(cpu, 0) == 2, 'Unexpected x86 MIDI transport/channel.')
        x86.require(x86.read(cpu, x86.argument(cpu, 2)) == 24 and
                    x86.argument(cpu, 3) == self.monitor_object + 0xa54,
                    'Unexpected x86 MIDI length/completion pointer.')
        self.frames.append(bytes(cpu.mem_read(x86.argument(cpu, 1), 24)))
        x86.returned(cpu, 1, 4)

    def builder(self, channel, enabled, pan, volume):
        self.cpu.mem_write(x86.DATA, bytes.fromhex('f0411000004d') + bytes(18))
        x86.execute(self.cpu, 0x26a80, x86.DATA,
                    (127 if channel == -1 else channel, int(not enabled), pan, volume))
        return bytes(self.cpu.mem_read(x86.DATA, 24))

    def monitor(self, channel, enabled, output, pan, gain, *, dispatch):
        self.frames.clear()
        fields = struct.pack('<iiiii', channel, output, gain, int(enabled), pan)
        if dispatch:
            self.cpu.mem_write(x86.DATA, bytes(8) + fields)
            x86.write(self.cpu, x86.REQUEST + 0xc, x86.DATA)
            x86.write(self.cpu, x86.REQUEST + 0x60, x86.URB + 0x100)
            x86.write(self.cpu, x86.URB + 0x10c, 0x222144)
            result = x86.execute(self.cpu, 0x261b0, x86.CONTEXT, (x86.REQUEST, x86.PROPS + 0x300))
            x86.require(result == 0, 'Legacy monitor IOCTL dispatcher rejected the request.')
        else:
            self.cpu.mem_write(x86.DATA, fields)
            x86.execute(self.cpu, 0x268e0, self.monitor_object, (x86.DATA,))
        return list(self.frames)


def descriptors(legacy, current, configuration):
    # The parser result and monitor root share scratch memory in this harness.
    legacy.cpu.mem_write(x86.CONTEXT, bytes(0x5c))
    current.cpu.mem_write(INTERFACE['CONTEXT'], bytes(0x5c))
    legacy.cpu.mem_write(x86.DATA, configuration)
    legacy.cpu.mem_write(x86.PROPS, struct.pack('<III', x86.DATA, len(configuration), 0))
    x86.execute(legacy.cpu, 0x32a80, x86.CONTEXT, (x86.PROPS,))
    INTERFACE['windows_descriptor'](current.cpu, configuration)
    record = bytes(legacy.cpu.mem_read(x86.CONTEXT, 0x5c))
    x86.require(record == bytes(current.cpu.mem_read(INTERFACE['CONTEXT'], 0x5c)),
                'Legacy/current complete descriptor records differ.')
    # Restore the synthetic monitor owner after the descriptor vector reused PROPS.
    x86.write(legacy.cpu, x86.PROPS + 0x1c, 1)
    MONITOR['HELPERS']['write'](current.cpu, INTERFACE['CONTEXT'] + 0x40, INTERFACE['PROPS'], 'Q')
    return len(record)


def monitor_cases(legacy, current):
    pans = (0, 0xffff, 0x10000, 0x3fffffff, 0x40000000, 0x7ffeffff, 0x7fff0000, 0x7fffffff)
    gains = (0, 255, 256, 0x20000000, 0x7fffffff)
    count = 0
    for channel, enabled, output, pan, gain in itertools.product(range(-2, 13), (False, True), (0, 8), pans, gains):
        raw_pan = pan >> 16
        if raw_pan == 0x7fff:
            raw_pan = 0x8000
        expected = [MONITOR['expected_frame'](channel, enabled, raw_pan, gain >> 8)] if -1 <= channel < 10 else []
        arguments = (channel, enabled, output, pan, gain)
        for dispatch in (False, True):
            x86.require(legacy.monitor(*arguments, dispatch=dispatch) == expected,
                        f'Legacy monitor differs: {arguments}, dispatch={dispatch}')
        modern = [MONITOR['expected_frame'](channel, enabled, raw_pan, gain >> 8)] if -1 <= channel < 12 else []
        x86.require(current.monitor(*arguments) == modern, f'Current monitor differs: {arguments}')
        count += 1
    for ready, connected in ((0, 0), (0, 1), (1, 0), (1, 1)):
        x86.write(legacy.cpu, legacy.monitor_object + 0xa50, ready)
        x86.write(legacy.cpu, x86.PROPS + 0x1c, connected)
        expected = [MONITOR['expected_frame'](0, True, 0, 0)] if ready and connected else []
        for dispatch in (False, True):
            x86.require(legacy.monitor(0, True, 0, 0, 0, dispatch=dispatch) == expected,
                        'Legacy monitor transport gate differs.')
    return count


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('legacy_kernel', type=Path, help='32-bit RDWM1110.SYS from the archive legacy 1.5.2 branch')
    parser.add_argument('current_kernel', type=Path, help='Windows 1.5.3 x64 RDWM1110.SYS')
    parser.add_argument('encoder_test', type=Path, help='built input_monitor_test executable')
    parser.add_argument('descriptors', type=Path, help='cached native bcdDevice 0200 USB descriptors')
    args = parser.parse_args()
    legacy, current = Kernel(args.legacy_kernel), MONITOR['Kernel'](args.current_kernel)
    descriptor_bytes = descriptors(legacy, current, INTERFACE['fixture'](args.descriptors))
    print(json.dumps({'descriptor_record_bytes': descriptor_bytes,
                      'codec_frames': MONITOR['codec_cases'](legacy, args.encoder_test.resolve()),
                      'monitor_cases_per_path': monitor_cases(legacy, current),
                      'legacy_paths': ['conversion', 'IOCTL dispatcher'],
                      'legacy_input_indices': 'broadcast -1, individual 0..9',
                      'current_input_indices': 'broadcast -1, individual 0..11',
                      'transport_gate_cases_per_legacy_path': 4,
                      'host_dependencies': 'memory-only memset/memcpy and final MIDI transport',
                      'hardware_validation': False}))


if __name__ == '__main__':
    main()
