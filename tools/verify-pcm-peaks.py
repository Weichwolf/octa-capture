#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Execute the original Windows/macOS PCM peak routines on synthetic audio."""

import argparse
import hashlib
import json
from pathlib import Path
import runpy
import struct
import subprocess

from unicorn import Uc, UC_ARCH_X86, UC_MODE_64
from unicorn import x86_const as registers

MAC_SHA256 = '63b3355f97bede9a4c7ff1b6565d86c39c84d97d88166ca4269528c8baf59427'
WINDOWS_SHA256 = '69030388d6b901cdbd1d72087b52f7dc5222a81ac49ffbc86bb49582c167299b'
CONTEXT, PROPS, DATA, REQUEST, URB, STACK, STOP = (
    0x71000000, 0x72000000, 0x73000000, 0x74000000, 0x75000000, 0x7600eff8, 0x77000000)


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def read_image(path, expected):
    image = path.read_bytes()
    require(hashlib.sha256(image).hexdigest() == expected, f'Unexpected reference SHA-256: {path}')
    return image


def load_pe(cpu, image):
    header = struct.unpack_from('<I', image, 0x3c)[0]
    count = struct.unpack_from('<H', image, header + 6)[0]
    optional = header + 24
    base = struct.unpack_from('<Q', image, optional + 24)[0]
    size = struct.unpack_from('<I', image, optional + 56)[0]
    cpu.mem_map(base, (size + 0xfff) & ~0xfff)
    sections = optional + struct.unpack_from('<H', image, header + 20)[0]
    for index in range(count):
        _name, _size, address, raw_size, offset = struct.unpack_from('<8sIIII', image, sections + index * 40)
        if raw_size:
            cpu.mem_write(base + address, image[offset:offset + raw_size])


def cpu_image(path, expected, windows=False):
    cpu = Uc(UC_ARCH_X86, UC_MODE_64)
    cpu.ctl_set_cpu_model(registers.UC_CPU_X86_HASWELL)
    image = read_image(path, expected)
    if windows:
        load_pe(cpu, image)
    else:
        source = runpy.run_path(str(Path(__file__).with_name('emulate-bulk-preset.py')))
        source['load_image'](cpu, image)
    for address, size in ((CONTEXT, 0x40000), (PROPS, 0x1000), (DATA, 0x10000),
                          (REQUEST, 0x1000), (URB, 0x1000), (STACK & ~0xffff, 0x10000), (STOP, 0x1000)):
        cpu.mem_map(address, size)
    cpu.reg_write(registers.UC_X86_REG_CR4, cpu.reg_read(registers.UC_X86_REG_CR4) | 0x200)
    return cpu


def write(cpu, address, value, code='I'):
    cpu.mem_write(address, struct.pack('<' + code, value))


def execute(cpu, address, arguments, windows=False):
    write(cpu, STACK, STOP, 'Q')
    cpu.reg_write(registers.UC_X86_REG_RSP, STACK)
    params = ((registers.UC_X86_REG_RCX, registers.UC_X86_REG_RDX, registers.UC_X86_REG_R8, registers.UC_X86_REG_R9)
              if windows else (registers.UC_X86_REG_RDI, registers.UC_X86_REG_RSI, registers.UC_X86_REG_RDX,
                               registers.UC_X86_REG_RCX))
    for register, value in zip(params, arguments, strict=True):
        cpu.reg_write(register, value)
    cpu.emu_start(address, STOP, timeout=2_000_000, count=200000)
    require(cpu.reg_read(registers.UC_X86_REG_RIP) == STOP, f'Reference did not return: {address:x}')


def windows_peaks(cpu, case):
    channels = case['channels']
    data = bytes.fromhex(case['bytes'])
    frames = len(data) // (channels * 3)
    rate = 192000 if channels == 4 else 48000
    cpu.mem_write(CONTEXT, bytes(0x40000))
    cpu.mem_write(PROPS, bytes(0x1000))
    cpu.mem_write(DATA, data)
    write(cpu, PROPS + 0x38, rate)
    if channels == 10:
        write(cpu, CONTEXT + 0xbb00, channels * 3)
        write(cpu, CONTEXT + 0xbb0c, 1)
        write(cpu, CONTEXT + 0xbc10, PROPS, 'Q')
        execute(cpu, 0x14001fce0, (CONTEXT, 0, DATA, frames), windows=True)
        offset = 0xbb10
    else:
        write(cpu, CONTEXT + 0x30e60, 2)
        write(cpu, CONTEXT + 0xdd2c, channels * 3)
        write(cpu, CONTEXT + 0xdd34, 1)
        write(cpu, CONTEXT + 0xde38, PROPS, 'Q')
        write(cpu, REQUEST, URB, 'Q')
        write(cpu, REQUEST + 32, channels * 3)
        write(cpu, URB + 0x28, DATA, 'Q')
        write(cpu, URB + 0x84, 1)
        write(cpu, URB + 0x8c, 0)
        write(cpu, URB + 0x90, len(data))
        execute(cpu, 0x140022c80, (CONTEXT, REQUEST, 0, 0), windows=True)
        offset = 0xddb8
    return list(struct.unpack(f'<{channels}I', cpu.mem_read(CONTEXT + offset, channels * 4)))


def mac_peaks(cpu, case):
    channels = case['channels']
    data = bytes.fromhex(case['bytes'])
    cpu.mem_write(CONTEXT, bytes(0x1000))
    cpu.mem_write(PROPS, bytes(0x1000))
    cpu.mem_write(DATA, data)
    write(cpu, PROPS, PROPS + 0x100, 'Q')
    write(cpu, PROPS + 0x114, 192000 if channels == 4 else 48000)
    cpu.mem_write(PROPS + 8, bytes([int(channels != 10)]))
    execute(cpu, 0xac24, (CONTEXT, PROPS, DATA, len(data)))
    return list(struct.unpack(f'<{channels}I', cpu.mem_read(CONTEXT, channels * 4)))


def expected_mac(case):
    peaks = [0] * case['channels']
    data = bytes.fromhex(case['bytes'])
    for index in range(0, len(data), 3):
        value = int.from_bytes(data[index:index + 3], 'little') << 8
        if value & 0x80000000:
            value ^= 0xffffffff
        channel = index // 3 % case['channels']
        peaks[channel] = max(peaks[channel], value)
    return peaks


def main():
    root = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mac_audio', type=Path)
    parser.add_argument('windows_kernel', type=Path)
    parser.add_argument('--test-binary', type=Path, default=root / 'build/strict/pcm_peaks_test')
    args = parser.parse_args()
    output = subprocess.run([str(args.test_binary), '--reference'], check=True,
                            capture_output=True, text=True, timeout=5)
    cases = json.loads(output.stdout)
    windows = cpu_image(args.windows_kernel, WINDOWS_SHA256, windows=True)
    mac = cpu_image(args.mac_audio, MAC_SHA256)
    differences = 0
    for case in cases:
        actual = windows_peaks(windows, case)
        require(actual == case['peaks'], f'Linux scanner differs from original Windows peaks: {case}, {actual}')
        actual = mac_peaks(mac, case)
        require(actual == expected_mac(case), f'Unexpected original macOS peak behavior: {case}, {actual}')
        differences += actual != case['peaks']
    print(f'PASS {len(cases)} synthetic PCM vectors: Linux scanner matches original Windows capture/playback routines.')
    print(f'PASS macOS execution: {differences} vectors differ from Windows because its scanner also rectifies negative samples.')


if __name__ == '__main__':
    main()
