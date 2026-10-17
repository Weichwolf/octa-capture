#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Verify original macOS Store/Load Memory frames in an isolated CPU emulator."""

import argparse
from pathlib import Path

from reference_midi import verify


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('executable', type=Path, help='extracted macOS 1.5.9 x86_64 control panel')
    args = parser.parse_args()
    frames = [bytes.fromhex('f0 41 10 00 00 4d 12 00 00 00 01') + bytes([action, checksum, 0xf7])
              for action, checksum in ((0, 0x7f), (1, 0x7e))]
    verify(args.executable, [(0x10002a946, action, frame) for action, frame in enumerate(frames)])
    for name, frame in zip(('store', 'load'), frames):
        print(f'{name}: {frame.hex(" ")} (original TxSaveLoad verified)')


if __name__ == '__main__':
    main()
