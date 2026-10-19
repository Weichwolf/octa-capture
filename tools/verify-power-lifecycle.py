#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Execute original Windows/macOS power callbacks offline with host I/O isolated."""

import argparse
import itertools
from pathlib import Path
import runpy
import struct

from unicorn import UC_HOOK_CODE
from unicorn import x86_const as registers

HELPERS = runpy.run_path(str(Path(__file__).with_name('verify-pcm-peaks.py')))
CONTEXT, PROPS, DATA, STOP = (HELPERS[name] for name in ('CONTEXT', 'PROPS', 'DATA', 'STOP'))
MODEL = 0x78000000


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def read(cpu, address, kind='I'):
    return struct.unpack('<' + kind, cpu.mem_read(address, struct.calcsize('<' + kind)))[0]


def write(cpu, address, value, kind='I'):
    cpu.mem_write(address, struct.pack('<' + kind, value))


def returned(cpu, value):
    stack = cpu.reg_read(registers.UC_X86_REG_RSP)
    cpu.reg_write(registers.UC_X86_REG_RAX, value & 0xffffffffffffffff)
    cpu.reg_write(registers.UC_X86_REG_RIP, read(cpu, stack, 'Q'))
    cpu.reg_write(registers.UC_X86_REG_RSP, stack + 8)


def pe_imports(cpu, path):
    base = 0x140000000
    image = HELPERS['read_image'](path, HELPERS['WINDOWS_SHA256'])
    header = struct.unpack_from('<I', image, 0x3c)[0]
    directory = base + struct.unpack_from('<I', image, header + 24 + 120)[0]
    result = {}
    while read(cpu, directory):
        lookup, _, _, _, target = struct.unpack('<5I', cpu.mem_read(directory, 20))
        index = 0
        while pointer := read(cpu, base + lookup + index * 8, 'Q'):
            require(not pointer >> 63, 'Ordinal imports require explicit audit.')
            name = bytes(cpu.mem_read(base + pointer + 2, 128)).split(b'\0', 1)[0].decode('ascii')
            result[name] = base + target + index * 8
            index += 1
        directory += 20
    return result


class Windows:
    def __init__(self, path):
        self.cpu = HELPERS['cpu_image'](path, HELPERS['WINDOWS_SHA256'], windows=True)
        self.cpu.mem_map(MODEL, 0x80000)
        require(struct.unpack('<3Q', self.cpu.mem_read(0x14002b600, 24)) ==
                (0x14000b730, 0x14000b990, 0x14000b950), 'Power interface method table differs.')
        self.events, self.frames, self.targets = [], [], []
        self.allocate, self.registration_status = True, 0
        self.imports = pe_imports(self.cpu, path)
        self.imported('ExAllocatePoolWithTag', self.allocated)
        self.imported('PcRegisterAdapterPowerManagement', self.registered)
        self.imported('KeWaitForSingleObject', lambda: self.event('wait', self.argument(0) - MODEL))
        self.imported('KeSetEvent', lambda: self.event('signal', self.argument(0) - MODEL))
        for address, name, offset in ((0x140004cb0, 'timer_start', 0x783a8),
                                       (0x140004d40, 'timer_stop', 0x783a8),
                                       (0x140014a70, 'save_host', 0x30),
                                       (0x14000bae0, 'stop_audio', 0x2d8)):
            self.stub(address, lambda name=name, offset=offset: self.checked(name, offset))
        self.stub(0x140013ef0, self.connected)
        self.stub(0x14000b9e0, self.resume_audio)
        self.stub(0x140027e10, self.worker)
        self.stub(0x14001b5e0, self.sent)
        self.observe(0x14000aa20, lambda: self.checked('bulk', 0x778e8))
        self.observe(0x14000a990, lambda: self.checked('meter', 0x778e8, self.argument(1) & 255))

    def argument(self, index):
        return self.cpu.reg_read((registers.UC_X86_REG_RCX, registers.UC_X86_REG_RDX,
                                  registers.UC_X86_REG_R8, registers.UC_X86_REG_R9)[index])

    def event(self, *values):
        self.events.append(values)
        return 0

    def checked(self, name, offset, *values):
        require(self.argument(0) == MODEL + offset, f'{name}: unexpected context address.')
        require(read(self.cpu, CONTEXT + 0x30) == self.case['new'], f'{name}: power cache was not updated first.')
        return self.event(name, *values)

    def stub(self, address, callback):
        self.cpu.hook_add(UC_HOOK_CODE, lambda cpu, *_: returned(cpu, callback()), begin=address, end=address)

    def observe(self, address, callback):
        self.cpu.hook_add(UC_HOOK_CODE, lambda *_: callback(), begin=address, end=address)

    def imported(self, name, callback):
        address = STOP + 0x100 + len(self.targets) * 16
        self.targets.append(address)
        write(self.cpu, self.imports[name], address, 'Q')
        self.stub(address, callback)

    def allocated(self):
        require((self.argument(0), self.argument(1), self.argument(2)) == (0x200, 0x38, 0x72436350),
                'Power object allocation differs.')
        self.event('allocate')
        return CONTEXT if self.allocate else 0

    def registered(self):
        require((self.argument(0), self.argument(1)) == (CONTEXT, PROPS), 'Power registration context differs.')
        self.event('register')
        return self.registration_status

    def connected(self):
        self.checked('connected', 0x30)
        return self.case['connected']

    def resume_audio(self):
        self.checked('resume_audio', 0x30)
        if self.case.get('drop'):
            write(self.cpu, MODEL + 0x68, 0)
        return 1

    def worker(self):
        command = self.argument(1)
        self.checked('worker', 0xb278, command)
        if command and self.case.get('drop'):
            write(self.cpu, MODEL + 0x68, 0)
        return self.case['ack']

    def sent(self):
        size = read(self.cpu, self.argument(3))
        self.frames.append(bytes(self.cpu.mem_read(self.argument(2), size)))
        return 1

    def reset(self):
        for address, size in ((CONTEXT, 0x100), (PROPS, 0x1000), (DATA, 0x10000), (MODEL, 0x80000)):
            self.cpu.mem_write(address, bytes(size))
        self.events.clear()
        self.frames.clear()
        write(self.cpu, PROPS + 0x40, DATA, 'Q')
        write(self.cpu, DATA + 0x208, MODEL, 'Q')

    def register(self, allocated=True, status=0):
        self.reset()
        self.allocate, self.registration_status = allocated, status
        write(self.cpu, MODEL + 0x78880, 0x55, 'Q')
        HELPERS['execute'](self.cpu, 0x1400127e0, (MODEL, PROPS, 0, 0), windows=True)
        return self.cpu.reg_read(registers.UC_X86_REG_RAX) & 0xffffffff

    def prepare(self, case):
        self.reset()
        self.case = case
        write(self.cpu, CONTEXT, 0x14002b5c8, 'Q')
        write(self.cpu, CONTEXT + 8, 0x14002b5e8, 'Q')
        write(self.cpu, CONTEXT + 0x28, MODEL, 'Q')
        write(self.cpu, CONTEXT + 0x30, case['old'])
        for offset, value in ((0x68, case['wave']), (0x2b8, case['transport']),
                               (0x78358, case['client']), (0x78370, case['meter'])):
            write(self.cpu, MODEL + offset, value)
        write(self.cpu, MODEL + 0x78330, PROPS, 'Q')
        write(self.cpu, PROPS + 0x38, case['ready'])

    def transition(self, case):
        self.prepare(case)
        HELPERS['execute'](self.cpu, 0x14000b730, (CONTEXT + 8, case['new'], 0, 0), windows=True)
        state = tuple(read(self.cpu, address) for address in (CONTEXT + 0x30, MODEL + 0x2b8, MODEL + 0x68))
        return state, list(self.events), list(self.frames)


def frame(command, body):
    return bytes.fromhex('f0411000004d') + bytes([command]) + body + bytes([-sum(body) % 128, 247])


def expected(case):
    old, target, transport, wave = (case[key] for key in ('old', 'new', 'transport', 'wave'))
    events, frames = [], []
    def meter(value):
        events.append(('meter', value))
        if case['client'] and case['ready']:
            frames.append(frame(0x12, bytes([0, 10, 0, 0, value])))
    def worker(command):
        events.extend([('wait', 0x3ecd0), ('worker', command), ('signal', 0x3ecd0)])
    if old == target or target not in (1, 2, 3, 4):
        return (old, transport, wave), events, frames
    if target != 1:
        events.extend([('timer_stop',), ('save_host',)])
        if transport not in (0, 3, 4, 5):
            transport = 3
        if case['ready']:
            meter(0)
        if wave:
            worker(1)
            if case.get('drop'):
                wave = 0
            if wave:
                events.extend([('wait', 0xb260), ('stop_audio',), ('signal', 0xb260)])
    else:
        events.append(('connected',))
        if case['connected']:
            if wave:
                events.extend([('wait', 0xb260), ('resume_audio',), ('signal', 0xb260)])
                if case.get('drop'):
                    wave = 0
                if wave:
                    worker(0)
                    if not case['ack']:
                        worker(1)
            if case['ready'] and case['client']:
                events.append(('bulk',))
                frames.append(frame(0x11, bytes([1, 0, 0, 0, 0, 0, 8, 122])))
                meter(case['meter'])
            if transport not in (0, 1, 4, 5):
                transport = 1
            events.append(('timer_start',))
    return (target, transport, wave), events, frames


def cases():
    keys = ('old', 'new', 'connected', 'wave', 'ready', 'client', 'meter', 'ack')
    for values in itertools.product((1, 2, 3, 4), (0, 1, 2, 3, 4, 5, 0xffffffff),
                                     (0, 2), (0, 1), (0, 1), (0, 1), (0, 1), (0, 1)):
        yield dict(zip(keys, values, strict=True), transport=2)
    for old, target, transport, drop in itertools.product((1, 3), (1, 3),
                                                          (0, 1, 3, 4, 5, 6, 0xffffffff), (False, True)):
        yield dict(old=old, new=target, transport=transport, drop=drop,
                   connected=2, wave=1, ready=1, client=1, meter=1, ack=0)


def registration(reference):
    for allocated, status in ((False, 0), (True, 0), (True, 0xc0000001)):
        result = reference.register(allocated, status)
        require(result == int(allocated), 'Original registration return differs.')
        require(read(reference.cpu, MODEL + 0x78880, 'Q') == (CONTEXT if allocated else 0x55),
                'Original registered-object ownership differs.')
        require(reference.events == ([('allocate',), ('register',)] if allocated else [('allocate',)]),
                'Original registration order differs.')
        if allocated:
            require(read(reference.cpu, CONTEXT + 0x30) == 1, 'Original initialization is not D0.')
            HELPERS['execute'](reference.cpu, 0x14000b960, (CONTEXT, 0x14002b570, DATA, 0), windows=True)
            require(read(reference.cpu, DATA, 'Q') == CONTEXT + 8, 'IAdapterPowerManagement QI differs.')
    print('PASS original power-object allocation, registration, interface selection and initial D0.')


def queries(reference):
    reference.register()
    count = 0
    for state in (0, 1, 2, 3, 4, 5, 0xffffffff):
        HELPERS['execute'](reference.cpu, 0x14000b990, (CONTEXT + 8, state, 0, 0), windows=True)
        require(reference.cpu.reg_read(registers.UC_X86_REG_RAX) & 0xffffffff ==
                (0 if state in (1, 2, 3, 4) else 0xc0000002), 'Power-state query policy differs.')
        count += 1
    for flags in (0, 0x200, 0x55555555, 0xffffffff):
        data = bytearray(range(64))
        struct.pack_into('<I', data, 4, flags)
        reference.cpu.mem_write(DATA, bytes(data))
        HELPERS['execute'](reference.cpu, 0x14000b950, (CONTEXT + 8, DATA, 0, 0), windows=True)
        require(reference.cpu.reg_read(registers.UC_X86_REG_RAX) == 0, 'Capability query did not return success.')
        struct.pack_into('<I', data, 4, flags | 0x200)
        require(bytes(reference.cpu.mem_read(DATA, len(data))) == data, 'Power capability updates differ.')
        count += 1
    print(f'PASS {count} original state-query/capability cases; SurpriseRemovalOK preserves other bytes.')


def mac_power(path):
    cpu = HELPERS['cpu_image'](path, HELPERS['MAC_SHA256'])
    observed = []
    def callback(kind):
        def hook(cpu, *_):
            require(cpu.reg_read(registers.UC_X86_REG_RDI) == PROPS, 'macOS power callback target differs.')
            observed.append((kind, read(cpu, CONTEXT + 8)))
            returned(cpu, 0)
        return hook
    for offset, kind in ((0x20, 'suspend'), (0x28, 'resume')):
        address = STOP + 0x100 + offset
        write(cpu, DATA + offset, address, 'Q')
        cpu.hook_add(UC_HOOK_CODE, callback(kind), begin=address, end=address)
    write(cpu, PROPS, DATA, 'Q')
    count = 0
    for initial, present in itertools.product((0, 1, 2), (False, True)):
        cpu.mem_write(CONTEXT, bytes(0x100))
        write(cpu, CONTEXT + 8, initial)
        write(cpu, CONTEXT + 0x10, PROPS if present else 0, 'Q')
        observed.clear()
        expected_calls, state = [], initial
        for name in ('resume', 'resume', 'suspend', 'suspend', 'resume', 'suspend'):
            target = int(name == 'resume')
            if state != target and present:
                expected_calls.append((name, state))
            address = 0x10f1c if target else 0x10ef0
            HELPERS['execute'](cpu, address, (CONTEXT, 0, 0, 0))
            state = target
            require(read(cpu, CONTEXT + 8) == state and observed == expected_calls,
                    f'macOS power callback/state ordering differs: initial={initial}, present={present}, {name}')
            count += 1
    print(f'PASS {count} original macOS power-manager events: duplicate suppression, null callbacks and cache ordering.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('windows_kernel', type=Path, help='Windows 1.5.3 x64 RDWM1110.SYS')
    parser.add_argument('--macos', type=Path, help='also execute original x86_64 macOS power-manager callbacks')
    args = parser.parse_args()
    reference = Windows(args.windows_kernel)
    registration(reference)
    queries(reference)
    count = 0
    for case in cases():
        actual = reference.transition(case)
        require(actual == expected(case), f'Power transition differs: {case}; original={actual}; expected={expected(case)}')
        count += 1
    print(f'PASS {count} original Windows power transitions: gating, ordering, cache state and exact MIDI frames.')
    if args.macos:
        mac_power(args.macos)
    print('USB/status, locks, timer, worker exchange and ring operations are memory stubs; no hardware or fuzzing.')


if __name__ == '__main__':
    main()
