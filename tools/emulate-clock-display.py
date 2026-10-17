#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Verify the four reference clock display states and external-clock blinking."""

import argparse
import hashlib
from pathlib import Path
import runpy

from unicorn import Uc, UC_ARCH_X86, UC_MODE_64
from unicorn import x86_const as registers


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('executable', type=Path, help='extracted macOS 1.5.9 x86_64 control panel')
    args = parser.parse_args()
    reference = runpy.run_path(str(Path(__file__).with_name('emulate-bulk-preset.py')))
    image = args.executable.read_bytes()
    if hashlib.sha256(image).hexdigest() != reference['REFERENCE_SHA256']:
        raise RuntimeError('Unexpected control-panel executable SHA-256')
    cpu = Uc(UC_ARCH_X86, UC_MODE_64)
    reference['load_image'](cpu, image)
    display, device, bitmap, stop, stack = (0x71000000, 0x72000000, 0x73000000,
                                           0x74000000, 0x75000ff8)
    for address in (display, device, bitmap, stop, stack & ~0xfff):
        cpu.mem_map(address, 0x1000)
    write_pointer = reference['write_pointer']
    write_pointer(cpu, display + 0x48, device)
    write_pointer(cpu, display + 0x20, bitmap)
    cpu.mem_write(bitmap + 0x1c, (4).to_bytes(4, 'little'))
    for code in range(4):
        for blink in (0, 1):
            cpu.mem_write(device + 0x9cc, code.to_bytes(4, 'little'))
            cpu.mem_write(display + 0x54, bytes([blink]))
            write_pointer(cpu, stack, stop)
            cpu.reg_write(registers.UC_X86_REG_RSP, stack)
            cpu.reg_write(registers.UC_X86_REG_RDI, display)
            cpu.emu_start(0x10000c666, stop, timeout=1_000_000, count=1000)
            expected = code if code != 1 else (3 if blink == 0 else 1)
            if (cpu.reg_read(registers.UC_X86_REG_RIP) != stop or
                    cpu.reg_read(registers.UC_X86_REG_RAX) != expected):
                raise RuntimeError(f'Clock code {code}, blink phase {blink}: reference display differs')
    print('Original clock display verified: inactive/active INTERNAL/EXTERNAL; inactive EXTERNAL blinks 1/3.')


if __name__ == '__main__':
    main()
