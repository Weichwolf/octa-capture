#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Execute original status polling and Windows status IOCTLs with memory-only I/O."""

import argparse
import itertools
import json
from pathlib import Path
import runpy
import struct

from unicorn import UC_HOOK_CODE
from unicorn import x86_const as registers

POWER = runpy.run_path(str(Path(__file__).with_name('verify-power-lifecycle.py')))
HELPERS = POWER['HELPERS']
CONTEXT, PROPS, DATA, STACK, STOP = (HELPERS[name] for name in ('CONTEXT', 'PROPS', 'DATA', 'STACK', 'STOP'))
read, write, require, returned = (POWER[name] for name in ('read', 'write', 'require', 'returned'))
MODEL = 0x78000000
OUTPUT = DATA + 0x1000
BUFFER = DATA + 0x8000
RATES = (44100, 48000, 96000, 192000)


class Windows:
    def __init__(self, path):
        self.cpu = HELPERS['cpu_image'](path, HELPERS['WINDOWS_SHA256'], windows=True)
        self.cpu.mem_map(MODEL, 0x80000)
        self.imports = POWER['pe_imports'](self.cpu, path)
        self.targets = []
        self.manager = CONTEXT + 0x32fa0
        self.stub(0x140027720, self.queued)
        self.stub(0x140029740, self.cleared)
        self.stub(0x140020750, self.drained)
        self.stub(0x140013ef0, self.connected)
        self.stub(0x14001e9b0, self.pipe_reset)
        for name, callback in (('KeInitializeEvent', self.initialized), ('KeWaitForSingleObject', self.waited),
                               ('KeAcquireSpinLockRaiseToDpc', self.locked),
                               ('KeReleaseSpinLock', self.unlocked)):
            target = STOP + 0x100 + len(self.targets) * 16
            self.targets.append(target)
            write(self.cpu, self.imports[name], target, 'Q')
            self.stub(target, callback)

    def argument(self, index):
        if index < 4:
            return self.cpu.reg_read((registers.UC_X86_REG_RCX, registers.UC_X86_REG_RDX,
                                      registers.UC_X86_REG_R8, registers.UC_X86_REG_R9)[index])
        return read(self.cpu, self.cpu.reg_read(registers.UC_X86_REG_RSP) + 8 + index * 8, 'Q')

    def stub(self, address, callback):
        self.cpu.hook_add(UC_HOOK_CODE, lambda *_: returned(self.cpu, callback()), begin=address, end=address)

    def initialized(self):
        require(tuple(self.argument(i) for i in range(3)) == (MODEL + 0x9d0, 0, 0),
                'Unexpected Windows status event initialization.')
        return 0

    def cleared(self):
        require(tuple(self.argument(i) for i in range(3)) == (BUFFER, 0, 8),
                'Unexpected Windows status transfer buffer clear.')
        self.cpu.mem_write(BUFFER, bytes(8))
        return BUFFER

    def queued(self):
        require(self.argument(0) == MODEL, 'Unexpected Windows polling context.')
        require(self.packets, 'Original Windows poll exceeded the fixed transfer sequence.')
        packet = self.packets.pop(0)
        self.transfers += 1
        if packet is None:
            return 0
        self.cpu.mem_write(BUFFER, packet)
        return 1

    def waited(self):
        require(tuple(self.argument(i) for i in range(4)) == (MODEL + 0x9d0, 0, 0, 0),
                'Unexpected Windows status wait arguments.')
        require(read(self.cpu, self.argument(4), 'q') == -1000000, 'Unexpected Windows polling deadline.')
        return self.wait_status

    def locked(self):
        require(self.argument(0) in (CONTEXT + 0x371b8, MODEL + 0x28) and not self.lock_depth,
                'Unexpected or recursive Windows stream lock.')
        self.lock_context = self.argument(0)
        self.lock_depth = 1
        return 2

    def unlocked(self):
        require(self.argument(0) == self.lock_context and self.argument(1) == 2 and self.lock_depth == 1,
                'Unexpected Windows stream unlock.')
        self.lock_depth = 0
        return 0

    def drained(self):
        require(self.argument(0) == MODEL, 'Unexpected Windows polling queue drain.')
        self.recovery.append('drain')
        return 0

    def connected(self):
        require(self.argument(0) == PROPS + 0x300, 'Unexpected Windows polling port query.')
        self.recovery.append('port')
        return int(self.port_connected)

    def pipe_reset(self):
        require(self.argument(0) == PROPS + 0x400, 'Unexpected Windows polling pipe reset.')
        self.recovery.append('reset')
        return int(self.reset_ok)

    def reset(self, packets, *, wait_status=0, stopped=False):
        self.packets, self.transfers, self.wait_status = list(packets), 0, wait_status
        self.lock_depth = 0
        self.recovery, self.port_connected, self.reset_ok = [], True, True
        self.cpu.mem_write(CONTEXT, bytes(0x40000))
        self.cpu.mem_write(MODEL, bytes(0x1000))
        self.cpu.mem_write(PROPS, bytes(0x1000))
        self.cpu.mem_write(OUTPUT, bytes([0xa5]) * 32)
        write(self.cpu, MODEL + 0x988, BUFFER, 'Q')
        write(self.cpu, MODEL + 0x998, 8)
        write(self.cpu, MODEL + 0xc, int(stopped))
        write(self.cpu, MODEL + 0x38, PROPS + 0x300, 'Q')
        write(self.cpu, MODEL + 0x48, PROPS + 0x400, 'Q')
        for offset, pointer in ((0x10, CONTEXT), (0x18, PROPS + 0x100), (0x20, PROPS), (0x38, MODEL)):
            write(self.cpu, self.manager + offset, pointer, 'Q')
        write(self.cpu, PROPS + 0x38, 48000)
        for offset in (0x52, 0x53, 0x55, 0x57):
            write(self.cpu, PROPS + offset, 1, 'B')

    def poll(self, *, manager=False):
        if manager:
            HELPERS['execute'](self.cpu, 0x140005320, (self.manager, 0, 0, 0), windows=True)
            return struct.unpack('<IIBB', self.cpu.mem_read(self.manager + 0x28, 10))
        write(self.cpu, STACK + 0x28, OUTPUT + 9, 'Q')
        HELPERS['execute'](self.cpu, 0x140005460, (MODEL, OUTPUT, OUTPUT + 4, OUTPUT + 8), windows=True)
        return bool(self.cpu.reg_read(registers.UC_X86_REG_RAX) & 0xff), bytes(self.cpu.mem_read(OUTPUT, 32))

    def ioctl(self, code):
        write(self.cpu, DATA + 0x18, OUTPUT, 'Q')
        write(self.cpu, DATA + 0xb8, DATA + 0x800, 'Q')
        write(self.cpu, DATA + 0x818, code)
        HELPERS['execute'](self.cpu, 0x1400292b0, (CONTEXT, DATA, 0, 0), windows=True)
        require(not self.lock_depth, 'Windows status getter retained the stream lock.')
        return self.cpu.reg_read(registers.UC_X86_REG_RAX) & 0xffffffff


class Mac:
    def __init__(self, path):
        self.cpu = HELPERS['cpu_image'](path, HELPERS['MAC_SHA256'])
        self.stub(0xa896, self.received)
        self.stub(0xcb68, self.locked)
        self.stub(0xa904, self.unlocked)

    def stub(self, address, callback):
        self.cpu.hook_add(UC_HOOK_CODE, lambda *_: returned(self.cpu, callback()), begin=address, end=address)

    def locked(self):
        require(self.cpu.reg_read(registers.UC_X86_REG_RDI) == CONTEXT + 0x48 and not self.lock_depth,
                'Unexpected macOS status lock.')
        self.lock_depth = 1
        return 1

    def unlocked(self):
        require(self.cpu.reg_read(registers.UC_X86_REG_RDI) == CONTEXT + 0x48 and self.lock_depth == 1,
                'Unexpected macOS status unlock.')
        self.lock_depth = 0
        return 0

    def received(self):
        require(self.cpu.reg_read(registers.UC_X86_REG_RDI) == CONTEXT and self.lock_depth == 1,
                'Unexpected or unlocked macOS status transfer.')
        require(self.packets, 'Original macOS poll exceeded the fixed transfer sequence.')
        packet = self.packets.pop(0)
        self.transfers += 1
        if packet is None:
            return 0xffffffffffffffff
        self.cpu.mem_write(BUFFER, packet)
        return 0

    def reset(self, packets, *, stopped=False):
        self.packets, self.transfers, self.lock_depth = list(packets), 0, 0
        self.cpu.mem_write(CONTEXT, bytes(0x1000))
        self.cpu.mem_write(OUTPUT, bytes([0xa5]) * 32)
        write(self.cpu, CONTEXT + 0x10, BUFFER, 'Q')
        write(self.cpu, CONTEXT + 0x40, int(not stopped), 'B')

    def poll(self):
        self.cpu.reg_write(registers.UC_X86_REG_R8, OUTPUT + 9)
        HELPERS['execute'](self.cpu, 0xa6c2, (CONTEXT, OUTPUT, OUTPUT + 4, OUTPUT + 8))
        require(not self.lock_depth, 'macOS status poll retained the lock.')
        return bool(self.cpu.reg_read(registers.UC_X86_REG_RAX) & 0xff), bytes(self.cpu.mem_read(OUTPUT, 32))


def packet(phase=1, rate=48000, flag=0, source=0, active=1, reserved=0):
    return bytes([phase, reserved, flag]) + rate.to_bytes(3, 'little') + bytes([source, active])


def status_cases(windows, mac):
    cases = [packet(flag=flag, rate=RATES[flag % 4], reserved=255) for flag in range(256)]
    cases += [packet(rate=rate, source=source, active=active, flag=2) for rate, source, active in
              itertools.product(RATES, (0, 1, 2, 255), (0, 1, 2, 255))]
    for final in cases:
        for reference in (windows, mac):
            reference.reset([packet(phase=0), final])
            ok, actual = reference.poll()
            expected = bytearray([0xa5] * 32)
            if reference is windows:
                struct.pack_into('<I', expected, 0, final[2] & 1)
                expected[9] = final[7]
            else:
                expected[0], expected[9] = int(final[2] != 0), int(final[7] != 0)
            expected[4:8] = final[3:6] + b'\0'
            expected[8] = final[6]
            require(ok and actual == expected and reference.transfers == 2,
                    f'Original status decoding/output isolation differs: {final.hex()}')
        windows.reset([packet(phase=0), final])
        require(windows.poll(manager=True) == (final[2] & 1, int.from_bytes(final[3:6], 'little'),
                                               final[6], final[7]), 'Windows status cache differs.')
    return len(cases)


def phase_cases(windows, mac):
    sequences = ((0, 1), (1, 0, 1), (1, 1, 0, 1), (2, 0, 1), (255, 1, 0, 1),
                 (1, 1, 1), (0, 0), (0, 2), (None,), (0, None), (1, None))
    for phases in sequences:
        packets = [None if phase is None else packet(phase=phase) for phase in phases]
        success = phases[-1] == 1 and 0 in phases
        for reference in (windows, mac):
            reference.reset(packets)
            ok, output = reference.poll()
            require(ok == success and reference.transfers == len(packets),
                    f'Original phase acquisition differs: {phases}')
            if not success and reference is mac:
                require(output == bytes([0xa5]) * 32, 'Failed macOS poll changed caller outputs.')
        windows.reset(packets)
        require(windows.poll(manager=True) == ((0, 48000, 0, 1) if success else (0, 44100, 0, 0)),
                f'Windows polling failure/cache fallback differs: {phases}')
    for reference in (windows, mac):
        reference.reset([], stopped=True)
        ok, output = reference.poll()
        require(not ok and output == bytes([0xa5]) * 32 and not reference.transfers,
                'Stopped original polling context performed a transfer or changed outputs.')
    for connected, reset_ok in itertools.product((False, True), repeat=2):
        windows.reset([packet(phase=0), packet(phase=0), packet()], wait_status=0x102)
        require(not windows.poll()[0] and read(windows.cpu, MODEL + 8) == 1,
                'Windows status timeout did not mark the polling context.')
        windows.port_connected, windows.reset_ok, windows.wait_status = connected, reset_ok, 0
        require(windows.poll()[0] == (connected and reset_ok), 'Windows timeout recovery differs.')
        require(windows.recovery == ['drain', 'port'] + (['reset'] if connected else []) and
                windows.transfers == (3 if connected and reset_ok else 1) and
                read(windows.cpu, MODEL + 8) == int(not (connected and reset_ok)) and not windows.lock_depth,
                'Windows timeout recovery ordering, error state or transfer gate differs.')
    for rate in (0, 32000, 88200, 176400, 352800, 384000, 12345, 0xffffff):
        for reference in (windows, mac):
            reference.reset([packet(phase=0), packet(rate=rate)])
            ok, output = reference.poll()
            require(ok == (reference is windows or rate != 0), 'Original zero-rate rejection differs.')
            if not ok:
                require(output == bytes([0xa5]) * 32, 'Rejected macOS zero rate changed caller outputs.')
        windows.reset([packet(phase=0), packet(rate=rate)])
        require(windows.poll(manager=True) == (0, 44100, 0, 0), 'Unsupported Windows rate did not fall back.')
    return {'phase_sequences_per_reference': len(sequences), 'stopped_cases_per_reference': 1,
            'windows_timeout_recovery_cases': 4, 'unsupported_cache_rates': 8}


def ioctl_cases(windows):
    count = 0
    for stream, secondary, source, measured, host in itertools.product(
            (-1, 0, 1, 3), (-1, 0, 1, 2), (0, 1, 2, 255), RATES, RATES):
        windows.reset([])
        write(windows.cpu, windows.manager + 0x2c, measured)
        write(windows.cpu, PROPS + 0x38, host)
        write(windows.cpu, windows.manager + 0x30, source, 'B')
        write(windows.cpu, windows.manager + 0x31, 255, 'B')
        write(windows.cpu, CONTEXT + 0x371cc, stream, 'i')
        write(windows.cpu, PROPS + 0x108, secondary, 'i')
        busy = stream > 0 or secondary != 0
        manager = bytes(windows.cpu.mem_read(windows.manager, 0x40))
        for code in (0x222050, 0x222320, 0x222324, 0x222340, 0x222344, 0x222348):
            expected = bytearray([0xa5] * 32)
            if code in (0x222050, 0x222324):
                struct.pack_into('<I', expected, 4, measured)
            elif code == 0x222320:
                struct.pack_into('<I', expected, 4, host)
            elif code == 0x222340:
                struct.pack_into('<II', expected, 4, measured, host)
                expected[12:14] = bytes([source, 255])
                struct.pack_into('<I', expected, 16, int(busy))
            else:
                struct.pack_into('<I', expected, 0, int(secondary != 0 if code == 0x222344 else stream > 0))
            windows.cpu.mem_write(OUTPUT, bytes([0xa5]) * 32)
            require(windows.ioctl(code) == 0 and bytes(windows.cpu.mem_read(OUTPUT, 32)) == expected,
                    f'Windows status IOCTL layout differs: {code:x}/{stream}/{secondary}/{source}')
            count += 1
        for rate in (*RATES, 0, 88200):
            expected = bytearray([0xa5] * 32)
            struct.pack_into('<I', expected, 4, rate)
            allowed = rate == host if busy or source else rate in RATES
            struct.pack_into('<I', expected, 8, int(allowed))
            windows.cpu.mem_write(OUTPUT, bytes(expected[:8]) + bytes([0xa5]) * 24)
            require(windows.ioctl(0x22232c) == 0 and bytes(windows.cpu.mem_read(OUTPUT, 32)) == expected,
                    'Windows rate-availability IOCTL differs.')
            count += 1
        require(bytes(windows.cpu.mem_read(windows.manager, 0x40)) == manager and not windows.transfers,
                'Windows getter changed manager state or performed a USB transfer.')
    for code in (0, 0x22204c, 0x222330, 0x22234c, 0xffffffff):
        windows.cpu.mem_write(OUTPUT, bytes([0xa5]) * 32)
        require(windows.ioctl(code) == 0xc000000d and
                bytes(windows.cpu.mem_read(OUTPUT, 32)) == bytes([0xa5]) * 32,
                'Windows status dispatcher accepted an unrelated IOCTL or changed outputs.')
    return {'status_and_availability_requests': count, 'unknown_status_requests': 5}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('windows_kernel', type=Path, help='Windows 1.5.3 x64 RDWM1110.SYS')
    parser.add_argument('--macos', type=Path, required=True, help='macOS 1.5.9 x86_64 audio driver')
    args = parser.parse_args()
    windows, mac = Windows(args.windows_kernel), Mac(args.macos)
    count = status_cases(windows, mac)
    print(json.dumps({'status_cases_per_reference': count, 'windows_cache_cases': count,
                      **phase_cases(windows, mac), **ioctl_cases(windows),
                      'host_dependencies': 'memory-only USB queue/read, drain, port/reset, waits, events and locks',
                      'hardware_validation': False}))


if __name__ == '__main__':
    main()
