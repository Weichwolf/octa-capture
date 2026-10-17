# SPDX-License-Identifier: GPL-2.0-only
"""Reject malformed panel commands without addressing a MIDI device."""

from pathlib import Path
import subprocess
import sys

binary = Path(sys.argv[1]).resolve()
cases = [[], ['state'], ['display'], ['display', '-1'], ['display', '4'],
         ['display', '1.5'], ['display', '256'], ['display', '0\n1 identify'],
         ['display', '0', 'unused', 'extra'], ['identify', 'unused', 'extra']]
for arguments in cases:
    result = subprocess.run([str(binary), *arguments], capture_output=True, timeout=3)
    assert result.returncode == 2, (arguments, result.returncode, result.stderr.decode())
for arguments in (['display', '0', 'default'], ['identify', 'default']):
    result = subprocess.run([str(binary), *arguments], capture_output=True, timeout=3)
    assert result.returncode == 1 and b'hardware MIDI port' in result.stderr, result.stderr.decode()
print('PASS malformed panel commands and non-hardware ports rejected before MIDI I/O')
