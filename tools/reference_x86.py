# SPDX-License-Identifier: GPL-2.0-only
"""Isolated x86 execution of the legacy Windows reference shipped in the archive."""

import hashlib
from pathlib import Path
import struct

from unicorn import Uc, UC_ARCH_X86, UC_MODE_32, UC_HOOK_CODE
from unicorn import x86_const as registers

DRIVER_SHA256 = 'a30af44f926f3bd2da3a3d82bc8c8a3464cce5460def9e8d96a6f031e7be8b1d'
CONTEXT, PROPS, DATA, REQUEST, URB, STACK, STOP = (
    0x71000000, 0x72000000, 0x73000000, 0x74000000, 0x75000000, 0x7600effc, 0x77000000)


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def read(cpu, address, kind='I'):
    return struct.unpack('<' + kind, cpu.mem_read(address, struct.calcsize('<' + kind)))[0]


def write(cpu, address, value, kind='I'):
    cpu.mem_write(address, struct.pack('<' + kind, value))


def cpu_image(path: Path):
    image = path.read_bytes()
    require(hashlib.sha256(image).hexdigest() == DRIVER_SHA256, 'Unexpected x86 reference SHA-256.')
    header = struct.unpack_from('<I', image, 0x3c)[0]
    require(image[header:header + 4] == b'PE\0\0' and
            struct.unpack_from('<H', image, header + 4)[0] == 0x14c, 'Expected an x86 PE image.')
    optional = header + 24
    require(struct.unpack_from('<H', image, optional)[0] == 0x10b, 'Expected PE32 optional header.')
    base = struct.unpack_from('<I', image, optional + 28)[0]
    size, headers = struct.unpack_from('<II', image, optional + 56)
    cpu = Uc(UC_ARCH_X86, UC_MODE_32)
    cpu.ctl_set_cpu_model(registers.UC_CPU_X86_HASWELL)
    cpu.mem_map(base, (size + 4095) & ~4095)
    cpu.mem_write(base, image[:headers])
    sections = optional + struct.unpack_from('<H', image, header + 20)[0]
    for index in range(struct.unpack_from('<H', image, header + 6)[0]):
        _, _, address, length, offset = struct.unpack_from('<8sIIII', image, sections + index * 40)
        if length:
            cpu.mem_write(base + address, image[offset:offset + length])
    for address, length in ((CONTEXT, 0x100000), (PROPS, 0x1000), (DATA, 0x10000),
                            (REQUEST, 0x1000), (URB, 0x1000), (STACK & ~0xffff, 0x10000), (STOP, 0x1000)):
        cpu.mem_map(address, length)
    cpu.reg_write(registers.UC_X86_REG_CR4, cpu.reg_read(registers.UC_X86_REG_CR4) | 0x200)
    cpu.reg_write(registers.UC_X86_REG_FPCW, 0x37f)
    memory_imports(cpu, base)
    return cpu


def imports(cpu, base=0x10000):
    header = base + read(cpu, base + 0x3c)
    descriptor = base + read(cpu, header + 24 + 104)
    result = {}
    while read(cpu, descriptor):
        lookup, _, _, _, target = struct.unpack('<5I', cpu.mem_read(descriptor, 20))
        index = 0
        while pointer := read(cpu, base + lookup + index * 4):
            require(not pointer >> 31, 'Ordinal import needs an explicit x86 audit.')
            name = bytes(cpu.mem_read(base + pointer + 2, 128)).split(b'\0', 1)[0].decode('ascii')
            result[name] = base + target + index * 4
            index += 1
        descriptor += 20
    return result


def argument(cpu, index):
    return read(cpu, cpu.reg_read(registers.UC_X86_REG_ESP) + 4 + index * 4)


def memory_imports(cpu, base):
    table = imports(cpu, base)
    def callback(name):
        def called(cpu, *_):
            destination, source, count = (argument(cpu, index) for index in range(3))
            require(count <= 0x100000, 'Unexpected memory import length.')
            data = bytes([source & 255]) * count if name == 'memset' else bytes(cpu.mem_read(source, count))
            if data:
                cpu.mem_write(destination, data)
            returned(cpu, destination)
        return called
    for index, name in enumerate(('memset', 'memcpy')):
        address = STOP + 0x100 + index * 16
        write(cpu, table[name], address)
        cpu.hook_add(UC_HOOK_CODE, callback(name), begin=address, end=address)


def execute(cpu, address, this, arguments=(), callee_cleanup=True):
    cpu.mem_write(STACK, struct.pack('<' + 'I' * (len(arguments) + 1), STOP, *arguments))
    cpu.reg_write(registers.UC_X86_REG_ESP, STACK)
    cpu.reg_write(registers.UC_X86_REG_ECX, this)
    preserved = (registers.UC_X86_REG_EBX, registers.UC_X86_REG_ESI,
                 registers.UC_X86_REG_EDI, registers.UC_X86_REG_EBP)
    for index, register in enumerate(preserved):
        cpu.reg_write(register, 0x11223300 + index)
    cpu.emu_start(address, STOP, timeout=2_000_000, count=500000)
    require(cpu.reg_read(registers.UC_X86_REG_EIP) == STOP, f'x86 reference did not return: {address:x}')
    expected_stack = STACK + 4 + (len(arguments) * 4 if callee_cleanup else 0)
    require(cpu.reg_read(registers.UC_X86_REG_ESP) == expected_stack, f'x86 stack cleanup differs: {address:x}')
    require(all(cpu.reg_read(register) == 0x11223300 + index for index, register in enumerate(preserved)),
            f'x86 callee-saved registers changed: {address:x}')
    return cpu.reg_read(registers.UC_X86_REG_EAX)


def returned(cpu, value, arguments=0):
    stack = cpu.reg_read(registers.UC_X86_REG_ESP)
    cpu.reg_write(registers.UC_X86_REG_EAX, value & 0xffffffff)
    cpu.reg_write(registers.UC_X86_REG_EIP, read(cpu, stack))
    cpu.reg_write(registers.UC_X86_REG_ESP, stack + 4 + arguments * 4)
