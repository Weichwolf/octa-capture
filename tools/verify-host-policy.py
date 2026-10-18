#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Execute original Windows host-policy paths offline; never access a device."""

import argparse
import itertools
import json
from pathlib import Path
import runpy
import struct

from unicorn import UC_HOOK_CODE
from unicorn import x86_const as registers

HELPERS = runpy.run_path(str(Path(__file__).with_name('verify-pcm-peaks.py')))
GUI_SHA256 = '634c140bc4e49b0d65c70d5156be19573ad3d440ac5ea24e24d3ad59974da406'
CONTEXT, PROPS, DATA, STACK, STOP = (HELPERS[name] for name in ('CONTEXT', 'PROPS', 'DATA', 'STACK', 'STOP'))
PROPERTY_SIZE = 0x158


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def read(cpu, address, kind='I'):
    return struct.unpack('<' + kind, cpu.mem_read(address, struct.calcsize('<' + kind)))[0]


def returned(cpu, value):
    stack = cpu.reg_read(registers.UC_X86_REG_RSP)
    cpu.reg_write(registers.UC_X86_REG_RAX, value & 0xffffffffffffffff)
    cpu.reg_write(registers.UC_X86_REG_RIP, read(cpu, stack, 'Q'))
    cpu.reg_write(registers.UC_X86_REG_RSP, stack + 8)


class Gui:
    def __init__(self, path):
        self.cpu = HELPERS['cpu_image'](path, GUI_SHA256, windows=True)
        self.level = 0
        self.results = [0] * 8
        self.requests = []
        self.host_calls = []
        self.stub(0x14003919c, lambda _cpu: 1)  # MFC UpdateData; controls are initialized in memory.
        # MSVC's cookie helper preserves the policy function's return register.
        self.stub(0x1401c31c0, lambda cpu: cpu.reg_read(registers.UC_X86_REG_RAX))
        self.imported(0x1401efdd0, self.slider)
        self.imported(0x1401ef8d0, self.open_file)
        self.imported(0x1401ef8c8, self.close_file)
        self.imported(0x1401ef8c0, self.ioctl)

    def stub(self, address, callback):
        def hook(cpu, _address, _size, _context):
            returned(cpu, callback(cpu))
        self.cpu.hook_add(UC_HOOK_CODE, hook, begin=address, end=address)

    def imported(self, address, callback):
        target = STOP + 0x100 + len(self.host_calls) * 0x10
        self.host_calls.append(address)
        HELPERS['write'](self.cpu, address, target, 'Q')
        self.stub(target, callback)

    def slider(self, cpu):
        require(cpu.reg_read(registers.UC_X86_REG_RDX) == 0x400, 'Unexpected window message')
        return self.level

    def open_file(self, cpu):
        path = cpu.reg_read(registers.UC_X86_REG_RCX)
        slot, remainder = divmod(path - CONTEXT - 0x134, 0x20c)
        require(remainder == 0 and 0 <= slot < 8, 'Unexpected host device path')
        return -1 if self.results[slot] is None else 0x550 + slot

    @staticmethod
    def close_file(_cpu):
        return 1

    def ioctl(self, cpu):
        stack = cpu.reg_read(registers.UC_X86_REG_RSP)
        slot = cpu.reg_read(registers.UC_X86_REG_RCX) - 0x550
        require(0 <= slot < 8, 'Unexpected synthetic handle')
        require(cpu.reg_read(registers.UC_X86_REG_RDX) == 0x2221ec, 'Unexpected property IOCTL')
        buffer = cpu.reg_read(registers.UC_X86_REG_R8)
        require(cpu.reg_read(registers.UC_X86_REG_R9) == PROPERTY_SIZE + 4, 'Wrong input size')
        require(read(cpu, stack + 0x28, 'Q') == buffer and read(cpu, stack + 0x30, 'Q') == PROPERTY_SIZE + 4,
                'Wrong output buffer/size')
        self.requests.append((slot, bytes(cpu.mem_read(buffer, PROPERTY_SIZE))))
        if self.results[slot] == 'failure':
            return 0
        HELPERS['write'](cpu, buffer + PROPERTY_SIZE, self.results[slot])
        HELPERS['write'](cpu, read(cpu, stack + 0x38, 'Q'), PROPERTY_SIZE + 4)
        return 1

    def staged(self, level, flags, previous_light):
        match_rate, monitoring, multichannel, expand, light = flags
        self.cpu.mem_write(CONTEXT, bytes(0x2000))
        properties = bytearray(PROPERTY_SIZE)
        properties[:0x100] = bytes((index * 37 + 11) % 256 for index in range(0x100))
        struct.pack_into('<iiii', properties, 0x12c, 1, -1000, 0, 1000)
        struct.pack_into('<I', properties, 0x140, previous_light)
        self.cpu.mem_write(CONTEXT + 0x1190, bytes(properties))
        for offset, value in zip((0x1708, 0x170c, 0x1710, 0x1718, 0x1714), flags, strict=True):
            HELPERS['write'](self.cpu, CONTEXT + offset, value)
        self.level = level
        HELPERS['execute'](self.cpu, 0x14002b7d0, (CONTEXT, 0, 0, 0), windows=True)
        index = max(0, min(9, level))
        expected = {0x118: monitoring, 0x120: index, 0x128: int(not match_rate),
                    0x13c: expand, 0x140: previous_light if expand else light,
                    0x144: int(index < 5), 0x148: int(not multichannel)}
        if light:
            expected.update({0x130: -70, 0x138: 0})
        for offset, value in expected.items():
            struct.pack_into('<i', properties, offset, value)
        actual = bytes(self.cpu.mem_read(CONTEXT + 0x1190, PROPERTY_SIZE))
        require(actual == properties, f'Complete staged property mismatch: {level=}, {flags=}')
        require(read(self.cpu, CONTEXT + 0x1530) == int(index < 5), 'Buffer hint differs')
        return actual

    def apply(self, properties, results):
        self.results, self.requests = results, []
        HELPERS['write'](self.cpu, CONTEXT + 0x1440, 0, 'Q')
        self.cpu.mem_write(PROPS, properties)
        HELPERS['execute'](self.cpu, 0x14002d0f0, (CONTEXT, PROPS, DATA, 0), windows=True)
        accepted = [value for value in results if isinstance(value, int)]
        require(bool(self.cpu.reg_read(registers.UC_X86_REG_RAX)) == bool(accepted), 'Apply success differs')
        require([slot for slot, _ in self.requests] == [slot for slot, value in enumerate(results) if value is not None],
                'Apply skipped or added a device slot')
        require(all(value == properties for _, value in self.requests), 'Apply changed staged property bytes')
        if accepted:
            expected = 2 if any(value in (2, 3) for value in accepted) else int(1 in accepted)
            require(read(self.cpu, DATA) == expected, 'Apply restart classification differs')


class Kernel:
    def __init__(self, path):
        self.cpu = HELPERS['cpu_image'](path, HELPERS['WINDOWS_SHA256'], windows=True)
        self.calls = []
        self.success = True
        for index, address in enumerate((0x14002b0e8, 0x14002b1c8)):
            target = STOP + 0x100 + index * 0x10
            HELPERS['write'](self.cpu, address, target, 'Q')
            self.cpu.hook_add(UC_HOOK_CODE, self.spinlock, begin=target, end=target)
        self.cpu.hook_add(UC_HOOK_CODE, self.rebuild, begin=0x14000d680, end=0x14000d680)

    @staticmethod
    def spinlock(cpu, _address, _size, _context):
        require(cpu.reg_read(registers.UC_X86_REG_RCX) == CONTEXT + 0x371b8, 'Wrong stream lock')
        returned(cpu, 0)

    def rebuild(self, cpu, _address, _size, _context):
        self.calls.append(tuple(cpu.reg_read(register) for register in
                                (registers.UC_X86_REG_RCX, registers.UC_X86_REG_RDX,
                                 registers.UC_X86_REG_R8, registers.UC_X86_REG_R9)))
        returned(cpu, int(self.success))

    def gate(self, disabled, host_change, capture, playback, present, same_rate, success):
        self.calls, self.success = [], success
        self.cpu.mem_write(CONTEXT, bytes(0x40000))
        HELPERS['write'](self.cpu, CONTEXT + 0x30110, PROPS, 'Q')
        HELPERS['write'](self.cpu, PROPS + 0x28, PROPS + 0x200, 'Q')
        HELPERS['write'](self.cpu, PROPS + 0x328, int(disabled))
        HELPERS['write'](self.cpu, CONTEXT + 0x371a0, 0x1234 if present else 0, 'Q')
        old_rate, new_rate = 48000, 48000 if same_rate else 96000
        HELPERS['write'](self.cpu, CONTEXT + 0x371c0, old_rate)
        HELPERS['write'](self.cpu, CONTEXT + 0x371cc, capture, 'i')
        HELPERS['write'](self.cpu, CONTEXT + 0x371d0, playback, 'i')
        HELPERS['execute'](self.cpu, 0x140005a70, (CONTEXT, new_rate, int(host_change), 0), windows=True)
        rebuild = present and not same_rate and capture <= 0 and playback <= 0 and not (disabled and host_change)
        require(self.calls == ([(CONTEXT, 0x1234, old_rate, new_rate)] if rebuild else []), 'WDM update gate differs')
        require(read(self.cpu, CONTEXT + 0x371c4) == new_rate, 'Requested rate was not retained')
        require(read(self.cpu, CONTEXT + 0x371c0) == (new_rate if rebuild and success else old_rate),
                'Cached WDM rate updated without successful rebuild')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('windows_gui', type=Path)
    parser.add_argument('windows_kernel', type=Path)
    args = parser.parse_args()
    gui, kernel = Gui(args.windows_gui), Kernel(args.windows_kernel)
    staged = 0
    for level, flags, prior in itertools.product(range(-1, 11), itertools.product((0, 1), repeat=5), (0, 1)):
        properties = gui.staged(level, flags, prior)
        staged += 1
    applied = 0
    for first, second in itertools.product((None, 'failure', 0, 1, 2, 3, 99), repeat=2):
        gui.apply(properties, [first, None, None, None, second, None, None, None])
        applied += 1
    scenarios = [[None] * slot + [1] + [None] * (7 - slot) for slot in range(8)]
    scenarios.extend(([0] * 8, [1] * 8, [None, 1, 2, 0, 'failure', 3, 99, 0],
                      [3, 0, 1, 'failure', 2, None, 0, 99]))
    for results in scenarios:
        gui.apply(properties, results)
        applied += 1
    gated = 0
    for flags in itertools.product((False, True), repeat=5):
        for capture, playback in ((0, 0), (1, 0), (0, 1), (-1, 0)):
            disabled, host, present, same_rate, success = flags
            kernel.gate(disabled, host, capture, playback, present, same_rate, success)
            gated += 1
    print('PASS original Windows host-property staging, Apply transport and WDM rate synchronization gate.')
    print(json.dumps({'staged_property_cases': staged, 'apply_cases': applied, 'wdm_rate_gate_cases': gated,
                      'host_dependencies': 'memory-only UI, handle, IOCTL, lock and WDM-rebuild stubs',
                      'hardware_validation': False}))


if __name__ == '__main__':
    main()
