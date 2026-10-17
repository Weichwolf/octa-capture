#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Execute original macOS DirectOut and serialize its resulting cache offline."""

import argparse
import hashlib
from pathlib import Path
import runpy
import struct

from unicorn import Uc, UcError, UC_ARCH_X86, UC_MODE_64, UC_HOOK_CODE
from unicorn import x86_const as registers


def emulate(reference, image, frame):
    cpu = Uc(UC_ARCH_X86, UC_MODE_64)
    cpu.ctl_set_cpu_model(registers.UC_CPU_X86_HASWELL)
    reference['load_image'](cpu, image)
    state, endpoint, bulk, transport, stop, stack, context = (
        0x71000000, 0x72000000, 0x73000000, 0x74000000, 0x75000000, 0x7600fff8, 0x78000000)
    for address, size in ((state, 0x4000), (endpoint, 0x1000), (bulk, 0x1000),
                          (transport, 0x1000), (stop, 0x1000), (stack & ~0xffff, 0x10000),
                          (context - 0x1000, 0x5000)):
        cpu.mem_map(address, size)
    write_pointer = reference['write_pointer']
    write_pointer(cpu, 0x100048098, endpoint + 0x80)
    write_pointer(cpu, endpoint + 0x80, 0x6d5c4b3a)
    cpu.mem_write(bulk, frame)
    cpu.reg_write(registers.UC_X86_REG_CR4, cpu.reg_read(registers.UC_X86_REG_CR4) | 0x200)
    frames = []

    def runtime(engine, address, _size, _context):
        if address == 0x10003e526:  # Original TxBlk's bzero.
            destination = engine.reg_read(registers.UC_X86_REG_RDI)
            size = engine.reg_read(registers.UC_X86_REG_RSI)
            if size != 1146:
                raise RuntimeError('Unexpected reference bzero length')
            engine.mem_write(destination, bytes(size))
        else:
            size = engine.reg_read(registers.UC_X86_REG_RDX)
            data = engine.reg_read(registers.UC_X86_REG_RSI)
            if size not in (14, 17, 19, 1159) or len(frames) >= 207:
                raise RuntimeError('Unexpected reference MIDI transmission')
            packet = bytes(engine.mem_read(data, size))
            if (not packet.startswith(bytes.fromhex('f0 41 10 00 00 4d 12')) or
                    packet[-1] != 0xf7 or any(byte >= 128 for byte in packet[7:-1]) or
                    sum(packet[7:-1]) % 128):
                raise RuntimeError('Reference produced malformed MIDI')
            frames.append(packet)
        position = engine.reg_read(registers.UC_X86_REG_RSP)
        returned = reference['pointer'](engine, position)
        engine.reg_write(registers.UC_X86_REG_RSP, position + 8)
        engine.reg_write(registers.UC_X86_REG_RAX, 1)
        engine.reg_write(registers.UC_X86_REG_RIP, returned)

    cpu.hook_add(UC_HOOK_CODE, runtime, begin=transport, end=transport)
    cpu.hook_add(UC_HOOK_CODE, runtime, begin=0x10003e526, end=0x10003e526)

    def call(address, argument0, argument1=0):
        write_pointer(cpu, stack, stop)
        cpu.reg_write(registers.UC_X86_REG_RSP, stack)
        cpu.reg_write(registers.UC_X86_REG_RDI, argument0)
        cpu.reg_write(registers.UC_X86_REG_RSI, argument1)
        try:
            cpu.emu_start(address, stop, timeout=5_000_000, count=2_000_000)
        except UcError as error:
            raise RuntimeError(f'Reference failed at {cpu.reg_read(registers.UC_X86_REG_RIP):#x}: {error}') from error
        if cpu.reg_read(registers.UC_X86_REG_RIP) != stop:
            raise RuntimeError('Reference instruction/time limit exceeded')

    call(0x100008afc, state)  # CCplDev constructor.
    call(0x100009434, state, bulk)  # SetBlk.
    if cpu.mem_read(state + 0x815, 1) != b'\1':
        raise RuntimeError('Reference rejected the input state')
    cpu.mem_write(state + 0xc, struct.pack('<I', frame[13]))
    cpu.mem_write(state + 0x10, bytes([frame[14] & 1]))
    cpu.mem_write(state + 0x14, struct.pack('<I', frame[15]))
    cpu.mem_write(context + 0x48, bytes(cpu.mem_read(state, 0x816)))
    write_pointer(cpu, context + 8, 1)
    write_pointer(cpu, context + 0x10, endpoint + 0x200)
    write_pointer(cpu, endpoint + 0x200, endpoint + 0x300)
    write_pointer(cpu, endpoint + 0x340, transport)
    call(0x10002a640, context - 8)  # CUiDev::DirectOut.
    if len(frames) != 206:
        raise RuntimeError('Reference did not produce 206 DirectOut frames')
    trace = b''.join(frames)
    call(0x10002a9b0, context)  # CUiCplDev::TxBlk of the final cached state.
    if len(frames) != 207 or len(frames[-1]) != 1159:
        raise RuntimeError('Reference did not serialize its final bulk state')
    return trace, frames[-1]


def verify_link_variants(reference, image, frame, trace, target):
    """Original command must preserve I/O and playback-master links in all four patterns."""
    for pattern in range(4):
        payload = bytearray(frame[11:-2])
        payload[0x58:0x60] = bytes([15 if pattern & 1 else 0, pattern & 1] * 4)
        payload[0x404:0x408] = bytes([pattern & 1] * 4)
        payload[0x43e:0x442] = bytes([pattern >> 1] * 4)
        payload[0x446:0x44a] = bytes([(pattern >> 1) ^ 1] * 4)

        def encode(body):
            address = bytes([1, 0, 0, 0])
            return frame[:7] + address + body + bytes([-sum(address + body) % 128, 0xf7])

        expected = bytearray(target[11:-2])
        expected[0x43e:0x442] = payload[0x43e:0x442]
        expected[0x446:0x44a] = payload[0x446:0x44a]
        variant_trace, variant_target = emulate(reference, image, encode(payload))
        if variant_trace != trace or variant_target != encode(expected):
            raise RuntimeError(f'Unexpected original DirectOut link behavior for pattern {pattern}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('executable', type=Path)
    parser.add_argument('current', type=Path, help='complete bulk DT1 .syx input')
    parser.add_argument('trace', type=Path, help='output: concatenated DirectOut .syx frames')
    parser.add_argument('target', type=Path, help='output: complete cached bulk DT1 .syx')
    args = parser.parse_args()
    reference = runpy.run_path(str(Path(__file__).with_name('emulate-bulk-preset.py')))
    image = args.executable.read_bytes()
    if hashlib.sha256(image).hexdigest() != reference['REFERENCE_SHA256']:
        raise RuntimeError('Unexpected control-panel executable SHA-256')
    frame = args.current.read_bytes()
    if (len(frame) != 1159 or not frame.startswith(bytes.fromhex('f0 41 10 00 00 4d 12 01 00 00 00')) or
            frame[-1] != 0xf7 or any(byte >= 128 for byte in frame[7:-1]) or sum(frame[7:-1]) % 128):
        raise RuntimeError('Expected a complete checksum-valid bulk DT1 input')
    trace, target = emulate(reference, image, frame)
    verify_link_variants(reference, image, frame, trace, target)
    args.trace.write_bytes(trace)
    args.target.write_bytes(target)
    print(f'Original DirectOut: 206 frames ({len(trace)} bytes), final bulk {len(target)} bytes; '
          'four additional link patterns verified; no hardware access.')


if __name__ == '__main__':
    main()
