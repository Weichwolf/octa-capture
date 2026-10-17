#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Execute original display-selector/identification transmitters without USB I/O."""

import argparse
from pathlib import Path

from reference_midi import verify


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('executable', type=Path, help='extracted macOS 1.5.9 x86_64 control panel')
    arguments = parser.parse_args()
    cases = []
    for bus in range(4):
        frame = bytes.fromhex('f0 41 10 00 00 4d 12 00 02 00 03')
        frame += bytes([bus, 0x7b - bus, 0xf7])
        cases.append((0x100002410, bus, frame))
    cases.append((0x10000b49c, 0, bytes.fromhex('f0 41 10 00 00 4d 12 00 00 00 00 00 00 f7')))
    verify(arguments.executable, cases)
    print('PASS original TxDMix A–D and TxBlink: all five command frames; no hardware access.')


if __name__ == '__main__':
    main()
