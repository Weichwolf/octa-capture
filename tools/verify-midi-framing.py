#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Compare original macOS MIDI framing with pinned ALSA functions offline."""

import argparse
import errno
import hashlib
import itertools
import json
from pathlib import Path
import re
import runpy
import struct
import subprocess

from unicorn import UC_HOOK_CODE
from unicorn import x86_const as registers

HELPERS = runpy.run_path(str(Path(__file__).with_name('verify-pcm-peaks.py')))
CONTEXT, PROPS, DATA, STOP = (HELPERS[name] for name in ('CONTEXT', 'PROPS', 'DATA', 'STOP'))
MIDI_SHA256 = '4dc99de17c8228fcde71dd954a954df9c722b42633a073d29bceec06d6b98927'
ALSA_SHA256 = '4cb3b394c6e226e85b6a907603e294a1ebed366bd924afced2dda169e4733dba'
LENGTHS = (0, 0, 2, 3, 3, 1, 2, 3, 3, 3, 3, 3, 2, 2, 3, 1)
REALTIME = (0xf8, 0xfa, 0xfb, 0xfc, 0xfe, 0xff)
require, write = HELPERS['require'], HELPERS['write']


def read(cpu, address, code='I'):
    return struct.unpack('<' + code, cpu.mem_read(address, struct.calcsize('<' + code)))[0]


def returned(cpu, value):
    stack = cpu.reg_read(registers.UC_X86_REG_RSP)
    cpu.reg_write(registers.UC_X86_REG_RAX, value)
    cpu.reg_write(registers.UC_X86_REG_RIP, read(cpu, stack, 'Q'))
    cpu.reg_write(registers.UC_X86_REG_RSP, stack + 8)


def extract_alsa(path):
    source = path.read_text()
    names = ('snd_usbmidi_output_standard_packet', 'snd_usbmidi_transmit_byte',
             'snd_usbmidi_input_data', 'snd_usbmidi_standard_input', 'snd_usbmidi_urb_error',
             'snd_usbmidi_submit_urb', 'snd_usbmidi_in_urb_complete')
    functions = []
    for name in names:
        match = re.search(r'^static (?:void|int) ' + name + r'\(', source, re.M)
        require(match is not None, f'Missing ALSA function: {name}')
        start = match.start()
        body, level = source.index('\n{', start) + 1, 0
        for end in range(body, len(source)):
            level += (source[end] == '{') - (source[end] == '}')
            if not level:
                break
        functions.append(source[start:end + 1])
    table = source.index('static const uint8_t snd_usbmidi_cin_length[]')
    constants = [source[table:source.index('};', table) + 2]]
    constants += re.findall(r'^#define STATE_\w+\s+\d+.*$', source, re.M)
    constants += re.findall(r'^#define ERROR_DELAY_JIFFIES.*$', source, re.M)
    digest = hashlib.sha256('\n'.join(functions + constants).encode()).hexdigest()
    require(digest == ALSA_SHA256, 'Unrecognized ALSA MIDI functions; audit before compiling.')
    notice = source[:source.index('*/') + 2]
    require(notice.startswith('/*') and 'Copyright (c) 2002-2009 Clemens Ladisch' in notice,
            'Missing original ALSA license notice.')
    return notice + '\n#include <errno.h>\n' + '\n'.join(constants + functions) + '\n'


def build_host(source, directory, compiler):
    directory.mkdir(parents=True, exist_ok=True)
    (directory / 'alsa-midi-reference.inc').write_text(extract_alsa(source))
    wrapper = Path(__file__).with_name('reference-midi-host.c')
    subprocess.run(['clang-format-19', '--dry-run', '--Werror', str(wrapper)], check=True, timeout=10)
    tidy = subprocess.run(['clang-tidy-19', str(wrapper), '--', '-std=c11', '-I', str(directory)],
                          capture_output=True, text=True, timeout=30)
    require(tidy.returncode == 0, tidy.stdout + tidy.stderr)
    binary = directory / 'reference-midi-host'
    flags = ('-std=c11 -O1 -g -Wall -Wextra -Wpedantic -Werror -Wconversion -Wsign-conversion '
             '-Wshadow -Wformat=2 -Wundef -Wcast-align -Wcast-qual -Wdouble-promotion -Wnull-dereference '
             '-Wimplicit-fallthrough -Wvla -Wstrict-prototypes -Wmissing-prototypes '
             '-fstack-protector-strong -fstack-clash-protection -ffp-contract=off -fno-omit-frame-pointer '
             '-D_FORTIFY_SOURCE=3 -fsanitize=address,undefined -fno-sanitize-recover=all '
             '-fPIE -pie -Wl,-z,relro,-z,now,-z,noexecstack').split()
    subprocess.run([compiler, *flags, '-I', str(directory), str(wrapper),
                    '-o', str(binary)], check=True, timeout=30)
    return binary.resolve()


class Mac:
    def __init__(self, path):
        self.cpu = HELPERS['cpu_image'](path, MIDI_SHA256)
        self.packets, self.received = [], [bytearray(), bytearray()]
        self.rx = CONTEXT + 0x2000
        self.cpu.hook_add(UC_HOOK_CODE, self.out, begin=0xcee0, end=0xcee0)
        write(self.cpu, 0x10038, PROPS, 'Q')
        write(self.cpu, PROPS, 0x1122334455667788, 'Q')
        self.stub(0xe118, lambda: self.cpu.reg_read(registers.UC_X86_REG_RAX))  # Stack probe.
        self.stub(0xe14e, lambda: 1234)  # Fixed host timestamp, not timing verification.
        self.stub(0xe082, self.entities)
        self.stub(0xe07c, self.entity)
        self.stub(0xe0ac, lambda: 1)
        self.stub(0xe0b2, self.source)
        self.stub(0xe0dc, self.packet_init)
        self.stub(0xe0d6, self.packet_add)
        self.stub(0xe160, self.copy)
        self.stub(0xe0e2, self.delivered)
        for address in (0x14550, 0x14590):
            require(bytes(self.cpu.mem_read(address, 64)) == struct.pack('<16I', *range(16)),
                    'Original MIDI cable mapping is not identity.')
        write(self.cpu, self.rx + 8, 3000)
        HELPERS['execute'](self.cpu, 0xd8cc, (self.rx, 0, 0, 0))
        require(bytes(self.cpu.mem_read(self.rx + 12, 64)) == struct.pack('<16I', 100, 101, *([0] * 14)),
                'Original SetRxEps did not map MIDI/CTRL to the two modeled entities.')

    def argument(self, index):
        return self.cpu.reg_read((registers.UC_X86_REG_RDI, registers.UC_X86_REG_RSI,
                                  registers.UC_X86_REG_RDX, registers.UC_X86_REG_RCX,
                                  registers.UC_X86_REG_R8, registers.UC_X86_REG_R9)[index])

    def stub(self, address, callback):
        self.cpu.hook_add(UC_HOOK_CODE, lambda cpu, *_: returned(cpu, callback()), begin=address, end=address)

    def entities(self):
        require(self.argument(0) == 3000, 'Unexpected modeled MIDI device.')
        return 2

    def entity(self):
        require(self.argument(0) == 3000 and self.argument(1) < 2, 'Unexpected MIDI entity index.')
        return 200 + self.argument(1)

    def source(self):
        require(self.argument(0) in (200, 201) and self.argument(1) == 0, 'Unexpected source selector.')
        return 100 + self.argument(0) - 200

    def packet_init(self):
        write(self.cpu, self.argument(0), 0)
        return self.argument(0) + 4

    def packet_add(self):
        base, size, previous, stamp, length, data = (self.argument(index) for index in range(6))
        count = read(self.cpu, base)
        packet = base + 4 if not count else (previous + 10 + read(self.cpu, previous + 8, 'H') + 3) & ~3
        require(size == 4096 and packet + 10 + length <= base + size, 'Unexpected CoreMIDI packet allocation.')
        write(self.cpu, packet, stamp, 'Q')
        write(self.cpu, packet + 8, length, 'H')
        self.cpu.mem_write(packet + 10, bytes(self.cpu.mem_read(data, length)))
        write(self.cpu, base, count + 1)
        return packet

    def copy(self):
        require(self.argument(2) <= 4096, 'Unexpected CoreMIDI copy length.')
        self.cpu.mem_write(self.argument(0), bytes(self.cpu.mem_read(self.argument(1), self.argument(2))))
        return self.argument(0)

    def delivered(self):
        endpoint, base = self.argument(0), self.argument(1)
        require(endpoint in (100, 101), 'Unexpected original receive endpoint.')
        packet = base + 4
        for _ in range(read(self.cpu, base)):
            length = read(self.cpu, packet + 8, 'H')
            require(packet + 10 + length <= base + 4096, 'CoreMIDI packet exceeds its modeled list.')
            self.received[endpoint - 100].extend(self.cpu.mem_read(packet + 10, length))
            packet = (packet + 10 + length + 3) & ~3
        return 0

    def out(self, cpu, _address, _size, _context):
        pointer = cpu.reg_read(registers.UC_X86_REG_RDI)
        self.packets.append(bytes(cpu.mem_read(pointer + 12, 4)))

    def transmit(self, events):
        self.packets.clear()
        for cable in range(16):
            context = CONTEXT + cable * 0x100
            self.cpu.mem_write(context, bytes(0x30))
            write(self.cpu, context, 0x109a8, 'Q')
            write(self.cpu, context + 0x18, DATA + cable * 0x1000, 'Q')
            write(self.cpu, context + 0x24, 1024)
            self.cpu.mem_write(context + 0x10, bytes([cable << 4]))
        for cable, byte in events:
            HELPERS['execute'](self.cpu, 0xcf08, (CONTEXT + cable * 0x100, byte, 0, 0))
        return b''.join(self.packets)

    def receive(self, packets):
        self.received = [bytearray(), bytearray()]
        # Keep each callback within the high-speed MIDI endpoint's 512 bytes.
        chunks = [packets[index:index + 512] for index in range(0, len(packets), 512)] or [b'']
        for chunk in chunks:
            if chunk:
                self.cpu.mem_write(DATA, chunk)
            HELPERS['execute'](self.cpu, 0xc8d8, (self.rx, DATA, len(chunk), 0))
        return tuple(map(bytes, self.received))


def packet(cable, cin, data):
    return bytes([cable << 4 | cin]) + data + bytes(3 - len(data))


def events(cable, data):
    return tuple((cable, byte) for byte in data)


def vectors():
    for cable, channel, kind in itertools.product((0, 1), range(16), range(8, 15)):
        length = 1 if kind in (12, 13) else 2
        for values in itertools.product((0, 64, 127), repeat=length):
            status = kind << 4 | channel
            second = tuple(127 - value for value in values)
            raw = bytes([status, *values, *second])
            expected = packet(cable, kind, bytes([status, *values])) + packet(cable, kind, bytes([status, *second]))
            yield 'running-status', events(cable, raw), expected
    for cable in (0, 1):
        for status, values, cin in ((0xf1, (127,), 2), (0xf2, (0, 127), 3),
                                     (0xf3, (64,), 2), (0xf6, (), 5)):
            data = bytes([status, *values])
            yield 'system-common', events(cable, data), packet(cable, cin, data)
        for kind, realtime in itertools.product(range(8, 15), REALTIME):
            data = bytes([kind << 4 | 15, 64] + ([] if kind in (12, 13) else [127]))
            for position in range(len(data) + 1):
                raw = data[:position] + bytes([realtime]) + data[position:]
                message, clock = packet(cable, kind, data), packet(cable, 15, bytes([realtime]))
                yield 'channel-realtime', events(cable, raw), message + clock if position == len(data) else clock + message
        lengths = (*range(17), 19, 20, 21, 61, 62, 63, 64, 127, 128, 129, 255, 256, 257,
                   511, 512, 513, 1157)
        for length in lengths:
            data = bytes([0xf0]) + bytes((index * 17 + 3) % 128 for index in range(length)) + b'\xf7'
            chunks = [data[index:index + 3] for index in range(0, len(data), 3)]
            wire = b''.join(packet(cable, 4 + len(chunk) if chunk[-1] == 0xf7 else 4, chunk) for chunk in chunks)
            yield 'sysex', events(cable, data), wire
            if length < 7:
                for realtime, position in itertools.product(REALTIME, range(len(data) + 1)):
                    raw = data[:position] + bytes([realtime]) + data[position:]
                    completed = len(chunks) if position == len(data) else position // 3
                    expected = wire[:completed * 4] + packet(cable, 15, bytes([realtime])) + wire[completed * 4:]
                    yield 'sysex-realtime', events(cable, raw), expected
    interleaved = ((0, 0x90), (1, 0xf0), (0, 60), (1, 0x7d), (0, 127),
                   (1, 1), (0, 61), (1, 2), (0, 0), (1, 0xf7))
    yield 'interleaved-cables', interleaved, bytes.fromhex('09903c7f14f07d0109903d001602f700')


def receiver_expected(wire, triggered=3):
    output = [bytearray(), bytearray()]
    for index in range(0, len(wire) - 3, 4):
        cable, cin = wire[index] >> 4, wire[index] & 15
        if cable < 2 and triggered & 1 << cable:
            output[cable].extend(wire[index + 1:index + 1 + LENGTHS[cin]])
    return tuple(map(bytes, output))


def host_records(binary, records):
    raw = b''.join(bytes([mode, mask]) + struct.pack('<I', len(data)) + data for mode, mask, data in records)
    result = subprocess.run([str(binary)], input=raw, capture_output=True, check=True, timeout=60)
    require(not result.stderr, 'Unexpected sanitizer/host diagnostic: ' + result.stderr.decode(errors='replace'))
    offset, output = 0, []
    for mode, _mask, _data in records:
        parts = []
        for _ in range(2 if mode == 1 else 1):
            require(offset + 4 <= len(result.stdout), 'Missing host result length.')
            length = struct.unpack_from('<I', result.stdout, offset)[0]
            offset += 4
            require(length <= 65536 and offset + length <= len(result.stdout), 'Invalid host result length.')
            parts.append(result.stdout[offset:offset + length])
            offset += length
        output.append(tuple(parts) if mode == 1 else parts[0])
    require(offset == len(result.stdout), 'Trailing host results.')
    return output


def compare(mac, binary):
    cases = list(vectors())
    records = [(0, 0, bytes(byte for event in event_list for byte in event)) for _, event_list, _ in cases]
    transmitted = host_records(binary, records)
    counts = {}
    for (name, event_list, expected), actual in zip(cases, transmitted, strict=True):
        require(mac.transmit(event_list) == actual == expected, f'Original/ALSA MIDI output differs: {name}, {event_list}')
        counts[name] = counts.get(name, 0) + 1
    received = host_records(binary, [(1, 3, wire) for wire in transmitted])
    for index, (wire, actual) in enumerate(zip(transmitted, received, strict=True)):
        require(mac.receive(wire) == actual == receiver_expected(wire), f'Original/ALSA MIDI input differs: vector {index}')
    return counts


def input_gates(mac, binary):
    wire = bytes.fromhex('0990407f1990417f')
    cases = [(wire + bytes(trailing), mask) for trailing, mask in itertools.product(range(4), range(4))]
    cases += [(packet(cable, 9, bytes([0x90, 64, 127])), 3) for cable in range(2, 16)]
    received = host_records(binary, [(1, mask, data) for data, mask in cases])
    for (data, mask), actual in zip(cases, received, strict=True):
        require(actual == receiver_expected(data, mask), 'ALSA input trigger/unmapped/trailing guard differs.')
        require(mac.receive(data) == receiver_expected(data), 'Original unmapped/trailing packet handling differs.')
    return len(cases)


def completion_cases(binary):
    stopped = (-errno.ENOENT, -errno.ECONNRESET, -errno.ESHUTDOWN, -errno.ENODEV)
    retry = (-errno.EPROTO, -errno.ETIME, -errno.EILSEQ)
    statuses = (0, *stopped, *retry, -errno.EPIPE, -errno.ENOMEM, -errno.EOVERFLOW)
    cases = list(itertools.product(statuses, (0, -errno.ENODEV, -errno.ENOMEM)))
    output = host_records(binary, [(2, 0, struct.pack('<ii', *case)) for case in cases])
    for (status, _submit), actual in zip(cases, output, strict=True):
        delivered = 3 if status == 0 else 0
        expected = bytes([int(status not in stopped + retry), int(status in retry), int(status in retry),
                          delivered, delivered, 100 if status in retry else 0])
        require(actual == expected, f'ALSA completion decision differs: {status}')
    return len(cases)


def aborted_sysex(mac, binary):
    cases = []
    for cable, length in itertools.product((0, 1), range(6)):
        raw = b'\xf0' + bytes([0x7d] * length) + bytes.fromhex('90407f')
        cases.append((cable, length, raw))
    results = host_records(binary, [(0, 0, bytes(byte for event in events(cable, raw) for byte in event))
                                   for cable, _length, raw in cases])
    for (cable, length, raw), linux in zip(cases, results, strict=True):
        original = mac.transmit(events(cable, raw))
        prefix = b'\xf0' + bytes([0x7d] * length)
        complete = len(prefix) // 3 * 3
        expected = b''.join(packet(cable, 4, prefix[index:index + 3]) for index in range(0, complete, 3))
        note = packet(cable, 9, bytes.fromhex('90407f'))
        require(linux == expected + note, 'ALSA interrupted-SysEx behavior differs.')
        final = prefix[complete:] + b'\xf7'
        require(original == expected + packet(cable, 4 + len(final), final) + note,
                'Original no longer closes interrupted SysEx with F7.')
    return len(cases)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mac_midi', type=Path, help='extracted macOS 1.5.9 x86_64 MIDI driver')
    parser.add_argument('alsa_midi', type=Path, help='midi.c from the supported ALSA snapshot/private module copy')
    parser.add_argument('--build-dir', type=Path, default=Path(__file__).resolve().parent.parent / 'build/midi-reference')
    parser.add_argument('--cc', default='clang-19')
    args = parser.parse_args()
    binary, mac = build_host(args.alsa_midi, args.build_dir.resolve(), args.cc), Mac(args.mac_midi)
    counts = compare(mac, binary)
    print(json.dumps({'transmit_and_receive_cases': sum(counts.values()), 'families': counts,
                      'input_gate_cases': input_gates(mac, binary), 'completion_cases': completion_cases(binary),
                      'documented_aborted_sysex_differences': aborted_sysex(mac, binary),
                      'alsa_functions_sha256': ALSA_SHA256, 'sanitizers': ['address', 'undefined'],
                      'host_dependencies': 'CoreMIDI packet lists/clock/endpoints; serial ALSA ports, USB submission/timers',
                      'hardware_validation': False, 'kernel_sanitizer_validation': False}))


if __name__ == '__main__':
    main()
