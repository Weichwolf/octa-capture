#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Execute original Windows/macOS stream geometry without loading either driver."""

import argparse
import json
from pathlib import Path
import runpy
import struct

from unicorn import UC_HOOK_CODE
from unicorn import x86_const as registers

RATES = (44100, 48000, 96000, 192000)
WINDOWS_GUI_SHA256 = '634c140bc4e49b0d65c70d5156be19573ad3d440ac5ea24e24d3ad59974da406'
HELPERS = runpy.run_path(str(Path(__file__).with_name('verify-pcm-peaks.py')))
CONTEXT, PROPS = (HELPERS[name] for name in ('CONTEXT', 'PROPS'))


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def read(cpu, address, code='I'):
    return struct.unpack('<' + code, cpu.mem_read(address, struct.calcsize('<' + code)))[0]


def write(cpu, address, value, code='I'):
    HELPERS['write'](cpu, address, value, code)


def returned(cpu, value):
    stack = cpu.reg_read(registers.UC_X86_REG_RSP)
    cpu.reg_write(registers.UC_X86_REG_RAX, value)
    cpu.reg_write(registers.UC_X86_REG_RSP, stack + 8)
    cpu.reg_write(registers.UC_X86_REG_RIP, read(cpu, stack, 'Q'))


def windows_layout(cpu, rate, capture, playback):
    cpu.mem_write(CONTEXT, bytes(0x40000))
    cpu.mem_write(PROPS, bytes(0x1000))
    write(cpu, CONTEXT + 8, PROPS, 'Q')
    write(cpu, CONTEXT + 0x10, PROPS + 0x400, 'Q')
    write(cpu, CONTEXT + 0xde38, PROPS, 'Q')
    write(cpu, PROPS + 0x38, rate)
    write(cpu, PROPS + 0x44, int(capture))
    write(cpu, PROPS + 0x48, int(playback))
    write(cpu, PROPS + 0x438, 1)  # High-speed USB fixture.
    cpu.mem_write(PROPS + 0x88, struct.pack('<10H', *range(100, 110)))
    calls = {}

    def dependency(engine, address, _size, _context):
        if address == 0x14001ec00:
            stack = engine.reg_read(registers.UC_X86_REG_RSP)
            calls['capture'] = read(engine, stack + 0x28)
            calls['packet_selector'] = engine.reg_read(registers.UC_X86_REG_R8)
        elif address == 0x14001ed90:
            calls['playback'] = engine.reg_read(registers.UC_X86_REG_R9)
        returned(engine, 1)

    # Replace only host allocation/setup dependencies. The original rate branches,
    # channel counts, byte strides and argument passing execute unchanged.
    hooks = [cpu.hook_add(UC_HOOK_CODE, dependency, begin=address, end=address)
             for address in (0x140024860, 0x140027e90, 0x14000da80, 0x14001ec00, 0x14001ed90)]
    try:
        HELPERS['execute'](cpu, 0x1400269c0, (CONTEXT, 0, 0, 0), windows=True)
        require(cpu.reg_read(registers.UC_X86_REG_RAX) == 1, 'Windows stream builder failed')
        expected_capture, expected_playback = (4, 4) if rate == 192000 else (12, 10)
        require(calls.get('capture') == (expected_capture if capture else None), 'Windows capture count differs')
        require(calls.get('playback') == (expected_playback if playback else None), 'Windows playback count differs')
        require(read(cpu, CONTEXT + 0xdd28) == expected_playback * 3, 'Windows playback stride differs')
        require(read(cpu, CONTEXT + 0xdd2c) == expected_capture * 3, 'Windows capture stride differs')
        require(read(cpu, CONTEXT + 0x31170) == 8000, 'Windows service frequency differs')
        fallback = rate - rate // 1000
        require(read(cpu, CONTEXT + 0x31168) == fallback // 8000 and
                read(cpu, CONTEXT + 0x31164) == fallback % 8000, 'Windows capture fallback accumulator differs')
        if capture:
            selector = {44100: 102, 48000: 103, 96000: 105, 192000: 107}[rate]
            require(calls['packet_selector'] == selector, 'Windows descriptor-rate selector differs')
        return {'rate': rate, 'capture': expected_capture, 'playback': expected_playback,
                'capture_stride': expected_capture * 3, 'playback_stride': expected_playback * 3,
                'capture_fallback_rate': fallback}
    finally:
        for hook in hooks:
            cpu.hook_del(hook)


def windows_packet_accounting(cpu, layout):
    request, urb = HELPERS['REQUEST'], HELPERS['URB']
    nominal, fallback = layout['rate'] // 8000, layout['capture_fallback_rate']
    packet_frames = [nominal, nominal + 1, 0, nominal, nominal // 2 - 1, nominal // 2, nominal, 0]
    cpu.mem_write(request, bytes(0x1000))
    cpu.mem_write(urb, bytes(0x1000))
    write(cpu, request, urb, 'Q')
    write(cpu, request + 32, layout['capture_stride'])
    write(cpu, urb + 0x84, len(packet_frames))
    accumulator, expected = 0, 0
    for index, frames in enumerate(packet_frames):
        write(cpu, urb + 0x90 + index * 12, frames * layout['capture_stride'])
        if frames < nominal // 2:
            accumulator += fallback % 8000
            frames = fallback // 8000 + accumulator // 8000
            accumulator %= 8000
        expected += frames
    HELPERS['execute'](cpu, 0x140023670, (CONTEXT, request, 0, 0), windows=True)
    require(cpu.reg_read(registers.UC_X86_REG_RAX) == expected, 'Windows short-packet accounting differs')
    require(read(cpu, CONTEXT + 0x3116c) == accumulator, 'Windows fallback phase differs')


def mac_layout(cpu, layout):
    cpu.mem_write(CONTEXT, bytes(0x1000))
    cpu.mem_write(PROPS, bytes(0x1000))
    write(cpu, CONTEXT, PROPS, 'Q')
    write(cpu, PROPS + 0x14, layout['rate'])
    write(cpu, PROPS + 0x39, 1, 'B')
    nominal = (layout['rate'] + 4000) // 8000
    for capture in (False, True):
        write(cpu, CONTEXT + 8, int(capture), 'B')
        HELPERS['execute'](cpu, 0x13856, (CONTEXT, 0, 0, 0))
        expected = nominal * layout['capture_stride' if capture else 'playback_stride']
        require(cpu.reg_read(registers.UC_X86_REG_RAX) == expected,
                'macOS nominal USB frame geometry differs from Windows layout')


def mac_transfer_allocation(cpu, layout):
    request = HELPERS['REQUEST']
    for capture in (False, True):
        cpu.mem_write(CONTEXT, bytes(0x1000))
        cpu.mem_write(PROPS, bytes(0x1000))
        write(cpu, PROPS, PROPS + 0x100, 'Q')
        write(cpu, PROPS + 8, int(capture), 'B')
        write(cpu, PROPS + 0x114, layout['rate'])
        write(cpu, PROPS + 0x139, 1, 'B')  # High-speed fixture, default device index 0.
        stride = layout['capture_stride' if capture else 'playback_stride']
        packet_bytes = ((layout['rate'] + 7999) // 8000) * stride
        write(cpu, request + 4, packet_bytes, 'H')
        allocations = []

        def dependency(engine, address, _size, _context):
            if address == 0x1d60:
                allocations.append(engine.reg_read(registers.UC_X86_REG_RDX))
            returned(engine, request if address == 0x2010 else 1)

        # Supply a synthetic endpoint descriptor and successful host allocations.
        # The original packet batching, byte sizing and channel branches execute.
        hooks = [cpu.hook_add(UC_HOOK_CODE, dependency, begin=address, end=address)
                 for address in (0x11bec, 0x2010, 0x1d60, 0x11a90)]
        try:
            HELPERS['execute'](cpu, 0x11af6, (CONTEXT, PROPS, PROPS + 0x200, PROPS + 0x300))
            require(cpu.reg_read(registers.UC_X86_REG_RAX) == 1, 'macOS packet allocation failed')
            require(read(cpu, CONTEXT + 0x40) == 32 and read(cpu, CONTEXT + 0x44) == 256,
                    'macOS default high-speed batching differs from 32 ms / 256 microframes')
            require(read(cpu, CONTEXT + 0x48) == packet_bytes and read(cpu, CONTEXT + 0x4c) == stride,
                    'macOS transfer packet size or channel stride differs')
            require(allocations == [packet_bytes * 256], 'macOS host allocation size differs')
        finally:
            for hook in hooks:
                cpu.hook_del(hook)


def descriptor_layout(path):
    raw, position, interface = path.read_bytes(), 0, None
    result = {}
    while position < len(raw):
        require(position + 2 <= len(raw), 'Truncated USB descriptor header')
        length, kind = raw[position:position + 2]
        require(length >= 2 and position + length <= len(raw), 'Invalid USB descriptor size')
        item = raw[position:position + length]
        if kind == 4:
            require(length >= 9, 'Truncated USB interface descriptor')
            interface = item[2]
        elif kind == 0x24 and interface in (0, 1) and length >= 4 and item[2:4] == b'\x02\x01':
            require(length == 11 and item[7] == 1, 'Unexpected audio format descriptor')
            rate = int.from_bytes(item[8:11], 'little')
            require(item[5:7] == b'\x03\x18', 'USB format is not packed 24-bit PCM')
            direction = 'capture' if interface == 1 else 'playback'
            require((rate, direction) not in result, 'Duplicate audio format')
            result[rate, direction] = item[4]
        position += length
    require(set(result) == {(rate, direction) for rate in RATES for direction in ('capture', 'playback')},
            'USB descriptor rate/direction set differs')
    return result


def windows_warning(path):
    cpu = HELPERS['cpu_image'](path, WINDOWS_GUI_SHA256, windows=True)
    captured = []

    def formatted(engine, _address, _size, _context):
        captured.extend(engine.reg_read(register) for register in
                        (registers.UC_X86_REG_RDX, registers.UC_X86_REG_R8, registers.UC_X86_REG_R9))
        engine.emu_stop()

    cpu.hook_add(UC_HOOK_CODE, formatted, begin=0x140026100, end=0x140026100)
    cpu.reg_write(registers.UC_X86_REG_RSP, HELPERS['STACK'])
    # Execute only the original warning's argument preparation, stopping before
    # the MFC resource formatter. The surrounding dialog/runtime is not emulated.
    cpu.emu_start(0x14002c892, HELPERS['STOP'], timeout=1_000_000, count=20)
    require(captured == [0x80b8, 4, 4], 'Windows 192 kHz warning format arguments differ')
    print('PASS Windows 192 kHz warning: original instructions pass 4-in/4-out to the localized formatter')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mac_audio', type=Path)
    parser.add_argument('windows_kernel', type=Path)
    parser.add_argument('--windows-gui', type=Path, help='also execute the 192 kHz warning argument preparation')
    parser.add_argument('--descriptors', type=Path, help='optional cached USB descriptors; no USB control requests')
    args = parser.parse_args()
    windows = HELPERS['cpu_image'](args.windows_kernel, HELPERS['WINDOWS_SHA256'], windows=True)
    mac = HELPERS['cpu_image'](args.mac_audio, HELPERS['MAC_SHA256'])
    layouts = []
    for rate in RATES:
        for capture, playback in ((True, True), (True, False), (False, True)):
            layout = windows_layout(windows, rate, capture, playback)
        windows_packet_accounting(windows, layout)
        mac_layout(mac, layout)
        mac_transfer_allocation(mac, layout)
        layouts.append(layout)
    if args.descriptors:
        cached = descriptor_layout(args.descriptors)
        for layout in layouts:
            for direction in ('capture', 'playback'):
                require(cached[layout['rate'], direction] == layout[direction],
                        'USB descriptor channel count differs from original drivers')
        print('PASS cached USB formats: all four rates, channel counts and packed 24-bit PCM')
    if args.windows_gui:
        windows_warning(args.windows_gui)
    print('PASS original CPU execution: 12 Windows stream-builder, 4 short-packet and 8 macOS nominal USB-frame cases')
    print('PASS 8 original macOS transfer allocations: default index 0, high speed, 32 ms / 256 microframes')
    print('Host dependencies are stubbed; no USB transfer, IRP allocation or audio signal is exercised.')
    print(json.dumps(layouts, indent=2))


if __name__ == '__main__':
    main()
