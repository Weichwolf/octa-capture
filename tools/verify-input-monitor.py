#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Execute original Windows monitoring/clock paths offline, with host I/O stubbed."""

import argparse
import itertools
import json
from pathlib import Path
import runpy
import struct
import subprocess

from unicorn import UC_HOOK_CODE
from unicorn import x86_const as registers

HELPERS = runpy.run_path(str(Path(__file__).with_name('verify-pcm-peaks.py')))
ASIO_SHA256 = '637715bb042aa3971467921d73ab53323214b65a6092a1ceae41e105377a7862'
CONTEXT, PROPS, DATA, STACK, STOP = (HELPERS[name] for name in ('CONTEXT', 'PROPS', 'DATA', 'STACK', 'STOP'))
SUCCESS = 0x3f4847a0
NOT_PRESENT = 0xfffffc18


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def read(cpu, address, kind='I'):
    return struct.unpack('<' + kind, cpu.mem_read(address, struct.calcsize('<' + kind)))[0]


def returned(cpu, value):
    stack = cpu.reg_read(registers.UC_X86_REG_RSP)
    cpu.reg_write(registers.UC_X86_REG_RAX, value)
    cpu.reg_write(registers.UC_X86_REG_RIP, read(cpu, stack, 'Q'))
    cpu.reg_write(registers.UC_X86_REG_RSP, stack + 8)


def expected_frame(channel, enabled, pan, volume):
    values = bytes([int(not enabled)]) + bytes(int(digit, 16) for digit in f'{pan:04x}{volume:06x}')
    body = bytes([0, 6, 127 if channel == -1 else channel, 20]) + values
    return bytes.fromhex('f0411000004d12') + body + bytes([-sum(body) % 128, 247])


class Kernel:
    def __init__(self, path):
        self.cpu = HELPERS['cpu_image'](path, HELPERS['WINDOWS_SHA256'], windows=True)
        self.frames = []
        HELPERS['write'](self.cpu, CONTEXT + 0xa70, 1)
        HELPERS['write'](self.cpu, CONTEXT + 0xa48, PROPS, 'Q')
        HELPERS['write'](self.cpu, PROPS + 0x38, 1)
        # Capture the final MIDI send boundary; no Windows/USB/MIDI API is run.
        self.cpu.hook_add(UC_HOOK_CODE, self.sent, begin=0x14001b5e0, end=0x14001b5e0)
        self.model = 0x78000000
        self.cpu.mem_map(self.model, 0x80000)
        HELPERS['write'](self.cpu, CONTEXT + 0x40, PROPS, 'Q')
        HELPERS['write'](self.cpu, PROPS + 0x208, self.model, 'Q')
        HELPERS['write'](self.cpu, self.model + 0x778e8 + 0xa70, 1)
        HELPERS['write'](self.cpu, self.model + 0x778e8 + 0xa48, PROPS, 'Q')
        HELPERS['write'](self.cpu, 0x14002b0a0, STOP + 0x200, 'Q')
        self.cpu.hook_add(UC_HOOK_CODE, self.completed, begin=STOP + 0x200, end=STOP + 0x200)

    @staticmethod
    def completed(cpu, _address, _size, _context):
        returned(cpu, 0)

    def sent(self, cpu, _address, _size, _context):
        self.frames.append(bytes(cpu.mem_read(cpu.reg_read(registers.UC_X86_REG_R8), 24)))
        returned(cpu, 1)

    def monitor(self, channel, enabled, output, pan, gain):
        self.frames.clear()
        self.cpu.mem_write(DATA, struct.pack('<iiiii', channel, output, gain, int(enabled), pan))
        HELPERS['execute'](self.cpu, 0x140006300, (CONTEXT, DATA, 0, 0), windows=True)
        return list(self.frames)

    def builder(self, channel, enabled, pan, volume):
        self.cpu.mem_write(DATA, bytes.fromhex('f0411000004d') + bytes(18))
        HELPERS['write'](self.cpu, STACK + 0x28, volume)
        HELPERS['execute'](self.cpu, 0x140009b70,
                           (DATA, 127 if channel == -1 else channel, int(not enabled), pan), windows=True)
        return bytes(self.cpu.mem_read(DATA, 24))

    def dispatch(self, request):
        self.frames.clear()
        irp, stack, buffer = DATA + 0x1000, PROPS + 0x300, DATA + 0x2000
        self.cpu.mem_write(buffer, request)
        HELPERS['write'](self.cpu, irp + 0x18, buffer, 'Q')
        HELPERS['write'](self.cpu, irp + 0xb8, stack, 'Q')
        HELPERS['write'](self.cpu, stack + 8, 28)
        HELPERS['write'](self.cpu, stack + 0x18, 0x222144)
        HELPERS['execute'](self.cpu, 0x14000ab60, (CONTEXT, irp, 0, 0), windows=True)
        require(read(self.cpu, irp + 0x30) == 0, 'Windows device dispatcher rejected monitor IOCTL')
        return list(self.frames)


class Asio:
    def __init__(self, path):
        self.cpu = HELPERS['cpu_image'](path, ASIO_SHA256, windows=True)
        self.requests = []
        self.success = True
        # Resolve just DeviceIoControl to a memory-only test stub.
        HELPERS['write'](self.cpu, 0x1800180e0, STOP + 0x100, 'Q')
        self.cpu.hook_add(UC_HOOK_CODE, self.ioctl, begin=STOP + 0x100, end=STOP + 0x100)
        for slot in range(8):
            HELPERS['write'](self.cpu, CONTEXT + 0x230 + slot * 0x170, 0xffffffffffffffff, 'Q')
        HELPERS['write'](self.cpu, CONTEXT + 0x230 + 2 * 0x170, 0x55, 'Q')

    def ioctl(self, cpu, _address, _size, _context):
        stack = cpu.reg_read(registers.UC_X86_REG_RSP)
        pointer = cpu.reg_read(registers.UC_X86_REG_R8)
        require(cpu.reg_read(registers.UC_X86_REG_RCX) == 0x55, 'Wrong mapped ASIO device handle')
        require(cpu.reg_read(registers.UC_X86_REG_RDX) == 0x222144, 'Unexpected ASIO IOCTL')
        require(cpu.reg_read(registers.UC_X86_REG_R9) == 28 and read(cpu, stack + 0x30) == 28,
                'Unexpected monitor IOCTL size')
        require(read(cpu, stack + 0x28, 'Q') == pointer, 'Monitor input/output buffers differ')
        self.requests.append(bytes(cpu.mem_read(pointer, 28)))
        HELPERS['write'](cpu, read(cpu, stack + 0x38, 'Q'), 28)
        returned(cpu, int(self.success))

    def future(self, selector, channel, enabled, output, pan, gain, *, available=True, local_channel=None):
        self.requests.clear()
        HELPERS['write'](self.cpu, CONTEXT + 0x3ddc, int(available))
        if channel >= 0 and channel < 512:
            physical = channel if local_channel is None else local_channel
            self.cpu.mem_write(CONTEXT + 0xcb0 + channel * 8, struct.pack('<ii', 2, physical))
        self.cpu.mem_write(DATA, struct.pack('<iiiii', channel, output, gain, int(enabled), pan))
        HELPERS['execute'](self.cpu, 0x1800080b0, (CONTEXT, selector, DATA, 0), windows=True)
        return self.cpu.reg_read(registers.UC_X86_REG_RAX) & 0xffffffff

    def clock_sources(self):
        self.cpu.mem_write(DATA, bytes(64))
        HELPERS['write'](self.cpu, PROPS, 0)
        HELPERS['execute'](self.cpu, 0x180007310, (CONTEXT, DATA, PROPS, 0), windows=True)
        require(read(self.cpu, PROPS) == 1, 'ASIO clock-source count differs')
        require(bytes(self.cpu.mem_read(DATA, 23)) == struct.pack('<4I', 0, 0, 0, 1) + b'Device\0',
                'ASIO clock source is not the single current Device source')
        for selector in (0, 1, -1):
            HELPERS['execute'](self.cpu, 0x180007550, (CONTEXT, selector & 0xffffffff, 0, 0), windows=True)
            expected = 0 if selector == 0 else 0xfffffc1a
            require(self.cpu.reg_read(registers.UC_X86_REG_RAX) & 0xffffffff == expected,
                    'ASIO accepted an unsupported clock-source selector')


def codec_cases(kernel, executable):
    output = subprocess.run([str(executable), '--emit'], check=True, capture_output=True, text=True, timeout=20).stdout
    require(len(output) < 256 * 1024, 'Unexpected encoder output size')
    seen = set()
    for row in output.splitlines():
        channel, enabled, pan, volume, encoded = row.split('\t')
        key = tuple(map(int, (channel, enabled, pan, volume)))
        require(key not in seen, 'Duplicate encoder fixture')
        seen.add(key)
        original = kernel.builder(*key)
        require(original == bytes.fromhex(encoded) == expected_frame(*key), f'Composite frame mismatch: {key}')
    require(len(seen) == 910, 'Incomplete composite encoder coverage')
    return len(seen)


def host_cases(kernel, asio):
    pans = (0, 0xffff, 0x10000, 0x3fffffff, 0x40000000, 0x7ffeffff, 0x7fff0000, 0x7fffffff)
    gains = (0, 255, 256, 0x20000000, 0x7fffffff)
    count = 0
    for channel, enabled, output, pan, gain in itertools.product(range(-1, 12), (False, True), (0, 8), pans, gains):
        physical = (channel + 3) % 12 if channel >= 0 else -1
        require(asio.future(3, channel, enabled, output, pan, gain, local_channel=physical) == SUCCESS,
                'ASIO monitor request failed')
        require(len(asio.requests) == 1, 'ASIO monitor did not address exactly the mapped device')
        request = asio.requests[0]
        prefix = (0x8000 if enabled else 0) | (physical + 1)
        require(read_bytes(request, 0) == 0 and read_bytes(request, 4, 'H') == prefix,
                'Incorrect ASIO monitor control word')
        require(request[8:] == struct.pack('<iiiii', physical, output, gain, int(enabled), pan),
                'ASIO changed the monitor fields')
        raw_pan = pan >> 16
        if raw_pan == 0x7fff:
            raw_pan = 0x8000
        frames = kernel.dispatch(request)
        require(frames == [expected_frame(physical, enabled, raw_pan, gain >> 8)],
                'Kernel monitor conversion differs from original ASIO input')
        count += 1
    for channel in (-2, 12, 512):
        require(not kernel.monitor(channel, True, 0, 0, 0), 'Kernel emitted a frame for an invalid input')
    for available in (False, True):
        result = asio.future(9, 0, False, 0, 0, 0, available=available)
        require(result == (SUCCESS if available else NOT_PRESENT) and not asio.requests,
                'ASIO feature-gate mismatch')
    require(asio.future(3, 0, True, 0, 0, 0, available=False) == NOT_PRESENT and not asio.requests,
            'Disabled ASIO monitoring performed I/O')
    asio.success = False
    require(asio.future(3, 0, True, 0, 0, 0) == NOT_PRESENT, 'ASIO concealed a failed IOCTL')
    asio.clock_sources()
    return count


def read_bytes(data, offset, kind='I'):
    return struct.unpack_from('<' + kind, data, offset)[0]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('windows_asio', type=Path)
    parser.add_argument('windows_kernel', type=Path)
    parser.add_argument('encoder_test', type=Path)
    args = parser.parse_args()
    kernel, asio = Kernel(args.windows_kernel), Asio(args.windows_asio)
    print(json.dumps({'codec_frames': codec_cases(kernel, args.encoder_test.resolve()),
                      'asio_kernel_monitor_cases': host_cases(kernel, asio),
                      'clock_sources': ['Device'], 'host_dependencies': 'memory-only I/O stubs',
                      'hardware_validation': False}))


if __name__ == '__main__':
    main()
