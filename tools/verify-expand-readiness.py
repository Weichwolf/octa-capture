#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Execute the original Windows VS EXPAND readiness gate with memory-only hosts."""

import argparse
from dataclasses import dataclass, replace
import itertools
import json
from pathlib import Path
import runpy
import struct

from unicorn import x86_const as registers

HOST = runpy.run_path(str(Path(__file__).with_name('verify-host-policy.py')))
HELPERS = HOST['HELPERS']
read, require = HOST['read'], HOST['require']
TYPE, RATE, CONTROLLER = 0x22204c, 0x222324, 0x22203c
RATES = (44100, 48000, 96000, 192000)


@dataclass(frozen=True)
class Device:
    version: int = 0x02000000
    rate: int = 48000
    controller: bytes = 'CONTROLLER-A'.encode('utf-16le')
    failed: frozenset[int] = frozenset()


def wide(cpu, address):
    data = bytearray()
    for index in range(260):
        unit = bytes(cpu.mem_read(address + index * 2, 2))
        if unit == b'\0\0':
            return data.decode('utf-16le')
        data += unit
    raise RuntimeError('Unterminated host string')


class Reference(HOST['Gui']):
    def __init__(self, path):
        super().__init__(path)
        self.paths, self.handles = {}, {}
        self.slots = {'base': [None] * 4, 'expand': [None] * 4}
        self.serial = 0
        self.comparisons = []
        self.stub(0x1401c3340, lambda cpu: cpu.reg_read(registers.UC_X86_REG_RAX))
        self.stub(0x1401cf750, self.compare)
        self.imported(0x1401eff40, self.format_path)

    def format_path(self, cpu):
        destination = cpu.reg_read(registers.UC_X86_REG_RCX)
        template = wide(cpu, cpu.reg_read(registers.UC_X86_REG_RDX))
        prefix = wide(cpu, cpu.reg_read(registers.UC_X86_REG_R8))
        index = cpu.reg_read(registers.UC_X86_REG_R9)
        require(template.endswith(('%s00%d', '%s%03d')) and 1 <= index <= 4, 'Unexpected device slot format')
        require(prefix in ('Rdwm1110', 'Rdwm1110Ex'), 'Unexpected device interface family')
        family = 'expand' if prefix.endswith('Ex') else 'base'
        self.paths[destination] = (family, index - 1)
        # Formatting/host name lookup is modeled; the original sees only opaque handles.
        text = f'{prefix}{index:03d}'
        cpu.mem_write(destination, (text + '\0').encode('utf-16le'))
        return len(text)

    def open_file(self, cpu):
        address = cpu.reg_read(registers.UC_X86_REG_RCX)
        require(address in self.paths, 'Unknown modeled path')
        family, slot = self.paths[address]
        if self.slots[family][slot] is None:
            return -1
        self.serial += 1
        handle = 0x5500 + self.serial
        self.handles[handle] = (family, slot)
        return handle

    def close_file(self, cpu):
        handle = cpu.reg_read(registers.UC_X86_REG_RCX)
        require(handle in self.handles, 'Unbalanced handle close')
        del self.handles[handle]
        return 1

    def ioctl(self, cpu):
        stack = cpu.reg_read(registers.UC_X86_REG_RSP)
        handle = cpu.reg_read(registers.UC_X86_REG_RCX)
        code = cpu.reg_read(registers.UC_X86_REG_RDX)
        data = cpu.reg_read(registers.UC_X86_REG_R8)
        size = cpu.reg_read(registers.UC_X86_REG_R9)
        require(handle in self.handles, 'IOCTL on an invalid handle')
        require(code in (TYPE, RATE, CONTROLLER), 'Unexpected readiness query')
        require(size == {TYPE: 4, RATE: 8, CONTROLLER: 0x808}[code], 'Unexpected query size')
        require(read(cpu, stack + 0x28, 'Q') == data and read(cpu, stack + 0x30, 'Q') == size,
                'Readiness query must use its bounded shared buffer')
        family, slot = self.handles[handle]
        device = self.slots[family][slot]
        returned = read(cpu, stack + 0x38, 'Q')
        HELPERS['write'](cpu, returned, 0)
        self.requests.append((family, slot, code))
        if code in device.failed:
            return 0
        if code == TYPE:
            HELPERS['write'](cpu, data, device.version)
        elif code == RATE:
            HELPERS['write'](cpu, data + 4, device.rate)
        else:
            require(len(device.controller) <= 0x800, 'Oversized controller fixture')
            cpu.mem_write(data + 4, struct.pack('<I', len(device.controller)) + device.controller)
        HELPERS['write'](cpu, returned, size)
        return 1

    def compare(self, cpu):
        first = cpu.reg_read(registers.UC_X86_REG_RCX)
        second = cpu.reg_read(registers.UC_X86_REG_RDX)
        size = cpu.reg_read(registers.UC_X86_REG_R8)
        require(size <= 0x800, 'Unbounded controller comparison')
        left, right = (bytes(cpu.mem_read(address, size)) for address in (first, second))
        require(all(byte < 128 for byte in left + right), 'Only ASCII controller keys are modeled')
        self.comparisons.append(size)
        return int(left.lower() != right.lower())

    def check(self, base, expand, expected):
        require(len(base) == len(expand) == 4, 'Expected four slots per interface family')
        require(not self.handles, 'Leaked modeled handle before execution')
        self.slots = {'base': base, 'expand': expand}
        self.paths, self.requests, self.comparisons = {}, [], []
        self.cpu.mem_write(HELPERS['STACK'] & ~0xffff, bytes(0x10000))
        HELPERS['execute'](self.cpu, 0x14002aaf0, (0, 0, 0, 0), windows=True)
        actual = self.cpu.reg_read(registers.UC_X86_REG_RAX) & 0xff
        require(actual == expected, f'Original readiness differs: {base=}, {expand=}, {actual=}, {expected=}')
        require(not self.handles, 'Original readiness leaked modeled handles')


def slots(index, device):
    result = [None] * 4
    result[index] = device
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('windows', type=Path, help='hash-pinned original RDDP1110.EXE')
    args = parser.parse_args()
    reference = Reference(args.windows)
    count = 0
    for primary, expanded, rate in itertools.product(range(4), range(4), RATES):
        device = Device(rate=rate)
        reference.check(slots(primary, device), slots(expanded, device), 0)
        count += 1
    for primary, expanded in itertools.product(range(4), repeat=2):
        device = Device()
        variations = [(replace(device, rate=96000), 2),
                      (replace(device, controller='CONTROLLER-B'.encode('utf-16le')), 3),
                      (replace(device, controller=b'X\0'), 3),
                      (replace(device, controller=device.controller.lower()), 0),
                      (replace(device, version=0x03000000), 4),
                      (replace(device, version=device.version | 0xffff), 0),
                      (replace(device, version=0), 1)]
        variations += [(replace(device, failed=frozenset({code})), 1) for code in (TYPE, RATE, CONTROLLER)]
        for candidate, expected in variations:
            reference.check(slots(primary, device), slots(expanded, candidate), expected)
            count += 1
        for code in (TYPE, RATE, CONTROLLER):
            reference.check(slots(primary, replace(device, failed=frozenset({code}))), slots(expanded, device), 1)
            count += 1
    device = Device()
    different_rate = replace(device, rate=96000)
    different_controller = replace(device, controller=b'B\0')
    incompatible = replace(device, version=0x03000000, rate=96000, controller=b'B\0')
    cases = [([None] * 4, [None] * 4, 1),
             (slots(0, device), [None] * 4, 1),
             ([None] * 4, slots(0, device), 1),
             ([device] * 4, [device] * 4, 0),
             ([None, replace(device, version=0), None, device], slots(2, device), 0),
             (slots(0, device), [different_rate, device, None, None], 2),
             (slots(0, device), [different_rate, different_controller, None, None], 3),
             (slots(0, device), slots(0, incompatible), 4)]
    for base, expand, expected in cases:
        reference.check(base, expand, expected)
        count += 1
    print('PASS original Windows VS EXPAND readiness across base/expansion slots, all rates, compatibility failures and query errors.')
    print(json.dumps({'cases': count, 'host_dependencies': 'memory-only handle/IOCTL, formatting and ASCII comparison stubs',
                      'hardware_validation': False, 'stream_synchronization_validation': False}))


if __name__ == '__main__':
    main()
