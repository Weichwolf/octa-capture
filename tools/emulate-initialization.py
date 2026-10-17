#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Verify original initialization selectors and Mixer A display frame offline."""

import argparse
from pathlib import Path
import runpy
import struct

from reference_midi import verify
from unicorn import Uc, UC_ARCH_X86, UC_MODE_64, UC_HOOK_CODE
from unicorn import x86_const as registers


def verify_menu(executable):
    reference = runpy.run_path(str(Path(__file__).with_name('emulate-bulk-preset.py')))
    cpu = Uc(UC_ARCH_X86, UC_MODE_64)
    reference['load_image'](cpu, executable.read_bytes())  # Hash already checked by verify.
    context, device, menu, callback, stop, stack = (
        0x71000000, 0x72000000, 0x73000000, 0x74000000, 0x75000000, 0x76000ff8)
    for address, length in ((context, 0x14000), (device, 0x1000), (menu, 0x1000),
                            (callback, 0x1000), (stop, 0x1000), (stack & ~0xfff, 0x1000)):
        cpu.mem_map(address, length)
    pointer = reference['write_pointer']
    pointer(cpu, context + 0x78, device)
    pointer(cpu, menu, menu + 0x100)
    pointer(cpu, 0x100048108, callback)
    tag_selector = reference['pointer'](cpu, 0x10005aec0)
    tag = 0

    def objective_c(engine, _address, _size, _context):
        if engine.reg_read(registers.UC_X86_REG_RSI) != tag_selector:
            raise RuntimeError('Unexpected original menu Objective-C call')
        position = engine.reg_read(registers.UC_X86_REG_RSP)
        returned = reference['pointer'](engine, position)
        engine.reg_write(registers.UC_X86_REG_RSP, position + 8)
        engine.reg_write(registers.UC_X86_REG_RAX, tag)
        engine.reg_write(registers.UC_X86_REG_RIP, returned)

    cpu.hook_add(UC_HOOK_CODE, objective_c, begin=callback, end=callback)
    initialization = range(0x100001e, 0x1000023)
    for rate in range(4):
        cpu.mem_write(device + 0x5c, struct.pack('<I', rate))
        for tag in [*initialization, 0x1000017, 0x100003c]:
            pointer(cpu, stack, stop)
            cpu.mem_write(menu + 8, b'\xa5')
            cpu.reg_write(registers.UC_X86_REG_RSP, stack)
            cpu.reg_write(registers.UC_X86_REG_RDI, context)
            cpu.reg_write(registers.UC_X86_REG_RSI, menu)
            cpu.emu_start(0x10000fa7c, stop, timeout=5_000_000, count=10000)
            enabled = tag in initialization or rate != 3
            if (cpu.reg_read(registers.UC_X86_REG_RIP) != stop
                    or bytes(cpu.mem_read(menu + 8, 1)) != bytes([enabled])):
                raise RuntimeError(f'Original menu availability mismatch: {rate=}, {tag=:#x}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('executable', type=Path, help='extracted macOS 1.5.9 x86_64 control panel')
    args = parser.parse_args()
    cases = []
    for selector in range(5):
        frame = bytes.fromhex('f0 41 10 00 00 4d 12 00 00 00 02')
        frame += bytes([selector, 0x7e - selector, 0xf7])
        cases.append((0x100026c28, selector, frame))
    cases.append((0x100002410, 0, bytes.fromhex('f0 41 10 00 00 4d 12 00 02 00 03 00 7b f7')))
    verify(args.executable, cases)
    verify_menu(args.executable)
    print('Original TxInit selectors 0..4 and TxDMix(0): all six frames verified; no hardware access.')
    print('Original Initialize menu enabled at all four rates; Patch Bay window disabled at 192 kHz.')


if __name__ == '__main__':
    main()
