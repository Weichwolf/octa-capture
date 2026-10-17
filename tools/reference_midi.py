# SPDX-License-Identifier: GPL-2.0-only
"""Execute bounded reference MIDI functions against an in-memory transport."""

import hashlib
from pathlib import Path
import runpy

from unicorn import Uc, UC_ARCH_X86, UC_MODE_64, UC_HOOK_CODE
from unicorn import x86_const as registers


def verify(executable, cases):
    reference = runpy.run_path(str(Path(__file__).with_name('emulate-bulk-preset.py')))
    image = executable.read_bytes()
    if hashlib.sha256(image).hexdigest() != reference['REFERENCE_SHA256']:
        raise RuntimeError('Unexpected control-panel executable SHA-256')
    cpu = Uc(UC_ARCH_X86, UC_MODE_64)
    cpu.ctl_set_cpu_model(registers.UC_CPU_X86_HASWELL)
    reference['load_image'](cpu, image)
    context, endpoint, transport, stop, stack = (0x71000000, 0x72000000, 0x73000000,
                                                0x74000000, 0x75000ff8)
    for address in (context, endpoint, transport, stop, stack & ~0xfff):
        cpu.mem_map(address, 0x1000)
    write_pointer = reference['write_pointer']
    write_pointer(cpu, context, 1)
    write_pointer(cpu, context + 8, endpoint)
    write_pointer(cpu, endpoint, endpoint + 0x100)
    write_pointer(cpu, endpoint + 0x140, transport)
    cpu.reg_write(registers.UC_X86_REG_CR4, cpu.reg_read(registers.UC_X86_REG_CR4) | 0x200)
    frames = []

    def capture(engine, _address, _size, _context):
        data = engine.reg_read(registers.UC_X86_REG_RSI)
        size = engine.reg_read(registers.UC_X86_REG_RDX)
        if size not in (14, 15):
            raise RuntimeError('Unexpected reference MIDI transmission length')
        frames.append(bytes(engine.mem_read(data, size)))
        position = engine.reg_read(registers.UC_X86_REG_RSP)
        returned = reference['pointer'](engine, position)
        engine.reg_write(registers.UC_X86_REG_RSP, position + 8)
        engine.reg_write(registers.UC_X86_REG_RAX, 1)
        engine.reg_write(registers.UC_X86_REG_RIP, returned)

    cpu.hook_add(UC_HOOK_CODE, capture, begin=transport, end=transport)
    for address, argument, expected in cases:
        frames.clear()
        write_pointer(cpu, stack, stop)
        cpu.reg_write(registers.UC_X86_REG_RSP, stack)
        cpu.reg_write(registers.UC_X86_REG_RDI, context)
        cpu.reg_write(registers.UC_X86_REG_RSI, argument)
        cpu.emu_start(address, stop, timeout=5_000_000, count=10000)
        if cpu.reg_read(registers.UC_X86_REG_RIP) != stop or frames != [expected]:
            raise RuntimeError(f'{address:#x}, argument {argument}: reference MIDI frame mismatch')
