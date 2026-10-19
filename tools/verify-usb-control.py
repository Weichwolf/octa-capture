#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Execute original macOS clock/DIN control requests with memory-only host APIs."""

import argparse
import json
from pathlib import Path
import runpy
import struct

from unicorn import UC_HOOK_CODE
from unicorn import arm64_const as arm_registers
from unicorn import x86_const as registers

REFERENCE = runpy.run_path(str(Path(__file__).with_name('verify-interface-selection.py')))
HELPERS = REFERENCE['HELPERS']
CONTEXT, PROPS, DATA, STACK, STOP = (HELPERS[name] for name in ('CONTEXT', 'PROPS', 'DATA', 'STACK', 'STOP'))
require, read, write = REFERENCE['require'], REFERENCE['read'], HELPERS['write']
RATES = (44100, 48000, 96000, 192000)


class MacUsb(REFERENCE['MacReference']):
    def __init__(self, path, architecture):
        super().__init__(path, architecture)
        self.addresses = dict(zip(('din_get', 'din_set', 'stat', 'ready', 'set_fs', 'send_fs', 'request'),
                                 (0x1ae90, 0x1afac, 0x1ad24, 0x1adf4, 0x1af24, 0x1b098, 0x1abac)
                                 if self.arm else
                                 (0x17606, 0x17704, 0x174bc, 0x1757e, 0x1768e, 0x177c8, 0x1732c), strict=True))
        guard, lock, unlock, copy, sleep = ((0x200c8, 0x5444, 0x5490, 0x1c3e8, 0x1c574) if self.arm else
                                          (0x1c0d8, 0xcb68, 0xa904, 0x18670, 0x18724))
        selector_base, class_pointer = (0x24490, 0x24560) if self.arm else (0x20478, 0x20548)
        self.selectors = {read(self.cpu, selector_base + index * 8, 'Q'): name for index, name in
                          enumerate(('allocate', 'send_data', 'copy', 'send_empty'))}
        require(len(self.selectors) == 4, 'Unexpected original Objective-C selector references.')
        write(self.cpu, class_pointer, PROPS + 0x300, 'Q')
        write(self.cpu, guard, PROPS, 'Q')
        write(self.cpu, PROPS, 0x1122334455667788, 'Q')
        self.stub(lock, self.locked)
        self.stub(unlock, self.unlocked)
        self.stub(copy, self.copied)
        self.stub(sleep, self.slept)
        if self.arm:
            objc = 0x1c40c
        else:
            objc = STOP + 0x200
            write(self.cpu, 0x1c130, objc, 'Q')
        self.stub(objc, self.message)
        self.reset()

    def arg(self, index):
        if self.arm:
            return self.cpu.reg_read(getattr(arm_registers, f'UC_ARM64_REG_X{index}'))
        return self.cpu.reg_read((registers.UC_X86_REG_RDI, registers.UC_X86_REG_RSI,
                                  registers.UC_X86_REG_RDX, registers.UC_X86_REG_RCX,
                                  registers.UC_X86_REG_R8, registers.UC_X86_REG_R9)[index])

    def stub(self, address, callback):
        self.cpu.hook_add(UC_HOOK_CODE, lambda *_: self.returned(callback()), begin=address, end=address)

    def reset(self, **settings):
        self.ok, self.allocate, self.acquire = True, True, True
        self.actual, self.reply, self.flags = None, b'\x01', [0]
        self.rate = 48000
        self.requests, self.locks, self.sleeps = [], [], []
        self.buffer = bytearray()
        self.cpu.mem_write(DATA + 0xe000, bytes([0xa5]) * 32)
        for name, value in settings.items():
            setattr(self, name, value)

    def locked(self):
        require(self.arg(0) == CONTEXT + (0x200 if self.arm else 0x1f8), 'Unexpected USB lock context.')
        self.locks.append(('lock', self.arg(0)))
        return int(self.acquire)

    def unlocked(self):
        require(self.arg(0) == CONTEXT + (0x200 if self.arm else 0x1f8), 'Unexpected USB unlock context.')
        self.locks.append(('unlock', self.arg(0)))
        return 0

    def copied(self):
        destination, source, size = (self.arg(index) for index in range(3))
        require(size <= 256, 'Unexpected original memcpy length.')
        if size:
            self.cpu.mem_write(destination, bytes(self.cpu.mem_read(source, size)))
        return destination

    def slept(self):
        self.sleeps.append(self.arg(0))
        return 0

    def message(self):
        kind = self.selectors.get(self.arg(1))
        if kind == 'allocate':
            require(self.arg(0) == PROPS + 0x300 and self.arg(3) <= 256,
                    'Unexpected NSMutableData allocation.')
            self.buffer = bytearray(self.cpu.mem_read(self.arg(2), self.arg(3)))
            return PROPS + 0x800 if self.allocate else 0
        if kind == 'copy':
            require(self.arg(0) == PROPS + 0x800 and self.arg(3) <= len(self.buffer),
                    'Original getBytes:length: exceeds its modeled NSMutableData contract.')
            if self.arg(3):
                self.cpu.mem_write(self.arg(2), bytes(self.buffer[:self.arg(3)]))
            return 0
        require(kind in ('send_data', 'send_empty') and self.arg(0) == PROPS + 0x100,
                'Unexpected Objective-C/IOKit message.')
        setup = struct.pack('<Q', self.arg(2))
        request_type, request, value, index, size = struct.unpack('<BBHHH', setup)
        payload = bytes(self.buffer) if kind == 'send_data' and request_type == 0x40 else b''
        self.requests.append((request_type, request, value, index, size, payload.hex()))
        if kind == 'send_data':
            require(self.arg(3) == PROPS + 0x800 and len(self.buffer) == size and self.arg(5) == 0,
                    'Unexpected data-request host arguments.')
            actual = size if self.actual is None else self.actual
            require(0 <= actual <= size, 'Host completion exceeds its modeled data allocation.')
            write(self.cpu, self.arg(4), actual, 'Q')
            if request_type == 0xc0:
                response = self.reply
                if (request, value) == (3, 1):
                    flags = self.flags.pop(0) if len(self.flags) > 1 else self.flags[0]
                    response = self.rate.to_bytes(3, 'little') + bytes([flags])
                require(len(response) >= actual, 'Missing modeled input bytes.')
                self.buffer[:actual] = response[:actual]
        else:
            require(self.arg(3) == 0, 'Unexpected no-data request error pointer.')
        return int(self.ok)

    def invoke(self, name, arguments=(), interface=None):
        arguments = (CONTEXT, PROPS + 0x100 if interface is None else interface, *arguments)
        self.raw_call(self.addresses[name], arguments)
        return self.cpu.reg_read(self.result) & 0xff

    def raw_call(self, address, arguments):
        if not self.arm and len(arguments) > 6:
            require(len(arguments) == 7, 'Unexpected x86_64 control-call arity.')
            write(self.cpu, STACK + 8, arguments[6], 'Q')
            arguments = arguments[:6]
        self.execute(address, arguments + (0,) * max(0, 4 - len(arguments)))
        lock = CONTEXT + (0x200 if self.arm else 0x1f8)
        trace = [('lock', lock), ('unlock', lock)] if self.acquire else [('lock', lock)]
        require(self.locks == trace * sum(kind == 'lock' for kind, _ in self.locks),
                'Original request did not balance its acquired lock.')


def request_cases(reference):
    count = 0
    for incoming in (False, True):
        for size, pointer in ((0, 0), (0, DATA + 0xe000), (1, DATA + 0xe000),
                              (255, DATA + 0xe000), (256, DATA + 0xe000), (1, 0)):
            for acquire in (False, True):
                reference.reset(acquire=acquire, reply=b'\x5a' * 255)
                original = bytes(range(256)) + b'\xa5' * 8
                reference.cpu.mem_write(DATA + 0xe000, original)
                write(reference.cpu, DATA + 0xdf00, size, 'H')
                direction = 0xc0 if incoming else 0x40
                reference.raw_call(reference.addresses['request'],
                                   (CONTEXT, PROPS + 0x100, direction, 5, 0, DATA + 0xdf00, pointer))
                result = reference.cpu.reg_read(reference.result) & 0xff
                rejected = size > 255 or size > 0 and pointer == 0
                require(result == int(not rejected), 'Original request preflight result differs.')
                require(read(reference.cpu, DATA + 0xdf00, 'H') == size,
                        'Original request length changed after preflight/success.')
                expected = original
                if not rejected:
                    payload = original[:size].hex() if not incoming else ''
                    require(reference.requests == [(direction, 5, 0, 0, size, payload)] and reference.locks,
                            'Original request setup/locking differs at a transfer boundary.')
                    if incoming:
                        expected = b'\x5a' * size + original[size:]
                else:
                    require(not reference.requests and not reference.locks,
                            'Rejected original request reached the host API or lock.')
                require(bytes(reference.cpu.mem_read(DATA + 0xe000, len(original))) == expected,
                        'Original request changed caller bytes outside its completed input.')
                count += 1
    return count


def din_cases(reference):
    values = (0, 1, 2, 0x3f, 0x40, 0x7f, 0x80, 0xff)
    for value in values:
        reference.reset()
        reference.invoke('din_set', (value,))
        require(reference.requests == [(0x40, 5, 0, 0, 2, bytes([0x40, value]).hex())],
                'Original DIN setter changed its opaque byte/request encoding.')
        reference.reset(reply=bytes([value]))
        result = reference.invoke('din_get', (DATA + 0xe000,))
        require(result == 1 and bytes(reference.cpu.mem_read(DATA + 0xe000, 8)) == bytes([value]) + b'\xa5' * 7,
                'Original DIN getter did not preserve the surrounding caller data.')
        require(reference.requests == [(0xc0, 5, 0, 0, 1, '')], 'Original DIN getter setup differs.')
    for settings in ({'ok': False}, {'actual': 0}, {'allocate': False}):
        reference.reset(**settings)
        require(reference.invoke('din_get', (DATA + 0xe000,)) == 0 and
                bytes(reference.cpu.mem_read(DATA + 0xe000, 8)) == b'\xa5' * 8,
                'Original DIN failure modified caller output.')
    reference.reset()
    require(reference.invoke('din_get', (DATA + 0xe000,), interface=0) == 0 and not reference.requests,
            'Null host interface did not reject the DIN query.')
    return len(values) * 2 + 4


def clock_cases(reference):
    count = 0
    for rate in RATES:
        reference.reset()
        reference.invoke('set_fs', (rate,))
        require(reference.requests == [(0x40, 3, 8, 0, 4, (b'\x40' + rate.to_bytes(3, 'little')).hex())],
                'Original forced-rate request differs.')
        for flags in (0, 1, 2, 3, 0x80, 0xff):
            reference.reset(rate=rate, flags=[flags])
            result = reference.invoke('stat', (DATA + 0xe004, DATA + 0xe000, DATA + 0xe001))
            require(result == 1 and read(reference.cpu, DATA + 0xe004) == rate and
                    bytes(reference.cpu.mem_read(DATA + 0xe000, 2)) == bytes([int(not flags & 1), int(not flags & 2)]),
                    'Original clock-status decoding differs.')
            require(reference.requests == [(0xc0, 3, 1, 0, 4, '')], 'Original clock-status setup differs.')
            count += 1
        count += 1
    for value in (0, 1, 2, 0xff):
        reference.reset(reply=bytes([value]))
        require(reference.invoke('ready', (DATA + 0xe000,)) == 1 and
                read(reference.cpu, DATA + 0xe000, 'B') == int(value == 1), 'Original readiness decode differs.')
        require(reference.requests == [(0xc0, 3, 2, 0, 1, '')], 'Original readiness request differs.')
        count += 1
    return count


def clock_poll_cases(reference):
    count = 0
    for rate in RATES:
        for flags, result, polls, sleeps in (([0], 1, 1, 0), ([1] * 39 + [0], 1, 40, 39),
                                             ([1], 0, 40, 40), ([2], 0, 1, 0), ([3], 0, 1, 0)):
            reference.reset(rate=rate, flags=list(flags))
            require(reference.invoke('send_fs', (rate,)) == result, 'Original rate negotiation result differs.')
            expected = [(0x40, 3, 8, 0, 4, (b'\x40' + rate.to_bytes(3, 'little')).hex())]
            expected += [(0xc0, 3, 1, 0, 4, '')] * polls
            require(reference.requests == expected and reference.sleeps == [200000] * sleeps,
                    'Original rate negotiation poll/200 ms delay boundary differs.')
            count += 1
        for settings, expected_result, polls in (({'rate': 48001}, 0, 1), ({'ok': False}, 0, 0),
                                                 ({'actual': 0}, 0, 1)):
            reference.reset(**({'rate': rate} | settings))
            require(reference.invoke('send_fs', (rate,)) == expected_result,
                    'Original clock poll did not reject a failed/short request or rate mismatch.')
            require(len(reference.requests) == 1 + polls and not reference.sleeps,
                    'Original clock poll continued after a transport/rate failure.')
            count += 1
    return count


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mac_audio', type=Path, help='original macOS x86_64 audio driver')
    parser.add_argument('--arm64', type=Path, help='optional original ARM64 audio driver')
    args = parser.parse_args()
    results = {}
    for architecture, path in (('x86_64', args.mac_audio), ('arm64', args.arm64)):
        if path is None:
            continue
        reference = MacUsb(path, architecture)
        results[architecture] = {'request_cases': request_cases(reference),
                                 'din_cases': din_cases(reference), 'clock_cases': clock_cases(reference),
                                 'clock_poll_cases': clock_poll_cases(reference)}
    print(json.dumps({'architectures': results, 'hardware_validation': False,
                      'din_setting_meanings_verified': False,
                      'host_dependencies': 'Objective-C mutable data, IOKit completion, locks, memcpy and sleep stubs'}))


if __name__ == '__main__':
    main()
