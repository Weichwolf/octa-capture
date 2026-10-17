#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Check routing presets and complete raw state restoration on OCTA-CAPTURE."""

import argparse
import json
from pathlib import Path
import signal
import subprocess
import tempfile


def run(command, expected=0):
    with subprocess.Popen(list(map(str, command)), stdout=subprocess.PIPE, stderr=subprocess.PIPE) as process:
        try:
            output, errors = process.communicate(timeout=20)
        except BaseException:
            process.terminate()
            try:
                process.communicate(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate(timeout=5)
            raise
        if process.returncode != expected:
            raise RuntimeError(f'{command}: {process.returncode}: {errors.decode(errors="replace")}')
        return errors if expected else output


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def raw(snapshot):
    body = bytes([1, 0, 0, 0]) + bytes.fromhex(snapshot['payload_hex'])
    return bytes.fromhex('f0 41 10 00 00 4d 12') + body + bytes([-sum(body) % 128, 0xf7])


def interrupt(_signum, _frame):
    raise KeyboardInterrupt


def main():
    root = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bin-dir', type=Path, default=root / 'build/strict')
    parser.add_argument('--port', default='hw:CARD=OCTACAPTURE,DEV=0,SUBDEV=1')
    parser.add_argument('--write', action='store_true', help='apply both routing presets and restore complete state')
    args = parser.parse_args()

    def state():
        return json.loads(run([args.bin_dir / 'octa-state', args.port]))

    before = state()
    if not args.write:
        print('Complete bulk state read. Use --write for routing/readback/recovery checks.')
        return
    signal.signal(signal.SIGTERM, interrupt)
    hardware = root / 'build/hardware'
    hardware.mkdir(parents=True, exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix='routing-', dir=hardware))
    baseline = directory / 'original.syx'
    baseline.write_bytes(raw(before))
    baseline.chmod(0o600)
    protected = directory / 'existing.syx'
    protected.write_bytes(b'preserve existing recovery file')
    error = run([args.bin_dir / 'octa-preset', 'routing', 'multi-monitor', protected, args.port], expected=1)
    require(b'create recovery snapshot' in error and protected.read_bytes() == b'preserve existing recovery file'
            and state() == before, 'Backup collision changed file or device state')
    print('PASS backup collision: existing file and complete device state unchanged', flush=True)
    for name, routes in (('multi-monitor', (0, 1, 2, 3, 0)), ('playback', (0, 5, 6, 7, 8))):
        recovery = directory / f'{name}-recovery.syx'
        try:
            run([args.bin_dir / 'octa-preset', 'routing', name, recovery, args.port])
            require(recovery.read_bytes() == baseline.read_bytes(), 'Raw recovery file differs from baseline')
            require(recovery.stat().st_mode & 0o077 == 0, 'Recovery file is not private')
            expected = bytearray.fromhex(before['payload_hex'])
            expected[5:10] = bytes(routes)
            require(bytes.fromhex(state()['payload_hex']) == expected, f'{name}: unexpected full-state change')
            print(f'PASS {name}: all five routes match original preset; other raw fields unchanged', flush=True)
        finally:
            run([args.bin_dir / 'octa-preset', 'restore', baseline, args.port])
            require(state() == before, 'Complete raw restoration failed')
    print('PASS: routing presets and exact restoration; original raw state unchanged.')


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, OSError, ValueError, subprocess.SubprocessError, KeyboardInterrupt) as error:
        print(f'FAIL: {error}')
        raise SystemExit(1) from error
