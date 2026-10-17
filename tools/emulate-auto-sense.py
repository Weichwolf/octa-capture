#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Verify every Auto-Sens selection and stop frame using original macOS code."""

import argparse
from pathlib import Path

from reference_midi import verify


def frame(address, payload):
    body = bytes(address) + bytes(payload)
    return bytes.fromhex('f0 41 10 00 00 4d 12') + body + bytes([-sum(body) % 128, 0xf7])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('executable', type=Path, help='extracted macOS 1.5.9 x86_64 control panel')
    args = parser.parse_args()
    cases = [(0x1000288f8, channels, frame([0, 2, 1, 4], [channels & 15, channels >> 4]))
             for channels in range(1, 256)]
    cases.extend((0x10002888a, action, frame([0, 2, 1, 5], [action])) for action in (0, 1))
    verify(args.executable, cases)
    print('Original Auto-Sens verified: all 255 nonempty selections, finish and cancel frames.')


if __name__ == '__main__':
    main()
