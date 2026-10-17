#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Compare the port-warning state table with actual execution of the macOS panel."""

import argparse
import hashlib
import itertools
import json
from pathlib import Path
import runpy

from unicorn import Uc, UC_ARCH_X86, UC_MODE_64, UC_HOOK_CODE
from unicorn import x86_const as registers

DISPLAY, DEVICE, FRAME, RUNTIME, STOP, STACK = (0x71000000, 0x72000000, 0x73000000,
                                              0x74000000, 0x75000000, 0x76000ff8)
FUNCTION = 0x100032042


class Reference:
    def __init__(self, executable):
        self.helpers = runpy.run_path(str(Path(__file__).with_name('emulate-bulk-preset.py')))
        image = executable.read_bytes()
        self.sha256 = hashlib.sha256(image).hexdigest()
        if self.sha256 != self.helpers['REFERENCE_SHA256']:
            raise ValueError('Unexpected original control-panel SHA-256.')
        self.cpu = Uc(UC_ARCH_X86, UC_MODE_64)
        self.helpers['load_image'](self.cpu, image)
        for address in (DISPLAY, DEVICE, FRAME, RUNTIME, STOP, STACK & ~0xfff):
            self.cpu.mem_map(address, 0x1000)
        self.pointer(DISPLAY + 0x48, DEVICE)
        self.pointer(DISPLAY + 0x18, FRAME + 0x300)
        self.pointer(FRAME + 8, FRAME + 0x400)
        self.pointer(FRAME + 0x10, FRAME + 0x100)
        self.pointer(FRAME + 0x100, FRAME + 0x200)
        self.pointer(FRAME + 0x258, RUNTIME + 0x100)
        self.pointer(0x100048108, RUNTIME)
        self.standard_selector = self.helpers['pointer'](self.cpu, 0x10005b070)
        self.bool_selector = self.helpers['pointer'](self.cpu, 0x10005b138)
        self.cpu.hook_add(UC_HOOK_CODE, self.runtime, begin=RUNTIME, end=RUNTIME)
        self.cpu.hook_add(UC_HOOK_CODE, self.frame, begin=0x10002fb4a, end=0x10002fb4a)
        self.cpu.hook_add(UC_HOOK_CODE, self.menu, begin=RUNTIME + 0x100, end=RUNTIME + 0x100)
        self.enabled = self.ready = self.shown = False

    def pointer(self, address, value):
        self.helpers['write_pointer'](self.cpu, address, value)

    def returned(self, engine, value):
        stack = engine.reg_read(registers.UC_X86_REG_RSP)
        address = self.helpers['pointer'](engine, stack)
        engine.reg_write(registers.UC_X86_REG_RAX, value)
        engine.reg_write(registers.UC_X86_REG_RSP, stack + 8)
        engine.reg_write(registers.UC_X86_REG_RIP, address)

    def runtime(self, engine, _address, _size, _context):
        selector = engine.reg_read(registers.UC_X86_REG_RSI)
        if selector == self.standard_selector:
            self.returned(engine, FRAME + 0x400)
        elif selector == self.bool_selector:
            if engine.reg_read(registers.UC_X86_REG_RDX) != 0x100055ed0:
                raise ValueError('Original requested an unexpected preference key.')
            self.returned(engine, int(not self.enabled))
        else:
            raise ValueError('Unexpected Objective-C selector.')

    def frame(self, engine, _address, _size, _context):
        self.returned(engine, FRAME if self.ready else 0)

    def menu(self, engine, _address, _size, _context):
        if engine.reg_read(registers.UC_X86_REG_RSI) != 0x1000046 or self.shown:
            raise ValueError('Unexpected original warning menu dispatch.')
        self.shown = True
        self.returned(engine, 0)

    def run(self, previous, pending, rate, enabled, ready):
        self.enabled, self.ready, self.shown = enabled, ready, False
        self.cpu.mem_write(DISPLAY + 0x54, previous.to_bytes(4, 'little'))
        self.cpu.mem_write(DISPLAY + 0x50, bytes([pending]))
        for offset in (0x9c4, 0x9c8):
            self.cpu.mem_write(DEVICE + offset, rate.to_bytes(4, 'little'))
        self.pointer(STACK, STOP)
        self.cpu.reg_write(registers.UC_X86_REG_RSP, STACK)
        self.cpu.reg_write(registers.UC_X86_REG_RDI, DISPLAY)
        self.cpu.emu_start(FUNCTION, STOP, timeout=1_000_000, count=1000)
        if self.cpu.reg_read(registers.UC_X86_REG_RIP) != STOP:
            raise RuntimeError('Original warning routine did not return.')
        cached = int.from_bytes(self.cpu.mem_read(DISPLAY + 0x54, 4), 'little')
        if cached != rate:
            raise RuntimeError('Original cached a different sample rate.')
        return [previous, pending, rate, enabled, ready, self.shown,
                bool(self.cpu.mem_read(DISPLAY + 0x50, 1)[0])]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('executable', type=Path)
    parser.add_argument('--golden', type=Path, default=Path('test/data/port-warning.json'))
    parser.add_argument('--write-golden', action='store_true')
    arguments = parser.parse_args()
    reference = Reference(arguments.executable)
    rates = (44100, 48000, 96000, 192000)
    cases = [reference.run(*case) for case in itertools.product((0, *rates), (False, True),
                                                               rates, (False, True), (False, True))]
    document = {'sha256': reference.sha256, 'function': hex(FUNCTION),
                'fields': ['previous', 'pending', 'rate', 'enabled', 'ready', 'show', 'pending_after'],
                'cases': cases}
    if arguments.write_golden:
        arguments.golden.write_text(json.dumps(document, separators=(',', ':')) + '\n')
    elif json.loads(arguments.golden.read_text()) != document:
        raise RuntimeError('Warning-state golden differs from original CPU execution.')
    print(f'PASS original port-warning transitions: {len(cases)} cases; no hardware access.')


if __name__ == '__main__':
    main()
