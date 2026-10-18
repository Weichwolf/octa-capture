#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Execute original Auto-Sens selection handlers offline, without device I/O."""

import argparse
from pathlib import Path
import runpy
import struct

from unicorn import UC_HOOK_CODE
from unicorn import x86_const as registers

HELPERS = runpy.run_path(str(Path(__file__).with_name('verify-pcm-peaks.py')))
MAC = runpy.run_path(str(Path(__file__).with_name('emulate-bulk-preset.py')))
CONTEXT, PROPS, DATA, STOP = (HELPERS[name] for name in ('CONTEXT', 'PROPS', 'DATA', 'STOP'))
GUI_SHA256 = '634c140bc4e49b0d65c70d5156be19573ad3d440ac5ea24e24d3ad59974da406'


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def returned(cpu, value):
    stack = cpu.reg_read(registers.UC_X86_REG_RSP)
    address = struct.unpack('<Q', cpu.mem_read(stack, 8))[0]
    cpu.reg_write(registers.UC_X86_REG_RAX, value)
    cpu.reg_write(registers.UC_X86_REG_RSP, stack + 8)
    cpu.reg_write(registers.UC_X86_REG_RIP, address)


class Windows:
    def __init__(self, path):
        self.cpu = HELPERS['cpu_image'](path, GUI_SHA256, windows=True)
        self.enabled = None
        self.stub(0x14003919c, lambda _cpu: 1)  # MFC UpdateData; controls supplied in memory.
        self.stub(0x14003b928, self.control)
        self.stub(0x14003b8b8, self.enable)

    def stub(self, address, callback):
        def hook(cpu, _address, _size, _context):
            returned(cpu, callback(cpu))
        self.cpu.hook_add(UC_HOOK_CODE, hook, begin=address, end=address)

    @staticmethod
    def control(cpu):
        require(cpu.reg_read(registers.UC_X86_REG_RCX) == CONTEXT, 'Unexpected dialog')
        require(cpu.reg_read(registers.UC_X86_REG_RDX) == 1, 'Unexpected control ID')
        return DATA

    def enable(self, cpu):
        require(cpu.reg_read(registers.UC_X86_REG_RCX) == DATA, 'Unexpected Start control')
        require(self.enabled is None, 'Start was updated more than once')
        self.enabled = bool(cpu.reg_read(registers.UC_X86_REG_RDX))
        return 1

    def run(self, action, rate, mask):
        self.enabled = None
        self.cpu.mem_write(CONTEXT, bytes(0x1000))
        HELPERS['write'](self.cpu, CONTEXT + 0x148, PROPS, 'Q')
        HELPERS['write'](self.cpu, PROPS + 0x21c, rate)
        values = tuple((mask >> channel) & 1 for channel in range(8))
        self.cpu.mem_write(CONTEXT + 0x158, struct.pack('<8I', *values))
        function = {'change': 0x140005710, 'all': 0x140005630, 'none': 0x140005ea0}[action]
        HELPERS['execute'](self.cpu, function, (CONTEXT, 0, 0, 0), windows=True)
        checked = struct.unpack('<8I', self.cpu.mem_read(CONTEXT + 0x158, 32))
        require(all(value in (0, 1) for value in checked), 'Non-boolean Windows selection')
        return sum(value << channel for channel, value in enumerate(checked)), self.enabled


class Mac:
    def __init__(self, path):
        self.cpu = HELPERS['cpu_image'](path, MAC['REFERENCE_SHA256'])
        self.selectors = {name: MAC['pointer'](self.cpu, address) for name, address in (
            ('set', 0x10005b060), ('get', 0x10005b200),
            ('control', 0x10005b1d8), ('enable', 0x10005ae50))}
        HELPERS['write'](self.cpu, 0x100048108, STOP + 0x100, 'Q')
        self.cpu.hook_add(UC_HOOK_CODE, self.runtime, begin=STOP + 0x100, end=STOP + 0x100)
        self.checked = [False] * 8
        self.enabled = None

    def runtime(self, cpu, _address, _size, _context):
        target = cpu.reg_read(registers.UC_X86_REG_RDI)
        selector = cpu.reg_read(registers.UC_X86_REG_RSI)
        value = cpu.reg_read(registers.UC_X86_REG_RDX)
        if selector == self.selectors['control']:
            require(target == DATA + 0x200 and value == 1, 'Unexpected Cocoa control request')
            result = DATA + 0x300
        elif selector == self.selectors['enable']:
            require(target == DATA + 0x300 and self.enabled is None, 'Unexpected Cocoa Start update')
            self.enabled = bool(value)
            result = 0
        else:
            index, remainder = divmod(target - DATA, 16)
            require(remainder == 0 and 0 <= index < 8, 'Unexpected Cocoa checkbox')
            if selector == self.selectors['set']:
                require(value in (0, 1), 'Non-boolean Cocoa checkbox')
                self.checked[index] = bool(value)
                result = 0
            else:
                require(selector == self.selectors['get'], 'Unexpected Cocoa selector')
                result = int(self.checked[index])
        returned(cpu, result)

    def run(self, action, rate, mask):
        self.enabled = None
        self.checked = [bool(mask & (1 << channel)) for channel in range(8)]
        self.cpu.mem_write(CONTEXT, bytes(0x1000))
        HELPERS['write'](self.cpu, CONTEXT + 8, DATA + 0x200, 'Q')
        HELPERS['write'](self.cpu, CONTEXT + 0x28, PROPS, 'Q')
        HELPERS['write'](self.cpu, PROPS + 0x5c, rate)
        for channel in range(8):
            HELPERS['write'](self.cpu, CONTEXT + 0x48 + channel * 16, DATA + channel * 16, 'Q')
        function = {'change': 0x100028380, 'all': 0x1000289ba, 'none': 0x100028a40}[action]
        HELPERS['execute'](self.cpu, function, (CONTEXT, 0, 0, 0))
        flags = bytes(self.cpu.mem_read(CONTEXT + 0x37, 8))
        require(list(flags) == list(map(int, self.checked)), 'Cocoa/model selection differs')
        return sum(value << channel for channel, value in enumerate(flags)), self.enabled


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('windows_gui', type=Path)
    parser.add_argument('mac_gui', type=Path)
    args = parser.parse_args()
    references = (Windows(args.windows_gui), Mac(args.mac_gui))
    cases = 0
    for rate in range(4):
        for mask in range(256):
            for action in ('change', 'all', 'none'):
                available = 15 if rate == 3 else 255
                expected = {'change': mask, 'all': available, 'none': 0}[action]
                for reference in references:
                    actual = reference.run(action, rate, mask)
                    require(actual == (expected, bool(expected)),
                            f'{type(reference).__name__} differs: {action}, rate index {rate}, mask {mask}: {actual}')
                cases += 1
    print(f'PASS original Windows/macOS Auto-Sens selection and Start gating: {cases} cases per binary; '
          'host controls stubbed, no device access.')


if __name__ == '__main__':
    main()
