#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Run the reference macOS bulk decoder and preset writer in an isolated CPU emulator."""

import argparse
import hashlib
from pathlib import Path
import struct

from unicorn import Uc, UcError, UC_ARCH_X86, UC_MODE_64, UC_HOOK_CODE
from unicorn import x86_const as registers

STATE = 0x71000000
FILE = 0x72000000
BULK = 0x73000000
MESSAGE = 0x74000000
STOP = 0x75000000
STACK = 0x7600fff8
HEADER = b'OCTA-CAPTURE Control Panel Data\0Ver100\0'
REFERENCE_SHA256 = '1ada3d6be71a6559ff84e7b21af1c964f0df2ce305c1f45fe0018cc148922092'


def pointer(cpu, address):
    return struct.unpack('<Q', cpu.mem_read(address, 8))[0]


def write_pointer(cpu, address, value):
    cpu.mem_write(address, struct.pack('<Q', value))


def load_image(cpu, image):
    commands = struct.unpack_from('<I', image, 16)[0]
    offset = 32
    for _ in range(commands):
        command, size = struct.unpack_from('<II', image, offset)
        if command == 0x19:  # LC_SEGMENT_64; the executable hash fixes the layout.
            name = image[offset + 8:offset + 24].split(b'\0', 1)[0]
            address, memory_size, file_offset, file_size = struct.unpack_from('<4Q', image, offset + 24)
            if name != b'__PAGEZERO' and memory_size:
                start = address & ~0xfff
                length = ((address + memory_size + 0xfff) & ~0xfff) - start
                cpu.mem_map(start, length)
                if file_size:
                    cpu.mem_write(address, image[file_offset:file_offset + file_size])
        offset += size


def emulate(image, frame):
    cpu = Uc(UC_ARCH_X86, UC_MODE_64)
    cpu.ctl_set_cpu_model(registers.UC_CPU_X86_HASWELL)
    load_image(cpu, image)
    for address, length in ((STATE, 0x4000), (FILE, 0x1000), (BULK, 0x1000),
                            (MESSAGE, 0x1000), (STOP, 0x1000), (0x76000000, 0x10000)):
        cpu.mem_map(address, length)
    # Emulated runtime: an open in-memory file and a stable stack guard.
    write_pointer(cpu, FILE + 8, FILE)
    write_pointer(cpu, 0x100048108, MESSAGE)
    write_pointer(cpu, 0x100048098, FILE + 0x80)
    write_pointer(cpu, FILE + 0x80, 0x6d5c4b3a)
    cpu.mem_write(BULK, frame)
    cpu.reg_write(registers.UC_X86_REG_CR4, cpu.reg_read(registers.UC_X86_REG_CR4) | 0x200)
    is_selector = pointer(cpu, 0x10005af08)
    write_selector = pointer(cpu, 0x10005b6c0)
    if bytes(cpu.mem_read(is_selector, 3)) != b'is\0' or \
            bytes(cpu.mem_read(write_selector, 18)) != b'writeData:ofSize:\0':
        raise RuntimeError('Unexpected Objective-C selectors')
    output = bytearray(HEADER)
    writes = 0

    def objective_c_call(engine, _address, _size, _context):
        nonlocal writes
        selector = engine.reg_read(registers.UC_X86_REG_RSI)
        if selector == write_selector:
            address = engine.reg_read(registers.UC_X86_REG_RDX)
            size = engine.reg_read(registers.UC_X86_REG_RCX)
            if size not in (1, 4) or writes >= 592:
                raise RuntimeError('Unexpected reference write')
            output.extend(engine.mem_read(address, size))
            writes += 1
        elif selector != is_selector:
            raise RuntimeError('Unexpected Objective-C call')
        stack = engine.reg_read(registers.UC_X86_REG_RSP)
        returned = pointer(engine, stack)
        engine.reg_write(registers.UC_X86_REG_RSP, stack + 8)
        engine.reg_write(registers.UC_X86_REG_RAX, 1)
        engine.reg_write(registers.UC_X86_REG_RIP, returned)

    cpu.hook_add(UC_HOOK_CODE, objective_c_call, begin=MESSAGE, end=MESSAGE)

    def call(address, argument0, argument1):
        write_pointer(cpu, STACK, STOP)
        cpu.reg_write(registers.UC_X86_REG_RSP, STACK)
        cpu.reg_write(registers.UC_X86_REG_RDI, argument0)
        cpu.reg_write(registers.UC_X86_REG_RSI, argument1)
        try:
            cpu.emu_start(address, STOP, timeout=5_000_000, count=2_000_000)
        except UcError as error:
            raise RuntimeError(f'Reference execution failed at {cpu.reg_read(registers.UC_X86_REG_RIP):#x}: {error}') from error
        if cpu.reg_read(registers.UC_X86_REG_RIP) != STOP:
            raise RuntimeError('Reference execution did not return within its instruction/time limit')

    call(0x100008afc, STATE, 0)  # CCplDev constructor.
    call(0x100009434, STATE, BULK)  # SetBlk, including its own checksum validation.
    if cpu.mem_read(STATE + 0x815, 1) != b'\1':
        raise RuntimeError('Reference did not accept the bulk state')
    call(0x10003a66e, FILE, STATE)  # SaveCpl.
    if cpu.reg_read(registers.UC_X86_REG_RAX) != 1 or len(output) != 1639 or writes != 592:
        raise RuntimeError('Unexpected reference preset layout')
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('executable', type=Path, help='extracted macOS 1.5.9 x86_64 control panel')
    parser.add_argument('bulk', type=Path, help='complete checksum-valid bulk DT1 .syx file')
    parser.add_argument('output', type=Path, help='destination .octa file')
    args = parser.parse_args()
    image = args.executable.read_bytes()
    if hashlib.sha256(image).hexdigest() != REFERENCE_SHA256:
        raise RuntimeError('Unexpected control-panel executable SHA-256')
    frame = args.bulk.read_bytes()
    if len(frame) != 1159 or not frame.startswith(bytes.fromhex('f0 41 10 00 00 4d 12 01 00 00 00')) or \
            frame[-1] != 0xf7 or any(byte >= 128 for byte in frame[7:-1]) or sum(frame[7:-1]) % 128:
        raise RuntimeError('Expected a checksum-valid OCTA-CAPTURE bulk DT1 frame')
    result = emulate(image, frame)
    args.output.write_bytes(result)
    print(f'Bulk/preset reference: {len(result)} bytes, 592 original writer calls; no device I/O.')


if __name__ == '__main__':
    main()
