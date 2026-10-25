#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Execute original sensitivity text/visibility methods with memory-only text hosts."""

import argparse
import itertools
import json
from pathlib import Path
import runpy
import struct

from unicorn import UC_HOOK_CODE
from unicorn import x86_const as registers

SOURCE = runpy.run_path(str(Path(__file__).with_name('verify-knob-display.py')))
HELPERS, WINDOWS_SHA, MAC_SHA = (SOURCE[name] for name in ('HELPERS', 'WINDOWS_SHA', 'MAC_SHA'))
CONTEXT, PROPS, DATA, STOP = (HELPERS[name] for name in ('CONTEXT', 'PROPS', 'DATA', 'STOP'))
require = SOURCE['require']


class Reference:
    def __init__(self, path, windows):
        self.windows = windows
        self.cpu = HELPERS['cpu_image'](path, WINDOWS_SHA if windows else MAC_SHA, windows=windows)
        self.current, self.written, self.strings, self.allocations = '', [], {}, 0
        if windows:
            self.write(CONTEXT + 0x100, DATA, 'Q')
            self.write(PROPS, PROPS + 0x100, 'Q')
            self.write(PROPS + 0x118, STOP + 0x100, 'Q')
            self.stub(STOP + 0x100, self.allocate_windows)
            self.stub(0x140030c68, lambda: PROPS)  # CString allocator host.
            self.stub(0x14000e4a0, self.copy_windows)
            self.stub(0x140035b5c, self.get_windows)
            self.stub(0x14003be10, self.set_windows)
        else:
            self.write(CONTEXT + 0x38, DATA, 'Q')
            self.write(CONTEXT + 8, PROPS, 'Q')
            self.cpu.mem_write(CONTEXT + 0x10, b'\x01\x01')
            self.stub(0x100014ee6, self.allocate_mac)
            self.stub(0x100014cf6, self.get_mac)
            self.stub(0x10003e3b2, self.compare_mac)
            self.stub(0x10003e34c, self.release_mac)
            self.write(0x100048108, STOP + 0x100, 'Q')
            self.stub(STOP + 0x100, self.set_mac)

    def argument(self, index):
        names = ((registers.UC_X86_REG_RCX, registers.UC_X86_REG_RDX, registers.UC_X86_REG_R8)
                 if self.windows else
                 (registers.UC_X86_REG_RDI, registers.UC_X86_REG_RSI, registers.UC_X86_REG_RDX))
        return self.cpu.reg_read(names[index])

    def read(self, address, kind='Q'):
        return struct.unpack('<' + kind, self.cpu.mem_read(address, struct.calcsize('<' + kind)))[0]

    def write(self, address, value, kind='Q'):
        self.cpu.mem_write(address, struct.pack('<' + kind, value))

    def text(self, address, wide=False):
        data, step = bytearray(), 2 if wide else 1
        for offset in range(0, 128, step):
            item = bytes(self.cpu.mem_read(address + offset, step))
            if item == bytes(step):
                return data.decode('utf-16-le' if wide else 'ascii')
            data += item
        raise RuntimeError('Unbounded original display string.')

    def stub(self, address, callback):
        def called(cpu, *_):
            value = callback()
            stack = cpu.reg_read(registers.UC_X86_REG_RSP)
            cpu.reg_write(registers.UC_X86_REG_RAX, (value or 0) & 0xffffffffffffffff)
            cpu.reg_write(registers.UC_X86_REG_RIP, self.read(stack))
            cpu.reg_write(registers.UC_X86_REG_RSP, stack + 8)
        self.cpu.hook_add(UC_HOOK_CODE, called, begin=address, end=address)

    def allocate_windows(self):
        require(self.argument(0) == PROPS, 'Unexpected CString allocator.')
        self.allocations += 1
        require(self.allocations <= 2, 'Unexpected CString allocation count.')
        address = DATA + 0x1000 + self.allocations * 0x100
        self.cpu.mem_write(address, bytes(0x100))
        # Keep modeled reference counts above the virtual host-free threshold.
        self.write(address + 0x10, 8, 'I')
        return address

    def store_windows(self, destination, text):
        address = self.read(destination)
        self.cpu.mem_write(address, text.encode('utf-16-le') + b'\0\0')

    def copy_windows(self):
        self.store_windows(self.argument(0), self.text(self.argument(1)))

    def get_windows(self):
        require(self.argument(0) == CONTEXT, 'Unexpected Windows text context.')
        self.store_windows(self.argument(1), self.current)

    def set_windows(self):
        require(self.argument(0) == CONTEXT, 'Unexpected Windows text update context.')
        self.current = self.text(self.argument(1), wide=True)
        self.written.append(self.current)
        return 1

    def allocate_mac(self):
        require(self.argument(2) == 0x8000100, 'Unexpected original text encoding.')
        self.allocations += 1
        handle = DATA + 0x2000 + self.allocations * 0x100
        self.strings[handle] = self.text(self.argument(1))
        self.write(self.argument(0), handle)
        return 1

    def get_mac(self):
        require(self.argument(0) == CONTEXT, 'Unexpected macOS text context.')
        address = DATA + 0x1800
        self.cpu.mem_write(address, self.current.encode('ascii') + b'\0')
        return address

    def compare_mac(self):
        require(self.argument(2) == 0, 'Unexpected CoreFoundation compare options.')
        return int(self.strings[self.argument(0)] != self.strings[self.argument(1)])

    def release_mac(self):
        del self.strings[self.argument(0)]

    def set_mac(self):
        require(self.argument(0) == PROPS, 'Unexpected Objective-C text host.')
        self.current = self.strings[self.argument(2)]
        self.written.append(self.current)

    def update(self, channel, value, previous):
        self.current, self.written, self.strings, self.allocations = previous, [], {}, 0
        self.write(CONTEXT + (0x150 if self.windows else 0x7c), channel, 'I')
        stride, offset, member = (0x64, 0x30, 0x2b8) if self.windows else (0x54, 0x28, 0xdc)
        self.write(DATA + channel // 2 * stride + channel % 2 * offset + member, value, 'I')
        HELPERS['execute'](self.cpu, 0x140023600 if self.windows else 0x100014b48,
                           (CONTEXT, 0, 0, 0), windows=self.windows)
        require(self.allocations == 2 and not self.strings, 'Text-host lifetime differs.')
        changed = self.cpu.reg_read(registers.UC_X86_REG_RAX) & 0xff
        return self.current, self.written, changed

    def shown(self, rate, preamp_view, channel):
        self.write(CONTEXT + (0x150 if self.windows else 0x7c), channel, 'I')
        self.write(DATA + (0x21c if self.windows else 0x5c), rate, 'I')
        self.write(DATA + (0xc38 if self.windows else 0x864), int(preamp_view), 'I' if self.windows else 'B')
        HELPERS['execute'](self.cpu, 0x14001a630 if self.windows else 0x100014b08,
                           (CONTEXT, 0, 0, 0), windows=self.windows)
        return self.cpu.reg_read(registers.UC_X86_REG_RAX) & 0xff


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('windows_gui', type=Path)
    parser.add_argument('--macos', type=Path, required=True)
    parser.add_argument('--save-vectors', type=Path)
    args = parser.parse_args()
    references = (Reference(args.windows_gui, True), Reference(args.macos, False))
    count, texts, visibility_cases = 0, {}, []
    for channel, value, same in itertools.product(range(8), (*range(101), 101, 255), (False, True)):
        text = f'{value / 2:4.1f}' if value <= 100 else '???'
        before = text if same else 'old'
        expected = text, [] if same else [text], int(not same)
        for reference in references:
            actual = reference.update(channel, value, before)
            require(actual == expected, 'Original sensitivity text update differs.')
            if channel == 0 and value <= 100:
                texts[value] = actual[0]
        count += 1
    visibility = 0
    for rate, preamp_view, channel in itertools.product(range(4), (False, True), (*range(9), 255)):
        expected = int(preamp_view and channel < (4 if rate == 3 else 8))
        for reference in references:
            require(reference.shown(rate, preamp_view, channel) == expected, 'Original sensitivity visibility differs.')
        visibility_cases.append({'rate_index': rate, 'preamp_view': preamp_view,
                                 'channel': channel, 'shown': bool(expected)})
        visibility += 1
    if args.save_vectors:
        args.save_vectors.write_text(json.dumps({'windows_sha256': WINDOWS_SHA, 'macos_sha256': MAC_SHA,
                                                'texts': [texts[index] for index in range(101)],
                                                'visibility': visibility_cases}, separators=(',', ':')) + '\n')
    print(f'PASS {count} original sensitivity text updates and {visibility} visibility cases per binary.')
    print('Text hosts, allocation and CF/Objective-C APIs are memory stubs; original selection/comparison and visibility execute. No painting or device I/O.')


if __name__ == '__main__':
    main()
