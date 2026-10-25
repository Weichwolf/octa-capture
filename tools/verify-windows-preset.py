#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Execute the Windows preset loader and kernel bulk sender without hardware I/O."""

import argparse
import itertools
import json
from pathlib import Path
import runpy
import struct
import subprocess
import tempfile

from unicorn import UC_HOOK_CODE
from unicorn import x86_const as registers

HELPERS = runpy.run_path(str(Path(__file__).with_name('verify-pcm-peaks.py')))
CONTEXT, PROPS, DATA, REQUEST, URB, STACK, STOP = (
    HELPERS[name] for name in ('CONTEXT', 'PROPS', 'DATA', 'REQUEST', 'URB', 'STACK', 'STOP'))
GUI_SHA256 = '634c140bc4e49b0d65c70d5156be19573ad3d440ac5ea24e24d3ad59974da406'
HEADER = b'OCTA-CAPTURE Control Panel Data\0Ver100\0'
MODEL_SIZE, MODEL = 0xa34, CONTEXT + 0x20c
require, write = HELPERS['require'], HELPERS['write']


def read(cpu, address, code='I'):
    return struct.unpack('<' + code, cpu.mem_read(address, struct.calcsize('<' + code)))[0]


def returned(cpu, value):
    stack = cpu.reg_read(registers.UC_X86_REG_RSP)
    cpu.reg_write(registers.UC_X86_REG_RAX, value & 0xffffffffffffffff)
    cpu.reg_write(registers.UC_X86_REG_RIP, read(cpu, stack, 'Q'))
    cpu.reg_write(registers.UC_X86_REG_RSP, stack + 8)


def execute(cpu, address, *arguments):
    HELPERS['execute'](cpu, address, (*arguments, *(0 for _ in range(4 - len(arguments)))), windows=True)
    return cpu.reg_read(registers.UC_X86_REG_RAX)


def imports(cpu, path):
    image = HELPERS['read_image'](path, GUI_SHA256)
    header = struct.unpack_from('<I', image, 0x3c)[0]
    base = 0x140000000
    cpu.mem_write(base, image[:struct.unpack_from('<I', image, header + 24 + 60)[0]])
    descriptor = base + read(cpu, base + header + 24 + 120)
    result = {}
    while read(cpu, descriptor):
        lookup, _, _, _, target = struct.unpack('<5I', cpu.mem_read(descriptor, 20))
        index = 0
        while pointer := read(cpu, base + lookup + index * 8, 'Q'):
            if not pointer >> 63:  # Named bindings only; unrelated ordinal imports are not executed.
                name = bytes(cpu.mem_read(base + pointer + 2, 128)).split(b'\0', 1)[0].decode('ascii')
                result[name] = base + target + index * 8
            index += 1
        descriptor += 20
    return result


class Kernel:
    def __init__(self, path):
        self.cpu = HELPERS['cpu_image'](path, HELPERS['WINDOWS_SHA256'], windows=True)
        self.frames = []
        self.cpu.hook_add(UC_HOOK_CODE, self.sent, begin=0x14001b5e0, end=0x14001b5e0)

    def reset(self, model, ready, connected, send_success):
        self.frames.clear()
        self.send_success = send_success
        self.cpu.mem_write(CONTEXT, model)
        self.cached = model[:0x3c]
        for offset, value, code in ((0xa70, ready, 'I'), (0xa48, PROPS, 'Q'), (0xa50, URB, 'Q')):
            write(self.cpu, CONTEXT + offset, value, code)
        write(self.cpu, PROPS + 0x38, connected)

    def sent(self, cpu, _address, _size, _context):
        require(cpu.reg_read(registers.UC_X86_REG_RCX) == URB and
                cpu.reg_read(registers.UC_X86_REG_RDX) == 2, 'Unexpected Windows MIDI transport.')
        require(read(cpu, cpu.reg_read(registers.UC_X86_REG_R9)) == 1159, 'Unexpected bulk frame length.')
        self.frames.append(bytes(cpu.mem_read(cpu.reg_read(registers.UC_X86_REG_R8), 1159)))
        returned(cpu, int(self.send_success))

    def ioctl(self, request, model):
        self.cpu.mem_write(REQUEST, request)
        if model is not None:
            self.cpu.mem_write(DATA, model)
            # Relocate the modeled user pointer into this separate CPU's memory.
            write(self.cpu, REQUEST + 8, DATA, 'Q')
        require(execute(self.cpu, 0x140006550, CONTEXT, URB + 0x100, REQUEST) == 1,
                'Original kernel rejected the preset request.')
        require(bytes(self.cpu.mem_read(CONTEXT, 0x3c)) == self.cached,
                'Original kernel changed cached system properties during preset application.')


class Windows:
    def __init__(self, path, kernel):
        self.cpu = HELPERS['cpu_image'](path, GUI_SHA256, windows=True)
        self.kernel = kernel
        self.stub(STOP + 0x100, self.read_file)
        self.stub(STOP + 0x110, self.close_file)
        self.stub(0x140041b50, self.open_file)
        table = imports(self.cpu, path)
        for index, (name, callback) in enumerate((('CreateFileW', self.open_device),
                                                  ('CloseHandle', self.close_device),
                                                  ('DeviceIoControl', self.ioctl))):
            address = STOP + 0x200 + index * 16
            write(self.cpu, table[name], address, 'Q')
            self.stub(address, callback)
        write(self.cpu, PROPS, PROPS + 0x100, 'Q')
        write(self.cpu, PROPS + 0x170, STOP + 0x100, 'Q')
        write(self.cpu, PROPS + 0x1a0, STOP + 0x110, 'Q')
        self.cpu.mem_write(DATA + 0x8000, 'offline.octa\0'.encode('utf-16le'))

    def stub(self, address, callback):
        self.cpu.hook_add(UC_HOOK_CODE, lambda cpu, *_: returned(cpu, callback()), begin=address, end=address)

    def argument(self, index):
        return self.cpu.reg_read((registers.UC_X86_REG_RCX, registers.UC_X86_REG_RDX,
                                  registers.UC_X86_REG_R8, registers.UC_X86_REG_R9)[index])

    def read_file(self):
        require(self.argument(0) == PROPS and self.argument(2) in (1, 4), 'Unexpected original file read.')
        count = self.argument(2)
        data = self.source[self.position:self.position + count]
        if data:
            self.cpu.mem_write(self.argument(1), data)
        self.position += len(data)
        self.reads += 1
        return len(data)

    def close_file(self):
        require(self.argument(0) == PROPS, 'Unexpected file close.')
        write(self.cpu, PROPS + 0x28, 0)
        return 0

    def open_file(self):
        require(self.argument(0) == PROPS and self.argument(1) == DATA + 0x8000 and
                self.argument(2) == 0, 'Unexpected CFile open.')
        return int(self.file_available)

    def open_device(self):
        require(self.argument(0) == CONTEXT + 4 and self.argument(1) == 0xc0000000 and
                self.argument(2) == 0 and self.argument(3) == 0, 'Unexpected device open.')
        stack = self.cpu.reg_read(registers.UC_X86_REG_RSP)
        require(read(self.cpu, stack + 0x28) == 3 and read(self.cpu, stack + 0x30) == 0x40000000,
                'Unexpected original device open flags.')
        require(bytes(self.cpu.mem_read(self.argument(0), len(self.path_bytes))) == self.path_bytes,
                'Original preset loader changed the selected device path.')
        self.opened_paths.append(self.device_path)
        self.opens += 1
        return 0x55 if self.device_available else 0xffffffffffffffff

    def close_device(self):
        require(self.argument(0) == 0x55, 'Unexpected device handle close.')
        return 1

    def ioctl(self):
        require(self.argument(0) == 0x55 and self.argument(1) == 0x22225c and
                self.argument(3) == 40, 'Unexpected preset IOCTL.')
        pointer, stack = self.argument(2), self.cpu.reg_read(registers.UC_X86_REG_RSP)
        require(read(self.cpu, stack + 0x28, 'Q') == pointer and read(self.cpu, stack + 0x30) == 40 and
                read(self.cpu, stack + 0x40, 'Q') == 0, 'Unexpected preset IOCTL output/overlapped fields.')
        request = bytes(self.cpu.mem_read(pointer, 40))
        selector = struct.unpack_from('<I', request, 16)[0]
        require(selector in (5, 47), 'Unexpected preset operation selector.')
        self.requests.append(selector)
        if self.ioctl_available:
            model = None
            if selector == 5:
                require(struct.unpack_from('<Q', request, 8)[0] == MODEL, 'Unexpected GUI state pointer.')
                model = bytes(self.cpu.mem_read(MODEL, MODEL_SIZE))
            self.kernel.ioctl(request, model)
        write(self.cpu, read(self.cpu, stack + 0x38, 'Q'), 40 if self.ioctl_available else 0)
        return int(self.ioctl_available)

    def load(self, source, current, *, valid=True, file=True, device=True, ioctl=True,
             ready=True, connected=True, midi=True, device_path='\\\\.\\Rdwm1110001'):
        self.source, self.position, self.reads, self.opens, self.requests = source, 0, 0, 0, []
        self.file_available, self.device_available, self.ioctl_available = file, device, ioctl
        self.cpu.mem_write(CONTEXT, bytes(0x2000))
        execute(self.cpu, 0x1400063e0, MODEL)
        # These current-state fields are absent from the preset. Loading must
        # replace them with the new source model's defaults, not retain them.
        for bus in range(4):
            write(self.cpu, MODEL + 0x3f0 + bus * 0x208, 1)
            for source_kind, pair in itertools.product(range(2), range(5)):
                write(self.cpu, MODEL + 0x210 + bus * 0x208 + source_kind * 0xf0 + pair * 0x30, 1)
        self.device_path, self.opened_paths = device_path, []
        self.path_bytes = (device_path + '\0').encode('utf-16le')
        require(len(self.path_bytes) <= 128, 'Oversized selected-device fixture.')
        self.cpu.mem_write(CONTEXT + 4, self.path_bytes)
        write(self.cpu, CONTEXT + 0x1438, int(valid))
        for offset, value in zip((8, 12, 16, 20, 24),
                                 (current[11] & 1, current[12], current[13], current[14] & 1, current[15]), strict=True):
            write(self.cpu, MODEL + offset, value)
        before = bytes(self.cpu.mem_read(MODEL, MODEL_SIZE))
        self.kernel.reset(before, int(ready), int(connected), midi)
        result = execute(self.cpu, 0x140006810, PROPS, DATA + 0x8000, CONTEXT)
        require(bytes(self.cpu.mem_read(MODEL, 0x3c)) == before[:0x3c], 'GUI preset load changed cached properties.')
        if not result:
            require(not self.requests and bytes(self.cpu.mem_read(MODEL, MODEL_SIZE)) == before,
                    'Failed file load changed the live model or issued an IOCTL.')
        return result, list(self.kernel.frames)


def fields():
    """Independent disk layout and one legal boundary per scalar, not random input."""
    specs = [(4, 8)] * 5 + [(4, 4)]
    for bank in range(5):
        specs.extend(((4, 127 if bank == 0 else 12), (4, 127 if bank == 0 else 49)))
    for _pair in range(4):
        specs.append((1, 1))
        for _side in range(2):
            specs.extend([(1, 1)] + [(4, maximum) for maximum in (50, 25, 45, 50, 8, 74)] + [(1, 1)] * 4 + [(4, 100)])
    for bus in range(4):
        specs.append((1, 1))
        for source in range(2):
            for _pair in range(5):
                specs.append((1, 1))
                for _side in range(2):
                    specs.extend([(1, 1)] * 2 + [(4, 0x7fffff if (bus, source) == (0, 0) else 0xffffffff),
                                                (4, 0x8000), (4, 0x7fffff)])
        for source in range(2):
            specs.extend(((1, 1), (4, 0x7fffff if (bus, source) == (0, 0) else 0xffffffff),
                          (4, 0x7fffff), (4, 0x7fffff)))
    require(len(specs) == 592 and sum(size for size, _ in specs) == 1600, 'Incomplete fixed scalar matrix.')
    offset = len(HEADER)
    for size, maximum in specs:
        yield offset, size, maximum
        offset += size


def fixed_cases(profile, current):
    yield 'profile', profile, current
    yield 'zero', HEADER + bytes(1600), current
    for offset, size, maximum in fields():
        data = bytearray(profile)
        value = int.from_bytes(data[offset:offset + size], 'little')
        boundary = 0x80 if size == 1 else (0 if value == maximum else maximum)
        data[offset:offset + size] = boundary.to_bytes(size, 'little')
        yield f'field-{offset}', bytes(data), current
    for rate, first, fourth, second, fifth in itertools.product(range(4), (0, 1), (0, 1), (0, 127), (0, 127)):
        frame = bytearray(current)
        frame[11:16] = bytes((first, second, rate, fourth, fifth))
        frame[-2] = -sum(frame[7:-2]) % 128
        yield f'prefix-{rate}-{first}-{fourth}-{second}-{fifth}', profile, bytes(frame)


def compare(windows, binary, fixtures):
    profile = (fixtures / 'preset-import-profile.octa').read_bytes()
    current = (fixtures / 'preset-export-bulk.syx').read_bytes()
    require(len(profile) == 1639 and profile.startswith(HEADER) and len(current) == 1159,
            'Expected synthetic Ver100/current-bulk fixtures.')
    count = 0
    with tempfile.TemporaryDirectory(prefix='octa-windows-preset-') as directory:
        preset, bulk = (Path(directory) / name for name in ('input.octa', 'current.syx'))
        for name, data, frame in fixed_cases(profile, current):
            preset.write_bytes(data)
            bulk.write_bytes(frame)
            expected = subprocess.run([str(binary), 'prepare', str(preset), str(bulk)],
                                      check=True, capture_output=True, timeout=20).stdout
            result, sent = windows.load(data, frame)
            require(result == 1 and windows.position == 1639 and windows.reads == 631 and
                    windows.requests == [5, 47] and sent == [expected] and len(expected) == 1159,
                    f'Original Windows/C++ preset application differs: {name}')
            if name == 'profile':
                require(expected == (fixtures / 'preset-import-reference.syx').read_bytes(),
                        'Windows/C++ import differs from the existing macOS reference fixture.')
            count += 1
    return count, profile, current


def failure_cases(windows, profile, current):
    cuts = (0, 31, 32, 38, 39, 40, 42, 59, 103, 371, 1638)
    inputs = [profile[:cut] for cut in cuts] + [b'X' + profile[1:]]
    for source in inputs:
        require(windows.load(source, current) == (0, []), 'Original accepted a truncated/wrong-magic preset.')
    for options in ({'valid': False}, {'file': False}):
        require(windows.load(profile, current, **options) == (0, []), 'Original file/connection guard differs.')
    for options in ({'device': False}, {'ioctl': False}):
        result, frames = windows.load(profile, current, **options)
        require(result == 1 and not frames and windows.opens == 2,
                'Original no longer reports success despite failed host device I/O.')
    result, attempted = windows.load(profile, current, midi=False)
    require(result == 1 and len(attempted) == 1, 'Original propagated the final MIDI transport failure.')
    for ready, connected in itertools.product((False, True), repeat=2):
        result, frames = windows.load(profile, current, ready=ready, connected=connected)
        require(result == 1 and len(frames) == int(ready and connected), 'Original MIDI readiness gate differs.')
    return {'file_or_context_rejections': len(inputs) + 2, 'unreported_host_failures': 3, 'transport_gates': 4}


def stricter_format_cases(windows, binary, profile, current):
    variants = (profile[:32] + b'Ver101\0' + profile[39:], profile + b'\0')
    with tempfile.TemporaryDirectory(prefix='octa-preset-format-') as directory:
        preset, bulk = (Path(directory) / name for name in ('input.octa', 'current.syx'))
        bulk.write_bytes(current)
        for source in variants:
            result, frames = windows.load(source, current)
            require(result == 1 and windows.position == 1639 and len(frames) == 1,
                    'Original changed its permissive version/trailing-data handling.')
            preset.write_bytes(source)
            prepared = subprocess.run([str(binary), 'prepare', str(preset), str(bulk)],
                                      capture_output=True, timeout=20)
            require(prepared.returncode == 1 and not prepared.stdout,
                    'Linux accepted an unsupported version or trailing preset data.')
    return len(variants)


def unit_cases(windows, profile, current):
    _, expected = windows.load(profile, current)
    require(len(expected) == 1, 'Expected one reference import frame.')
    count = 0
    for prefix, slot in itertools.product(('Rdwm1110', 'Rdwm1110Ex'), range(1, 5)):
        path = f'\\\\.\\{prefix}{slot:03d}'
        result, frames = windows.load(profile, current, device_path=path)
        require(result == 1 and frames == expected and windows.requests == [5, 47] and
                windows.opened_paths == [path, path] and windows.opens == 2,
                'Original preset import did not follow its selected base/EXP context.')
        count += 1
    return count


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('windows_gui', type=Path)
    parser.add_argument('windows_kernel', type=Path)
    parser.add_argument('preset_binary', type=Path, help='built octa-preset; only offline prepare is invoked')
    parser.add_argument('--fixtures', type=Path, default=Path(__file__).resolve().parent.parent / 'test/data')
    args = parser.parse_args()
    windows = Windows(args.windows_gui, Kernel(args.windows_kernel))
    count, profile, current = compare(windows, args.preset_binary.resolve(), args.fixtures)
    print(json.dumps({'preset_import_cases': count, **failure_cases(windows, profile, current),
                      'selected_base_exp_paths': unit_cases(windows, profile, current),
                      'stricter_linux_format_cases': stricter_format_cases(
                          windows, args.preset_binary.resolve(), profile, current),
                      'file_reads_per_complete_case': 631, 'kernel_requests': [5, 47],
                      'original_gui_success_does_not_prove_device_write': True,
                      'host_dependencies': 'memory-only CFile and device APIs; final MIDI transport sink',
                      'hardware_validation': False}))


if __name__ == '__main__':
    main()
