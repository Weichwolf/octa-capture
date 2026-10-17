#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Exercise file boundaries, reference presets and output errors through the CLI."""

import hashlib
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def run(binary, arguments, expected=0, **options):
    options.setdefault('stdout', subprocess.PIPE)
    result = subprocess.run([binary, *map(str, arguments)], stderr=subprocess.PIPE,
                            timeout=8, check=False, **options)
    require(result.returncode == expected, f'{arguments}: status {result.returncode}, {result.stderr!r}')
    return result


def main():
    binary = sys.argv[1]
    fixtures = Path(sys.argv[2])
    hashes = ('c7880db9105f0778d589e1cd2df740b2a31f7f08e345cb2e11a3a4e1e1b3a282',
              'd5e4b5a2fdfdee62a34e7b12f3b1fd858e1f063dc5896009b400e9639118a1a3')
    for pattern, expected_hash in enumerate(hashes):
        path = fixtures / f'preset-ver100-windows-{pattern}.octa'
        data = path.read_bytes()
        require(hashlib.sha256(data).hexdigest() == expected_hash, 'Windows reference fixture changed')
        require(len(data) == 1639, 'Reference fixture has the wrong size')
        require(run(binary, ['normalize', path]).stdout == data,
                'Windows reference did not round trip byte-for-byte')
        summary = json.loads(run(binary, ['inspect', path]).stdout)
        require(summary['format'] == 'Ver100' and summary['bytes'] == len(data) and
                summary['output_routes'] == list(struct.unpack_from('<5I', data, 39)) and
                summary['reverb_type'] == struct.unpack_from('<I', data, 59)[0],
                'Summary disagrees with independently read file offsets')
    prepare = ['prepare', fixtures / 'preset-import-profile.octa', fixtures / 'preset-export-bulk.syx']
    require(run(binary, prepare).stdout == (fixtures / 'preset-import-reference.syx').read_bytes(),
            'Offline import preparation disagrees with original LoadCpl/Set/TxBlk')
    run(binary, ['prepare', fixtures / 'preset-ver100-windows-1.octa',
                 fixtures / 'preset-export-bulk.syx'], expected=1)
    with open('/dev/full', 'wb') as full:
        run(binary, prepare, expected=1, stdout=full)
    original = (fixtures / 'preset-ver100-windows-0.octa').read_bytes()
    with tempfile.TemporaryDirectory(prefix='octa-preset-test-') as directory:
        path = Path(directory) / 'test.octa'
        invalid = [original[:length] for length in (0, 31, 38, 103, 1638)]
        invalid.extend((b'?' + original[1:], original[:32] + b'Ver200\0' + original[39:],
                        original + bytes(2 * 1024 * 1024)))
        for data in invalid:
            path.write_bytes(data)
            for mode in ('normalize', 'inspect'):
                result = run(binary, [mode, path], expected=1)
                require(not result.stdout and result.stderr, 'Rejected file emitted output or lacked an error')
        noncanonical = bytearray(original)
        noncanonical[103] = 255
        path.write_bytes(noncanonical)
        noncanonical[103] = 1
        require(run(binary, ['normalize', path]).stdout == noncanonical,
                'Nonzero flag did not normalize without changing other data')
        run(binary, ['inspect', Path(directory) / 'missing.octa'], expected=1)
    for arguments in ([], ['inspect'], ['unknown', fixtures], ['inspect', fixtures, 'extra'], ['export', 'invalid', 'extra']):
        run(binary, arguments, expected=2)
    run(binary, ['export', 'invalid'], expected=1)
    with tempfile.TemporaryDirectory(prefix='octa-preset-apply-test-') as directory:
        snapshot = Path(directory) / 'recovery.syx'
        rejected = run(binary, ['apply', fixtures / 'preset-ver100-windows-1.octa', snapshot, 'invalid'], expected=1)
        require(b'outside known control bounds' in rejected.stderr and not snapshot.exists(),
                'Invalid preset was not rejected before device/backup access')
        rejected = run(binary, ['restore', fixtures / 'preset-export-bulk.syx', 'invalid'], expected=1)
        require(b'invalid or outside known control bounds' in rejected.stderr,
                'Invalid recovery was not rejected before device access')
    with tempfile.TemporaryDirectory(prefix='octa-memory-cli-test-') as directory:
        snapshot = Path(directory) / 'recovery.syx'
        for mode in ('memory-store', 'memory-load'):
            run(binary, [mode], expected=2)
            run(binary, [mode, snapshot, 'invalid', 'extra'], expected=2)
            result = run(binary, [mode, snapshot, 'invalid'], expected=1)
            require(b'hardware MIDI port' in result.stderr and not snapshot.exists(),
                    'Invalid memory port accessed the backup path')
    with tempfile.TemporaryDirectory(prefix='octa-routing-cli-test-') as directory:
        snapshot = Path(directory) / 'recovery.syx'
        run(binary, ['routing'], expected=2)
        run(binary, ['routing', 'unknown', snapshot], expected=2)
        for name in ('multi-monitor', 'playback'):
            result = run(binary, ['routing', name, snapshot, 'invalid'], expected=1)
            require(b'hardware MIDI port' in result.stderr and not snapshot.exists(),
                    'Invalid routing port accessed the recovery path')
    with tempfile.TemporaryDirectory(prefix='octa-direct-out-cli-test-') as directory:
        snapshot = Path(directory) / 'recovery.syx'
        run(binary, ['direct-out'], expected=2)
        result = run(binary, ['direct-out', snapshot, 'invalid'], expected=1)
        require(b'hardware MIDI port' in result.stderr and not snapshot.exists(),
                'Invalid Direct Out port accessed the backup path')
    for mode in ('normalize', 'inspect'):
        with open('/dev/full', 'wb') as full:
            result = run(binary, [mode, fixtures / 'preset-ver100-windows-0.octa'], expected=1, stdout=full)
            require(b'Could not write preset output' in result.stderr, 'Output error was not reported')
    print('Preset CLI: reference round trips, invalid files, flag normalization and output errors passed.')


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, OSError, ValueError, subprocess.SubprocessError) as error:
        print(f'FAIL: {error}', file=sys.stderr)
        sys.exit(1)
