#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Execute the separate original macOS MIDI plugin's port selection offline."""

import argparse
import json
from pathlib import Path
import runpy

REFERENCE = runpy.run_path(str(Path(__file__).with_name('verify-interface-selection.py')))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mac_midi', type=Path, help='hash-pinned macOS 1.5.9 MIDI plugin x86_64 slice')
    args = parser.parse_args()
    reference = REFERENCE['MacReference'](args.mac_midi, 'x86_64', midi=True)
    cases, openings = REFERENCE['mac_selection'](reference, (4, 4))
    if (cases, openings) != (25, 5):
        raise RuntimeError('Incomplete original MIDI selector/open coverage.')
    print(json.dumps({'selector_cases': cases, 'open_sequences': openings,
                      'source_sha256': REFERENCE['MIDI_SHA256'],
                      'host_dependencies': 'memory-only interface OpenIf', 'hardware_validation': False}))
    print('PASS original MIDI port selectors and open sequences: interface 2 with both audio descriptor '
          'flags present, otherwise interface 1. Interface 4 is not selected by this path. No USB transfers.')


if __name__ == '__main__':
    main()
