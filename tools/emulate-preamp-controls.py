#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Compare all preamp-control endpoints with original macOS transmitters offline."""

import argparse
import json
from pathlib import Path
import subprocess

from reference_midi import verify


def main():
    root = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('executable', type=Path, help='extracted macOS 1.5.9 x86_64 control panel')
    parser.add_argument('--binary', type=Path, default=root / 'build/strict/octa-control')
    args = parser.parse_args()
    parameters = [
        ('phantom', 0x1000132a8, 1), ('low-cut', 0x100013d52, 1),
        ('phase', 0x10001375e, 1), ('hi-z', 0x10001389c, 1),
        ('sensitivity', 0x100014844, 100), ('stereo-link', 0x100011a46, 1),
        ('compressor', 0x1000152be, 1), ('gate', 0x1000160fc, 50),
        ('attack', 0x1000167a6, 25), ('release', 0x1000169dc, 45),
        ('threshold', 0x10001633a, 50), ('ratio', 0x100016570, 8),
        ('gain', 0x100016c1a, 74),
    ]
    cases, commands = [], []
    for parameter, (name, address, maximum) in enumerate(parameters):
        for channel in range(8):
            if (name == 'hi-z' and channel >= 2) or (name == 'stereo-link' and channel % 2):
                continue
            for value in (0, maximum):
                body = bytes([0, 5, channel, parameter, value])
                frame = bytes.fromhex('f0 41 10 00 00 4d 12') + body + bytes([-sum(body) % 128, 0xf7])
                selector = channel // 2 if name == 'stereo-link' else channel
                cases.append((address, (selector, value), frame))
                commands.append([str(channel + 1), name, str(value)])
    verify(args.executable, cases)
    for case, command in zip(cases, commands, strict=True):
        output = subprocess.run([str(args.binary), '--dry-run', *command],
                                check=True, capture_output=True, text=True, timeout=5)
        if bytes.fromhex(json.loads(output.stdout)['frame_hex']) != case[2]:
            raise RuntimeError(f'C++ preamp control differs from original: {command}')
    print(f'All {len(cases)} control endpoints match original TxPre functions and C++ dry runs; no hardware access.')


if __name__ == '__main__':
    main()
