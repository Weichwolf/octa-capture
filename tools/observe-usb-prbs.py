#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Observe bounded PRBS11 discontinuities in OCTA-CAPTURE USB transfers, in RAM only."""

import argparse
from collections import Counter
import ctypes
import errno
import fcntl
import json
import os
from pathlib import Path
import re
import select
import signal
import struct
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parent.parent
CAPACITY = 8192
HEADER_SIZE = 64
MAX_ERRORS = 100


def next_pattern(state):
    return ((state << 1) & 2047) | (((state >> 10) ^ (state >> 8)) & 1)


class Pattern:
    """Arm after 64 matching samples; report gaps that resume within 512 samples."""

    def __init__(self, direction, report):
        self.direction = direction
        self.report = report
        self.frames = 0
        self.verified = 0
        self.errors = []
        self.total_errors = 0
        self.reset()

    def reset(self):
        self.previous = None
        self.valid = 0
        self.pending = None

    def sample(self, value, stamp, channel):
        self.frames += 1
        state = value + 1024
        if self.pending and self.frames - self.pending['frame'] > 512:
            self.pending = None
        expected = None if self.previous is None else next_pattern(self.previous)
        if state != expected and self.valid >= 64:
            self.pending = {'frame': self.frames, 'time': stamp,
                            'zero_sample': value == 0, 'source_channel': channel}
        if not 1 <= state <= 2047:
            self.previous = None
            self.valid = 0
            return
        self.valid = self.valid + 1 if state == expected else 1
        self.previous = state
        if self.valid >= 64:
            self.verified += 1
            if self.pending:
                record = {**self.pending, 'direction': self.direction,
                          'resynchronized_after_samples': self.frames - self.pending['frame']}
                self.total_errors += 1
                if len(self.errors) < MAX_ERRORS:
                    self.errors.append(record)
                    self.report({'continuity_error': record})
                self.pending = None


class Decoder:
    def __init__(self, device, bus, report):
        self.device, self.bus = device, bus
        self.output = Pattern('submitted-output', report)
        self.input = Pattern('completed-input-return', report)
        self.events = Counter()
        self.packet_errors = Counter()
        self.transfer_errors = Counter()
        self.invalid = 0
        self.channel = None
        self.zeros = 0

    def outgoing(self, payload, stamp):
        if len(payload) % 30:
            self.invalid += 1
            return
        for position in range(0, len(payload), 30):
            frame = payload[position:position + 30]
            if not any(frame):
                self.zeros += 1
                if self.channel is not None:
                    self.output.sample(0, stamp, self.channel + 1)
                if self.zeros > 512:
                    self.channel = None
                    self.output.reset()
                continue
            self.zeros = 0
            if self.channel is None:
                self.channel = next(index for index in range(10) if any(frame[index * 3:index * 3 + 3]))
                self.output.reset()
            start = self.channel * 3
            self.output.sample(int.from_bytes(frame[start:start + 3], 'little', signed=True),
                               stamp, self.channel + 1)

    def incoming(self, packet, descriptors, base, stamp):
        for index in range(descriptors):
            error, start, size = struct.unpack_from('<iII', packet, index * 16)
            if error:
                self.packet_errors[error] += 1
                continue
            if size % 36 or base + start + size > len(packet):
                self.invalid += 1
                continue
            for position in range(base + start, base + start + size, 36):
                self.input.sample(int.from_bytes(packet[position + 30:position + 33], 'little', signed=True),
                                  stamp, None if self.channel is None else self.channel + 1)

    def event(self, header, data):
        if len(header) != HEADER_SIZE:
            self.invalid += 1
            return
        event, transfer, endpoint, device, bus = struct.unpack_from('<4BH', header, 8)
        if transfer != 0 or device != self.device or bus != self.bus or endpoint not in (5, 133):
            return
        status = struct.unpack_from('<i', header, 28)[0]
        self.events[chr(event), endpoint, status] += 1
        if (event == 67 and status not in (0, -2, -104)) or (event == 83 and status not in (0, -115)):
            self.transfer_errors[endpoint, status] += 1
        if (event, endpoint) != (83, 5) and (event, endpoint, status) != (67, 133, 0):
            return
        captured = struct.unpack_from('<I', header, 36)[0]
        descriptors = struct.unpack_from('<I', header, 60)[0]
        base = descriptors * 16
        if captured > CAPACITY or captured > len(data) or base > captured:
            self.invalid += 1
            return
        seconds, microseconds = struct.unpack_from('<qi', header, 16)
        stamp = seconds + microseconds / 1e6
        packet = data[:captured]
        if endpoint == 5:
            self.outgoing(packet[base:], stamp)
        else:
            self.incoming(packet, descriptors, base, stamp)

    def summary(self, queued, dropped):
        return {'events': {str(key): count for key, count in self.events.items()},
                'outgoing_pattern_errors': self.output.errors,
                'incoming_pattern_errors': self.input.errors,
                'outgoing_error_count': self.output.total_errors,
                'incoming_error_count': self.input.total_errors,
                'outgoing_verified_pattern_samples': self.output.verified,
                'incoming_verified_pattern_samples': self.input.verified,
                'packet_errors': dict(self.packet_errors),
                'transfer_errors': {str(key): count for key, count in self.transfer_errors.items()},
                'invalid_payloads': self.invalid,
                'reader_queued': queued, 'reader_dropped': dropped, 'audio_payload_files': False}


class Request(ctypes.Structure):
    _fields_ = [('header', ctypes.c_void_p), ('data', ctypes.c_void_p), ('capacity', ctypes.c_size_t)]


def identify(device, binary):
    if not re.fullmatch(r'[0-9]+-[0-9]+(?:\.[0-9]+)*', device):
        raise ValueError('Use a USB device name such as 1-1, without an interface suffix.')
    path = Path('/sys/bus/usb/devices') / device
    if (path.joinpath('idVendor').read_text().strip(), path.joinpath('idProduct').read_text().strip()) != ('0582', '0120'):
        raise ValueError('The selected USB device is not an OCTA-CAPTURE.')
    cards = [card for card in Path('/sys/class/sound').glob('card[0-9]*')
             if path.resolve() in (card / 'device').resolve().parents]
    if len(cards) != 1:
        raise ValueError('Expected exactly one ALSA card for the selected USB device.')
    port = 'hw:' + cards[0].name.removeprefix('card')
    status = json.loads(subprocess.run([str(binary), port], capture_output=True, check=True, timeout=10).stdout)
    if status['sample_rate_hz'] not in (44100, 48000, 96000):
        raise ValueError('The digital return requires 44.1/48/96 kHz; do not select 192 kHz while observing.')
    return int((path / 'busnum').read_text()), int((path / 'devnum').read_text())


def observe(decoder, seconds):
    library = ctypes.CDLL(None, use_errno=True)
    library.ioctl.restype = ctypes.c_int
    header = ctypes.create_string_buffer(HEADER_SIZE)
    data = ctypes.create_string_buffer(CAPACITY)
    request = Request(ctypes.addressof(header), ctypes.addressof(data), CAPACITY)
    stopped = False

    def stop(_signum, _frame):
        nonlocal stopped
        stopped = True

    descriptor = os.open(f'/dev/usbmon{decoder.bus}', os.O_RDWR | os.O_NONBLOCK)
    previous = {number: signal.signal(number, stop) for number in (signal.SIGINT, signal.SIGTERM)}
    try:
        if library.ioctl(descriptor, 0x9204, ctypes.c_ulong(16 * 1024 * 1024)) < 0:
            raise OSError(ctypes.get_errno(), 'Cannot set USB monitor ring size.')
        deadline = time.monotonic() + seconds
        while not stopped and time.monotonic() < deadline:
            if library.ioctl(descriptor, ctypes.c_ulong(0x4000920a | (ctypes.sizeof(request) << 16)),
                             ctypes.byref(request)) < 0:
                code = ctypes.get_errno()
                if code == errno.EAGAIN:
                    select.select([descriptor], [], [], 0.05)
                    continue
                if code == errno.EINTR:
                    continue
                raise OSError(code, 'Cannot read USB monitor event.')
            decoder.event(header.raw, data.raw)
        stats = bytearray(8)
        fcntl.ioctl(descriptor, 0x80089203, stats, True)
        return decoder.summary(*struct.unpack('<II', stats))
    finally:
        os.close(descriptor)
        for number, handler in previous.items():
            signal.signal(number, handler)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--device', required=True, help='USB sysfs name, for example 1-1')
    parser.add_argument('--seconds', type=int, default=360, help='observation duration, 1..3600')
    parser.add_argument('--status-binary', type=Path, default=ROOT / 'build/release/octa-status')
    args = parser.parse_args()
    if not 1 <= args.seconds <= 3600:
        parser.error('--seconds must be 1..3600.')
    bus, device = identify(args.device, args.status_binary)
    def report(record):
        print(json.dumps(record), flush=True)

    decoder = Decoder(device, bus, report)
    summary = observe(decoder, args.seconds)
    report({'summary': summary})
    if (summary['outgoing_error_count'] or summary['incoming_error_count'] or summary['packet_errors'] or summary['transfer_errors'] or
            summary['invalid_payloads'] or summary['reader_dropped']):
        return 1
    if not summary['outgoing_verified_pattern_samples'] or not summary['incoming_verified_pattern_samples']:
        return 2
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(f'USB PRBS observation failed: {error}', file=sys.stderr)
        raise SystemExit(1) from error
