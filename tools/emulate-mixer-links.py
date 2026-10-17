#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Verify 104 stereo/master-link frames against original macOS code and the C++ CLI."""

import argparse
import json
from pathlib import Path
import subprocess

from reference_midi import verify


def frame(address, value):
    body = bytes(address) + bytes([value])
    return bytes.fromhex('f0 41 10 00 00 4d 12') + body + bytes([-sum(body) % 128, 0xf7])


def main():
    root = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('executable', type=Path, help='extracted macOS 1.5.9 x86_64 control panel')
    parser.add_argument('--binary', type=Path, default=root / 'build/strict/octa-dsp')
    args = parser.parse_args()
    cases, commands = [], []
    for bus, letter in enumerate('ABCD'):
        for source, name in enumerate(('input', 'playback')):
            for pair in range(5):
                for enabled in range(2):
                    expected = frame((0, 6 + source, bus * 16 + pair * 2, 0), enabled)
                    cases.append((0x10001cb8c, (source, bus, pair, enabled), expected))
                    commands.append(['mixer', name, letter, str(pair * 2 + 1), 'link', str(enabled)])
            for enabled in range(2):
                expected = frame((0, 8 + source, bus * 16, 0), enabled)
                cases.append((0x1000236da, (2 + source, bus, enabled), expected))
                commands.append(['master', name, letter, '1', 'link', str(enabled)])
        for enabled in range(2):
            expected = frame((0, 8, bus * 16, 13), enabled)
            cases.append((0x100022f8c, (bus, enabled), expected))
            commands.append(['master', 'input', letter, '1', 'io-link', str(enabled)])
    verify(args.executable, cases)
    for case, command in zip(cases, commands, strict=True):
        output = subprocess.run([str(args.binary), '--dry-run', *command],
                                check=True, capture_output=True, text=True, timeout=5)
        if bytes.fromhex(json.loads(output.stdout)['frame_hex']) != case[2]:
            raise RuntimeError(f'C++ control differs from original: {command}')
    print('All 104 link frames match original TxLink/TxMstLink/TxMstIOLink and C++ dry runs; no hardware access.')


if __name__ == '__main__':
    main()
