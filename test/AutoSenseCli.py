#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Reject invalid Auto-Sens arguments before opening MIDI or creating a backup."""

from pathlib import Path
import subprocess
import sys
import tempfile


def main():
    binary = sys.argv[1]
    with tempfile.TemporaryDirectory(prefix='octa-auto-sense-cli-') as directory:
        snapshot = Path(directory) / 'recovery.syx'
        for mask, duration, action in (('0', '1', 'cancel'), ('256', '1', 'cancel'),
                                      ('-1', '1', 'cancel'), ('1x', '1', 'cancel'),
                                      ('1', '0', 'cancel'), ('1', '60001', 'cancel'),
                                      ('1', '999999999999999999', 'cancel'), ('1', '1', 'unknown')):
            result = subprocess.run([binary, mask, duration, action, str(snapshot), 'invalid'],
                                    capture_output=True, timeout=8, check=False)
            if result.returncode != 2 or snapshot.exists() or result.stdout:
                raise RuntimeError(f'Invalid arguments accessed MIDI/backup: {result}')
        result = subprocess.run([binary, '1', '1', 'cancel', str(snapshot), 'invalid'],
                                capture_output=True, timeout=8, check=False)
        if result.returncode != 1 or snapshot.exists() or b'hardware MIDI port' not in result.stderr:
            raise RuntimeError('Invalid MIDI port was not rejected before backup access')
    print('Auto-Sens CLI argument boundaries passed without device access.')


if __name__ == '__main__':
    main()
