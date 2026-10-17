# SPDX-License-Identifier: GPL-2.0-only
"""Exercise preamp dry-run frames and reject invalid controls without hardware."""

import json
from pathlib import Path
import subprocess
import sys


def run(binary, arguments, expected=0, **options):
    result = subprocess.run([str(binary), *map(str, arguments)], stderr=subprocess.PIPE,
                            timeout=5, check=False, **options)
    if result.returncode != expected:
        raise RuntimeError(f'{arguments}: {result.returncode}: {result.stderr.decode(errors="replace")}')
    return result


def main():
    binary = Path(sys.argv[1])
    cases = [(('1', 'sensitivity', '20'), 'f0411000004d12000500041463f7'),
             (('7', 'stereo-link', '1'), 'f0411000004d1200050605016ff7'),
             (('2', 'hi-z', '1'), 'f0411000004d12000501030176f7')]
    for arguments, expected in cases:
        output = run(binary, ['--dry-run', *arguments, 'invalid'], stdout=subprocess.PIPE)
        if json.loads(output.stdout) != {'frame_hex': expected}:
            raise RuntimeError(f'Unexpected encoded reference frame: {arguments}')
    run(binary, [], expected=2, stdout=subprocess.PIPE)
    run(binary, ['--dry-run'], expected=2, stdout=subprocess.PIPE)
    for channel in (2, 4, 6, 8):
        for prefix in ([], ['--dry-run']):
            result = run(binary, [*prefix, channel, 'stereo-link', 1, 'invalid'], expected=2,
                         stdout=subprocess.PIPE)
            if b'Invalid preamp' not in result.stderr:
                raise RuntimeError('Stereo selector accessed the MIDI backend')
    for channel in range(3, 9):
        result = run(binary, [channel, 'hi-z', 0, 'invalid'], expected=2, stdout=subprocess.PIPE)
        if b'Invalid preamp' not in result.stderr:
            raise RuntimeError('Unsupported Hi-Z control accessed the MIDI backend')
    for arguments in ((0, 'phase', 1), (9, 'phase', 1), (1, 'unknown', 1),
                      (1, 'sensitivity', 101), (1, 'stereo-link', 2)):
        run(binary, ['--dry-run', *arguments], expected=2, stdout=subprocess.PIPE)
    result = run(binary, [1, 'phase', 0, 'invalid'], expected=1, stdout=subprocess.PIPE)
    if b'hardware MIDI port' not in result.stderr:
        raise RuntimeError('Valid live command did not reach port validation')
    with open('/dev/full', 'wb') as full:
        run(binary, ['--dry-run', 1, 'phase', 1], expected=1, stdout=full)


if __name__ == '__main__':
    main()
