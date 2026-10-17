#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Verify routing preset frames against original macOS TxOutSel code."""

import argparse
from pathlib import Path

from reference_midi import verify


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('executable', type=Path, help='extracted macOS 1.5.9 x86_64 control panel')
    args = parser.parse_args()
    # Lists from CDlgPatchBay::OnSetSelAlt and CCplPnl::OnSetSelDirect.
    for name, routes in (('multi-monitor', (0, 1, 2, 3, 0)), ('playback', (0, 5, 6, 7, 8))):
        cases = []
        for pair, selection in enumerate(routes):
            body = bytes([0, 3, 0, pair, selection])
            frame = bytes.fromhex('f0 41 10 00 00 4d 12') + body + bytes([-sum(body) % 128, 0xf7])
            cases.append((0x1000262c8, (pair, selection), frame))
        verify(args.executable, cases)
        print(f'{name}: all five frames match original TxOutSel')


if __name__ == '__main__':
    main()
