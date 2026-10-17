#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Check mixer link/mono switches, master coupling and complete raw restoration."""

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import signal
import subprocess
import tempfile


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def run(command):
    with subprocess.Popen(list(map(str, command)), stdout=subprocess.PIPE, stderr=subprocess.PIPE) as process:
        try:
            output, errors = process.communicate(timeout=20)
        except BaseException:
            process.terminate()
            try:
                process.communicate(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate(timeout=5)
            raise
        require(process.returncode == 0, f'{command}: {process.returncode}: {errors.decode(errors="replace")}')
        return output


def frame(payload):
    body = bytes([1, 0, 0, 0]) + payload
    return bytes.fromhex('f0 41 10 00 00 4d 12') + body + bytes([-sum(body) % 128, 0xf7])


def number(payload, offset, digits, value):
    payload[offset:offset + digits] = bytes(int(digit, 16) for digit in f'{value:0{digits}x}')


def flag(payload, offset, index, value):
    byte, bit = offset + index // 4, 1 << (index % 4)
    payload[byte] = (payload[byte] & ~bit) | (bit if value else 0)


def fixture(original):
    """Distinct, very low volumes reveal coupling without raising monitor levels."""
    payload = bytearray(original)
    for bus in range(4):
        for source in range(2):
            base = source * 0x1f4
            for pair in range(5):
                flag(payload, base + 0x58 + bus * 2, pair, False)
                flag(payload, base + 0x60 + bus * 2, pair, False)
                for side in range(2):
                    channel = pair * 2 + side
                    index = bus * 10 + channel
                    flag(payload, base + 0x68 + bus * 3, channel, bool(side))
                    flag(payload, base + 0x74 + bus * 3, channel, not side)
                    number(payload, base + 0x80 + index * 4, 4, 0x1000 if side == 0 else 0x7000)
                    number(payload, base + 0x120 + index * 6, 6, 0x100 if side == 0 else 0x80)
                    if bus == source == 0:
                        number(payload, 0x210 + channel * 6, 6, 0x40 if side == 0 else 0x20)
            payload[0x404 + source * 0x42 + bus] &= ~1
            for side in range(2):
                number(payload, 0x408 + source * 0x42 + (bus * 2 + side) * 6, 6,
                       (0x400 if side == 0 else 0x200) >> source)
        payload[0x43e + bus] &= ~1
    return payload


@dataclass(frozen=True)
class Switch:
    kind: str
    bus: int
    source: int = 0
    pair: int = 0

    def command(self):
        group = ('input', 'playback')[self.source]
        bus = 'ABCD'[self.bus]
        if self.kind in ('link', 'mono'):
            return ['mixer', group, bus, str(self.pair * 2 + 1), self.kind]
        return ['master', group, bus, '1', 'link' if self.kind == 'master' else 'io-link']

    def expected(self, current, enabled):
        result = bytearray(current)
        base = self.source * 0x1f4
        if self.kind in ('link', 'mono'):
            offset = (0x58 if self.kind == 'link' else 0x60) + base + self.bus * 2
            flag(result, offset, self.pair, enabled)
            if self.kind == 'link' and enabled:
                channel = self.pair * 2
                for packed in (0x68, 0x74):
                    start = base + packed + self.bus * 3
                    bit = (result[start + channel // 4] >> (channel % 4)) & 1
                    flag(result, start, channel + 1, bit)
                start = base + 0x120 + (self.bus * 10 + channel) * 6
                result[start + 6:start + 12] = result[start:start + 6]
                if self.bus == self.source == 0:
                    start = 0x210 + channel * 6
                    result[start + 6:start + 12] = result[start:start + 6]
        elif self.kind == 'master':
            offset = 0x404 + self.source * 0x42 + self.bus
            result[offset] = (result[offset] & ~1) | int(enabled)
            if enabled:
                start = 0x408 + self.source * 0x42 + self.bus * 12
                result[start + 6:start + 12] = result[start:start + 6]
        else:
            result[0x43e + self.bus] = (result[0x43e + self.bus] & ~1) | int(enabled)
            if enabled:
                start = 0x408 + self.bus * 12
                result[start + 0x42:start + 0x42 + 12] = result[start:start + 12]
        return result


def switches():
    for bus in range(4):
        for source in range(2):
            for pair in range(5):
                for kind in ('link', 'mono'):
                    yield Switch(kind, bus, source, pair)
            yield Switch('master', bus, source)
        yield Switch('io', bus)


def interrupt(_signum, _frame):
    raise KeyboardInterrupt


def main():
    root = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bin-dir', type=Path, default=root / 'build/strict')
    parser.add_argument('--port', default='hw:CARD=OCTACAPTURE,DEV=0,SUBDEV=1')
    parser.add_argument('--write', action='store_true', help='check 92 switches and 128 master-volume coupling cases')
    parser.add_argument('--section', choices=('all', 'switches', 'masters'), default='all')
    args = parser.parse_args()

    def state():
        return json.loads(run([args.bin_dir / 'octa-state', args.port]))

    def readback(expected, label):
        observed = bytes.fromhex(state()['payload_hex'])
        differences = [(f'{i:03x}', old, new) for i, (old, new) in
                       enumerate(zip(observed, expected, strict=True)) if old != new]
        require(observed == expected, f'{label}: full-state mismatch {differences[:24]}')

    def write(command, value):
        run([args.bin_dir / 'octa-dsp', *command, str(int(value)), args.port])

    before = state()
    if not args.write:
        switch_count = 0 if args.section == 'masters' else 92
        master_count = 0 if args.section == 'switches' else 128
        print(f'Complete state read. Use --write for {switch_count} switch cycles and '
              f'{master_count} master coupling checks with raw recovery.')
        return
    original = bytes.fromhex(before['payload_hex'])
    require(len(original) == 1146 and original[2] < 3, 'Four-mixer fixture requires 44.1, 48 or 96 kHz')
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, interrupt)
    hardware = root / 'build/hardware'
    hardware.mkdir(parents=True, exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix='mixer-links-', dir=hardware))
    baseline = directory / 'original.syx'
    baseline.write_bytes(frame(original))
    baseline.chmod(0o600)
    case_file = directory / 'test-state.syx'

    def load(payload):
        case_file.write_bytes(frame(payload))
        case_file.chmod(0o600)
        run([args.bin_dir / 'octa-preset', 'restore', case_file, args.port])
        readback(payload, 'test fixture')

    plan = list(switches()) if args.section != 'masters' else []
    for item in plan:
        for enabled in range(2):
            run([args.bin_dir / 'octa-dsp', '--dry-run', *item.command(), str(enabled)])
    base = fixture(original)
    try:
        for item in plan:
            load(base)
            expected = base
            for enabled in (True, False):
                expected = item.expected(expected, enabled)
                write(item.command(), enabled)
                readback(expected, item.command())
            print(f'PASS switch {item.command()}: off/on/off, coupling and complete raw isolation', flush=True)
        for bus, letter in enumerate('ABCD' if args.section != 'switches' else ''):
            for links in range(8):
                incoming, playback, io_link = (bool(links & mask) for mask in (1, 2, 4))
                for source, name in enumerate(('input', 'playback')):
                    for side in range(2):
                        configured = bytearray(base)
                        for offset, enabled in ((0x404, incoming), (0x446, playback), (0x43e, io_link)):
                            configured[offset + bus] = (configured[offset + bus] & ~1) | int(enabled)
                        for group in range(2):
                            for channel in range(2):
                                number(configured, 0x408 + group * 0x42 + (bus * 2 + channel) * 6, 6, 0x400)
                        load(configured)
                        expected = bytearray(configured)
                        linked = (incoming, playback)[source]
                        for group in range(2):
                            if group != source and not io_link:
                                continue
                            for channel in range(2):
                                peer_linked = (incoming, playback)[group] if group != source else False
                                if channel == side or linked or peer_linked:
                                    number(expected, 0x408 + group * 0x42 + (bus * 2 + channel) * 6, 6, 0x200)
                        command = ['master', name, letter, str(side + 1), 'volume']
                        write(command, 0x200)
                        readback(expected, f'{command}, links={links:03b}')
            print(f'PASS master {letter}: all 32 input/playback/IO-link/direction/channel combinations', flush=True)
    finally:
        run([args.bin_dir / 'octa-preset', 'restore', baseline, args.port])
        require(state() == before, f'Original raw state restoration failed; recovery: {baseline}')
        print('PASS exact restoration: original raw state unchanged', flush=True)


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, OSError, ValueError, subprocess.SubprocessError, KeyboardInterrupt) as error:
        print(f'FAIL: {error}')
        raise SystemExit(1) from error
