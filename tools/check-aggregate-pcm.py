#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Exercise real ALSA aggregate routing with synthetic PCM sources, never hardware."""

import argparse
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from gui.aggregate import configuration
from gui.devices import Device

FRAMES, BUFFER = 4096, 1024


def run_case(directory, binary, plugin, rate, direction):
    directory.mkdir()
    base = Device('serial:FIXTURE_BASE', 'FIXTURE_BASE', 0, 'FIXTURE_BASE')
    expansion = Device('serial:FIXTURE_EXP', 'FIXTURE_EXP', 1, 'FIXTURE_EXP', '0121')
    config = configuration(base, expansion, rate).decode()
    config += f'pcm_type.octa_fixture {{ lib "{plugin}" }}\n'
    # Only the four hardware leaves are replaced; multi/route/asym are the real ALSA implementations.
    for stream in ('playback', 'capture'):
        for unit, index in (('base', 0), ('exp', 1)):
            config += (f'pcm.!octa_expand_{unit}_{stream} {{ type octa_fixture unit {index}'
                       f' directory "{directory}" }}\n')
    path = directory / 'alsa.conf'
    path.write_text(config)
    command = ['bwrap', '--die-with-parent', '--unshare-all', '--ro-bind', '/', '/',
               '--dev', '/dev', '--proc', '/proc', '--tmpfs', '/sys',
               '--bind', str(directory), str(directory), '--setenv', 'ALSA_CONFIG_PATH', str(path),
               str(binary), str(rate), direction, str(directory / 'capture.raw')]
    result = subprocess.run(command, capture_output=True, text=True, timeout=10)
    if result.returncode or result.stderr:
        raise RuntimeError(f'Offline ALSA fixture failed: {result.returncode}\n{result.stdout}\n{result.stderr}')
    if result.stdout.strip() != f'PASS aggregate {direction} {rate} Hz, {FRAMES} frames':
        raise RuntimeError('Missing complete aggregate transfer evidence.')
    if direction == 'playback':
        for unit, first in (('base', 0), ('exp', 10)):
            expected = b''.join((frame * 20 + first + channel - 40960).to_bytes(3, 'little', signed=True)
                                for frame in range(FRAMES) for channel in range(10))
            if (directory / f'{unit}.raw').read_bytes() != expected:
                raise RuntimeError(f'{rate} Hz {unit}: playback sample/channel order differs.')
    else:
        expected = b''.join(((0x10000 + (channel // 12) * 0x10000 + (channel % 12) * 512 + frame % BUFFER) *
                            (-1 if channel % 2 else 1)).to_bytes(3, 'little', signed=True)
                           for frame in range(FRAMES) for channel in range(24))
        if (directory / 'capture.raw').read_bytes() != expected:
            raise RuntimeError(f'{rate} Hz: capture sample/channel order differs.')
    print(f'PASS {rate} Hz {direction}: EXP poll master, {FRAMES} frames and exact S24_3LE channel ordering.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary', type=Path, default=ROOT / 'build/strict/aggregate_pcm_fixture')
    parser.add_argument('--plugin', type=Path, default=ROOT / 'build/strict/octa-aggregate-fixture.so')
    args = parser.parse_args()
    binary, plugin = args.binary.resolve(strict=True), args.plugin.resolve(strict=True)
    with tempfile.TemporaryDirectory(prefix='octa-aggregate-pcm-') as name:
        for rate in (44100, 48000, 96000):
            for direction in ('playback', 'capture'):
                run_case(Path(name) / f'{rate}-{direction}', binary, plugin, rate, direction)
    print('All PCM devices were synthetic and isolated from host /dev, /sys and network. '
          'Physical clock synchronization, USB streaming, latency and stability remain unverified.')


if __name__ == '__main__':
    main()
