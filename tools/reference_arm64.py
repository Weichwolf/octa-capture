# SPDX-License-Identifier: GPL-2.0-only
"""Execute pinned macOS ARM64 MIDI transmitters with an in-memory transport."""

import hashlib
from pathlib import Path
import runpy

REFERENCE_SHA256 = 'ba2a1b47aa7fdf199542318ca6181fcc0a738a1205ca04849d5831cbec00a97d'


def verify(executable, cases):
    from unicorn import Uc, UC_ARCH_ARM64, UC_MODE_ARM, UC_HOOK_CODE
    from unicorn import arm64_const as registers
    image = executable.read_bytes()
    if hashlib.sha256(image).hexdigest() != REFERENCE_SHA256:
        raise RuntimeError('Unexpected ARM64 control-panel executable SHA-256')
    reference = runpy.run_path(str(Path(__file__).with_name('emulate-bulk-preset.py')))
    cpu = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
    reference['load_image'](cpu, image)
    context, endpoint, transport, stop, stack = (0x71000000, 0x72000000, 0x73000000,
                                                0x74000000, 0x75000000)
    for address in (context, endpoint, transport, stop):
        cpu.mem_map(address, 0x1000)
    cpu.mem_map(stack, 0x4000)
    write_pointer = reference['write_pointer']
    write_pointer(cpu, context, 1)
    write_pointer(cpu, context + 8, endpoint)
    write_pointer(cpu, endpoint, endpoint + 0x100)
    write_pointer(cpu, endpoint + 0x140, transport)
    cpu.reg_write(registers.UC_ARM64_REG_CPACR_EL1, 3 << 20)
    frames = []

    def capture(engine, _address, _size, _context):
        data = engine.reg_read(registers.UC_ARM64_REG_X1)
        size = engine.reg_read(registers.UC_ARM64_REG_X2)
        if engine.reg_read(registers.UC_ARM64_REG_X0) != endpoint or size not in (14, 15) or frames:
            raise RuntimeError('Unexpected ARM64 reference MIDI transmission')
        if not stack <= data <= stack + 0x4000 - size:
            raise RuntimeError('ARM64 reference MIDI data is outside its stack')
        frames.append(bytes(engine.mem_read(data, size)))
        engine.reg_write(registers.UC_ARM64_REG_X0, 1)
        engine.reg_write(registers.UC_ARM64_REG_PC, engine.reg_read(registers.UC_ARM64_REG_LR))

    cpu.hook_add(UC_HOOK_CODE, capture, begin=transport, end=transport)
    for address, argument, expected in cases:
        frames.clear()
        arguments = argument if isinstance(argument, tuple) else (argument,)
        if len(arguments) > 4:
            raise ValueError('At most four ARM64 reference arguments are supported')
        cpu.mem_write(stack, bytes(0x4000))
        cpu.reg_write(registers.UC_ARM64_REG_SP, stack + 0x4000)
        cpu.reg_write(registers.UC_ARM64_REG_LR, stop)
        cpu.reg_write(registers.UC_ARM64_REG_X0, context)
        for index, value in enumerate(arguments + (0,) * (4 - len(arguments)), 1):
            cpu.reg_write(getattr(registers, f'UC_ARM64_REG_X{index}'), value)
        cpu.emu_start(address, stop, timeout=5_000_000, count=10000)
        if cpu.reg_read(registers.UC_ARM64_REG_PC) != stop or frames != [expected] or \
                cpu.reg_read(registers.UC_ARM64_REG_X0) != 1:
            raise RuntimeError(f'{address:#x}, arguments {argument}: ARM64 reference MIDI frame mismatch')
