#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Compare all preamp-control endpoints with original macOS transmitters offline."""

import argparse
import json
from pathlib import Path
import subprocess

from reference_midi import verify
from reference_arm64 import verify as verify_arm64


def main():
    root = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('executable', type=Path, help='extracted macOS 1.5.9 control-panel architecture slice')
    parser.add_argument('--architecture', choices=('x86_64', 'arm64'), default='x86_64')
    parser.add_argument('--all-values', action='store_true', help='check every legal index instead of endpoints only')
    parser.add_argument('--binary', type=Path, default=root / 'build/strict/octa-control')
    args = parser.parse_args()
    parameters = [
        ('phantom', 0x1000132a8, 0x100015174, 1), ('low-cut', 0x100013d52, 0x100015da8, 1),
        ('phase', 0x10001375e, 0x1000156c8, 1), ('hi-z', 0x10001389c, 0x100015850, 1),
        ('sensitivity', 0x100014844, 0x1000175c0, 100), ('stereo-link', 0x100011a46, 0x100013c20, 1),
        ('compressor', 0x1000152be, 0x1000181d0, 1), ('gate', 0x1000160fc, 0x100019068, 50),
        ('attack', 0x1000167a6, 0x10001976c, 25), ('release', 0x1000169dc, 0x1000199c0, 45),
        ('threshold', 0x10001633a, 0x1000192c4, 50), ('ratio', 0x100016570, 0x100019518, 8),
        ('gain', 0x100016c1a, 0x100019c1c, 74),
    ]
    cases, commands = [], []
    for parameter, (name, x86_address, arm_address, maximum) in enumerate(parameters):
        address = arm_address if args.architecture == 'arm64' else x86_address
        for channel in range(8):
            if (name == 'hi-z' and channel >= 2) or (name == 'stereo-link' and channel % 2):
                continue
            for value in range(maximum + 1) if args.all_values else (0, maximum):
                body = bytes([0, 5, channel, parameter, value])
                frame = bytes.fromhex('f0 41 10 00 00 4d 12') + body + bytes([-sum(body) % 128, 0xf7])
                selector = channel // 2 if name == 'stereo-link' else channel
                cases.append((address, (selector, value), frame))
                commands.append([str(channel + 1), name, str(value)])
    (verify_arm64 if args.architecture == 'arm64' else verify)(args.executable, cases)
    for case, command in zip(cases, commands, strict=True):
        output = subprocess.run([str(args.binary), '--dry-run', *command],
                                check=True, capture_output=True, text=True, timeout=5)
        if bytes.fromhex(json.loads(output.stdout)['frame_hex']) != case[2]:
            raise RuntimeError(f'C++ preamp control differs from original: {command}')
    print(f'All {len(cases)} {args.architecture} preamp/compressor cases match original TxPre functions and C++ dry runs; no hardware access.')


if __name__ == '__main__':
    main()
