#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Execute original USB descriptor/port selection offline using cached descriptors."""

import argparse
from pathlib import Path
import runpy
import struct

from unicorn import UC_HOOK_CODE
from unicorn import x86_const as registers

HELPERS = runpy.run_path(str(Path(__file__).with_name('verify-pcm-peaks.py')))
CONTEXT, PROPS, DATA = (HELPERS[name] for name in ('CONTEXT', 'PROPS', 'DATA'))
RATES = (44100, 48000, 96000, 192000)
PACKETS = {'capture': (256, 256, 468, 312), 'playback': (212, 212, 392, 312)}


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def read(cpu, address, code='I'):
    return struct.unpack('<' + code, cpu.mem_read(address, struct.calcsize('<' + code)))[0]


def returned(cpu, value):
    stack = cpu.reg_read(registers.UC_X86_REG_RSP)
    address = read(cpu, stack, 'Q')
    cpu.reg_write(registers.UC_X86_REG_RAX, value)
    cpu.reg_write(registers.UC_X86_REG_RSP, stack + 8)
    cpu.reg_write(registers.UC_X86_REG_RIP, address)


def fixture(path):
    with path.open('rb') as source:
        raw = source.read(65554)
    require(27 <= len(raw) <= 65553 and raw[:2] == bytes([18, 1]), 'Invalid cached device descriptor')
    require(struct.unpack_from('<HH', raw, 8) == (0x0582, 0x0120), 'Unexpected USB VID/PID')
    require(struct.unpack_from('<H', raw, 12)[0] == 0x0200, 'This fixture targets bcdDevice 0200')
    total = struct.unpack_from('<H', raw, 20)[0]
    require(raw[18:20] == bytes([9, 2]) and 9 <= total <= len(raw) - 18,
            'Invalid cached configuration descriptor')
    configuration = raw[18:18 + total]
    interfaces = set()
    auxiliary_alternates = set()
    interface = alternate = None
    position = 0
    while position < len(configuration):
        require(position + 2 <= len(configuration), 'Truncated descriptor header')
        size, kind = configuration[position:position + 2]
        require(size >= 2 and position + size <= len(configuration), 'Invalid descriptor length')
        if kind == 4:
            require(size >= 9, 'Truncated interface descriptor')
            interface, alternate = configuration[position + 2:position + 4]
            interfaces.add(interface)
        if kind == 5:
            require(size >= 7, 'Truncated endpoint descriptor')
            if interface == 4:
                endpoint = configuration[position:position + size]
                require(alternate in range(1, 5) and endpoint[2:4] == bytes([0x81, 3]) and
                        struct.unpack_from('<H', endpoint, 4)[0] == 8 and endpoint[6] == alternate,
                        'Unexpected interface-4 endpoint/interval')
                auxiliary_alternates.add(alternate)
        position += size
    require(interfaces == set(range(5)), 'Expected all five OCTA-CAPTURE interfaces, including interface 4')
    require(auxiliary_alternates == set(range(1, 5)), 'Expected interface-4 alternates 1..4')
    return configuration


def mac_descriptor(cpu, configuration):
    cpu.mem_write(DATA, configuration + b'\0')
    cpu.mem_write(PROPS, struct.pack('<QII', DATA, len(configuration) + 1, 200))
    cpu.reg_write(registers.UC_X86_REG_R8, PROPS + 0xf4)
    cpu.reg_write(registers.UC_X86_REG_R9, PROPS + 0x1d4)
    HELPERS['execute'](cpu, 0x12598, (CONTEXT, PROPS, PROPS + 0x18, PROPS + 0xf8))
    flags = (read(cpu, PROPS + 0xf4, 'B'), read(cpu, PROPS + 0x1d4, 'B'))
    require(flags == (4, 4), 'Original macOS parser did not find all four audio alternates')
    for name, base, interface, direction in (('capture', 0x18, 1, 1), ('playback', 0xf8, 0, 0)):
        for index, rate in enumerate(RATES):
            record = bytes(cpu.mem_read(PROPS + base + index * 20, 20))
            channels = 4 if rate == 192000 else (12 if direction else 10)
            require(record[:3] == bytes([1, interface, index + 1]) and record[7] == channels and
                    record[8] == 24 and record[16:18] == bytes([direction, 1]) and
                    struct.unpack_from('<I', record, 12)[0] == rate and
                    struct.unpack_from('<H', record, 18)[0] == PACKETS[name][index],
                    f'Original macOS {name} descriptor differs at {rate} Hz')
    return flags


def mac_selection(cpu, native_flags):
    flag_sets = ((0, 0), (0, 1), (1, 0), (1, 1), native_flags)
    cases = 0
    for capture, playback in flag_sets:
        cpu.mem_write(PROPS + 0xf4, bytes([capture]))
        cpu.mem_write(PROPS + 0x1d4, bytes([playback]))
        shared = capture and playback
        for kind, direction, expected in ((2, 0, (1, 2 if shared else 1, 2)),
                                           (2, 1, (1, 2 if shared else 1, 1)),
                                           (4, 0, (1, int(bool(shared)), 1)),
                                           (4, 1, (1, 0, 1)), (8, 0, (1, 3, 1))):
            cpu.mem_write(CONTEXT, bytes([0xa5]) * 8)
            HELPERS['execute'](cpu, 0x169a0, (PROPS, CONTEXT, kind, direction))
            require(bytes(cpu.mem_read(CONTEXT, 3)) == bytes(expected), 'Original macOS port selector differs')
            cases += 1
    opened = []

    def open_interface(engine, _address, _size, _context):
        pointer = engine.reg_read(registers.UC_X86_REG_RDI)
        interface = (pointer - DATA) // 0x100 - 1
        require(pointer == DATA + (interface + 1) * 0x100 and interface in range(8),
                'Unexpected original macOS interface object')
        opened.append(interface)
        returned(engine, 1)

    hook = cpu.hook_add(UC_HOOK_CODE, open_interface, begin=0x15c5c, end=0x15c5c)
    try:
        for capture, playback in flag_sets:
            opened.clear()
            cpu.mem_write(CONTEXT, bytes(0x40))
            cpu.mem_write(PROPS + 0x18 + 0xf4, bytes([capture]))
            cpu.mem_write(PROPS + 0x18 + 0x1d4, bytes([playback]))
            for interface in range(8):
                HELPERS['write'](cpu, PROPS + 0x248 + interface * 8, DATA + (interface + 1) * 0x100, 'Q')
            HELPERS['execute'](cpu, 0x16a5e, (CONTEXT, PROPS, 4, 0))
            expected = [1, 0, 3] if capture and playback else [0]
            require(opened == expected, f'Original macOS audio open sequence differs: {opened}')
            require(cpu.reg_read(registers.UC_X86_REG_RAX) & 0xffffffff == 1, 'Original macOS port opening failed')
    finally:
        cpu.hook_del(hook)
    return cases, len(flag_sets)


def windows_descriptor(cpu, configuration):
    cpu.mem_write(DATA, configuration)
    cpu.mem_write(PROPS, struct.pack('<QQI', DATA, 0, len(configuration)))
    HELPERS['execute'](cpu, 0x140014ea0, (CONTEXT, PROPS, 0, 0), windows=True)
    require(read(cpu, CONTEXT) == 0, 'Original Windows native interface-layout flag differs')
    for name, alternates, packets in (
            ('capture', (0x28, 0x29, 0x2b, 0x2d), (0x4c, 0x4e, 0x52, 0x56)),
            ('playback', (0x1d, 0x1e, 0x20, 0x22), (0x36, 0x38, 0x3c, 0x40))):
        for index, rate in enumerate(RATES):
            require(read(cpu, CONTEXT + alternates[index], 'B') == index + 1 and
                    read(cpu, CONTEXT + packets[index], 'H') == PACKETS[name][index],
                    f'Original Windows {name} descriptor differs at {rate} Hz')


def windows_selection(cpu):
    calls = []
    cases = 0

    def open_pipe(engine, _address, _size, _context):
        require(engine.reg_read(registers.UC_X86_REG_RCX) == CONTEXT and
                engine.reg_read(registers.UC_X86_REG_RDX) == PROPS, 'Unexpected original Windows pipe object')
        calls.append((engine.reg_read(registers.UC_X86_REG_R8), engine.reg_read(registers.UC_X86_REG_R9)))
        returned(engine, 1)

    hook = cpu.hook_add(UC_HOOK_CODE, open_pipe, begin=0x14001e7b0, end=0x14001e7b0)
    try:
        for layout in (0, 1):
            HELPERS['write'](cpu, PROPS + 0x48, layout)
            for address, expected in ((0x14001e860, (3, 0)), (0x14001e870, (2 - layout, 1)),
                                      (0x14001e890, (2 - layout, 0)), (0x14001e8b0, (1 - layout, 0)),
                                      (0x14001e8d0, (0, 0))):
                calls.clear()
                HELPERS['execute'](cpu, address, (CONTEXT, PROPS, 0, 0), windows=True)
                require(calls == [expected], f'Original Windows pipe constructor differs: {calls}')
                cases += 1
    finally:
        cpu.hook_del(hook)
    return cases


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mac_audio', type=Path, help='macOS 1.5.9 x86_64 audio-driver slice')
    parser.add_argument('windows_kernel', type=Path, help='Windows 1.5.3 x64 RDWM1110.SYS')
    parser.add_argument('descriptors', type=Path, help='cached USB device/configuration descriptors, e.g. sysfs descriptors')
    args = parser.parse_args()
    configuration = fixture(args.descriptors)
    mac = HELPERS['cpu_image'](args.mac_audio, HELPERS['MAC_SHA256'])
    windows = HELPERS['cpu_image'](args.windows_kernel, HELPERS['WINDOWS_SHA256'], windows=True)
    flags = mac_descriptor(mac, configuration)
    windows_descriptor(windows, configuration)
    mac_cases, openings = mac_selection(mac, flags)
    windows_cases = windows_selection(windows)
    print('PASS original Windows/macOS cached-descriptor parsing at all four rates, including packet sizes and macOS channel counts.')
    print(f'PASS {mac_cases + windows_cases} original port-selector cases and {openings} macOS audio-open sequences; '
          'host open calls are memory stubs.')
    print('Normal native paths select capture 1, playback 0, MIDI 2 and status 3; interface 4 meaning remains unresolved. No USB transfers.')


if __name__ == '__main__':
    main()
