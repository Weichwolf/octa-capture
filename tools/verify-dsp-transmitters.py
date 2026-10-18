#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Compare original macOS DSP transmitters and our CLI offline on either architecture."""

import argparse
from collections import Counter
import json
from pathlib import Path
import subprocess

from reference_midi import verify
from reference_arm64 import verify as verify_arm64

TRANSMITTERS = {
    'link': (0x10001cb8c, 0x10001f5d4),
    'mute': (0x10001d980, 0x10002032c),
    'solo': (0x10001dfa2, 0x1000209e0),
    'pan': (0x1000208f6, 0x100023314),
    'volume': (0x100021e28, 0x100024890),
    'send': (0x10001fba8, 0x100022620),
    'master-link': (0x1000236da, 0x10002601c),
    'master-volume': (0x100023924, 0x1000262c4),
    'io-link': (0x100022f8c, 0x100025944),
    'return': (0x100023e04, 0x1000267c0),
    'type': (0x1000271ca, 0x100029940),
    'pre-delay': (0x1000273fa, 0x100029bf8),
    'time': (0x1000274dc, 0x100029d20),
    'route': (0x1000262c8, 0x100004bc4),
}
VOLUMES = (0, 1, 0xf, 0x10, 0xff, 0x100, 0xfff, 0x1000, 0xffff,
           0x10000, 0xfffff, 0x100000, 0x123456, 0x200000, 0x2aaaaa, 0x555555, 0x654321, 0x7fffff)
PANS = (0, 1, 0xf, 0x10, 0xff, 0x100, 0xfff, 0x1000, 0x1234, 0x2aaa,
        0x3fff, 0x4000, 0x4321, 0x5555, 0x7fff, 0x8000)


def frame(address, value, digits=1):
    payload = bytes([value]) if digits == 1 else bytes(
        (value >> shift) & 15 for shift in range((digits - 1) * 4, -1, -4))
    body = bytes(address) + payload
    return bytes.fromhex('f0411000004d12') + body + bytes([-sum(body) % 128, 247])


def mixer_cases():
    for source, name in enumerate(('input', 'playback')):
        for bus, letter in enumerate('ABCD'):
            for channel in range(10):
                for parameter, selector, digits, values in (
                        ('link', 0, 1, (0, 1)), ('solo', 2, 1, (0, 1)), ('mute', 3, 1, (0, 1)),
                        ('pan', 4, 4, PANS), ('volume', 8, 6, VOLUMES), ('send', 14, 6, VOLUMES)):
                    if parameter == 'link' and channel % 2:
                        continue
                    if parameter == 'send' and (source != 0 or bus != 0):
                        continue
                    for value in values:
                        argument = channel // 2 if parameter == 'link' else channel
                        yield (parameter, (source, bus, argument, value),
                               ['mixer', name, letter, str(channel + 1), parameter, str(value)],
                               frame((0, 6 + source, bus * 16 + channel, selector), value, digits))


def master_cases():
    for source, name in enumerate(('input', 'playback')):
        for bus, letter in enumerate('ABCD'):
            for value in (0, 1):
                yield ('master-link', (2 + source, bus, value),
                       ['master', name, letter, '1', 'link', str(value)],
                       frame((0, 8 + source, bus * 16, 0), value))
            for channel in range(2):
                for value in VOLUMES:
                    yield ('master-volume', (2 + source, bus, channel, value),
                           ['master', name, letter, str(channel + 1), 'volume', str(value)],
                           frame((0, 8 + source, bus * 16 + channel, 1), value, 6))
            if source == 0:
                for value in (0, 1):
                    yield ('io-link', (bus, value), ['master', name, letter, '1', 'io-link', str(value)],
                           frame((0, 8, bus * 16, 13), value))
    for value in VOLUMES:
        yield ('return', (2, 0, value), ['master', 'input', 'A', '1', 'return', str(value)],
               frame((0, 8, 0, 7), value, 6))


def effect_route_cases():
    for value in range(5):
        yield ('type', value, ['reverb', 'type', '0', str(value)], frame((0, 4, 0, 0), value))
    for parameter, selector, count in (('pre-delay', 1, 13), ('time', 2, 50)):
        for bank in range(1, 5):
            for value in range(count):
                yield (parameter, (bank, value), ['reverb', parameter, str(bank), str(value)],
                       frame((0, 4, bank, selector), value))
    for pair in range(5):
        for value in range(9):
            yield ('route', (pair, value), ['route', str(pair + 1), str(value)], frame((0, 3, 0, pair), value))


def main():
    root = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('executable', type=Path, help='extracted macOS 1.5.9 control-panel architecture slice')
    parser.add_argument('--architecture', choices=('x86_64', 'arm64'), default='x86_64')
    parser.add_argument('--binary', type=Path, default=root / 'build/strict/octa-dsp')
    args = parser.parse_args()
    cases = [*mixer_cases(), *master_cases(), *effect_route_cases()]
    architecture = int(args.architecture == 'arm64')
    original = [(TRANSMITTERS[name][architecture], arguments, expected)
                for name, arguments, _command, expected in cases]
    (verify_arm64 if architecture else verify)(args.executable, original)
    for name, arguments, command, expected in cases:
        output = subprocess.run([str(args.binary), '--dry-run', *command], check=True,
                                capture_output=True, text=True, timeout=5)
        if bytes.fromhex(json.loads(output.stdout)['frame_hex']) != expected:
            raise RuntimeError(f'{name} {arguments}: C++ DSP frame differs from original')
    coverage = Counter(case[0] for case in cases)
    if set(coverage) != set(TRANSMITTERS):
        raise RuntimeError('An original DSP transmitter has no comparison cases')
    print(f'PASS {len(cases)} {args.architecture} DSP frames and C++ dry runs; no hardware access.')
    print(json.dumps(dict(sorted(coverage.items())), sort_keys=True))


if __name__ == '__main__':
    main()
