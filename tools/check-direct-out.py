#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Check Direct Out against the original command's state changes and restore raw state."""

import argparse
import json
from pathlib import Path
import signal
import subprocess
import tempfile


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def run(command, expected=0):
    with subprocess.Popen(list(map(str, command)), stdout=subprocess.PIPE, stderr=subprocess.PIPE) as process:
        try:
            output, errors = process.communicate(timeout=90)
        except BaseException:
            process.terminate()
            try:
                process.communicate(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate(timeout=5)
            raise
        require(process.returncode == expected,
                f'{command}: {process.returncode}: {errors.decode(errors="replace")}')
        return errors if expected else output


def raw(snapshot):
    body = bytes([1, 0, 0, 0]) + bytes.fromhex(snapshot['payload_hex'])
    return bytes.fromhex('f0 41 10 00 00 4d 12') + body + bytes([-sum(body) % 128, 0xf7])


def target(before):
    """Independent raw expectation: preserve every bit outside DirectOut's controls."""
    payload = bytearray.fromhex(before['payload_hex'])

    def number(offset, digits, value):
        payload[offset:offset + digits] = bytes(int(digit, 16) for digit in f'{value:0{digits}x}')

    for bus in range(4):
        payload[5 + bus] = bus
        payload[0x58 + bus * 2] &= 0x70  # Four analog links off; preserve reserved bits.
        payload[0x59 + bus * 2] |= 1  # Digital pair linked.
        for flags in (0x68, 0x74):
            payload[flags + bus * 3] &= 0x70
            payload[flags + bus * 3 + 1] &= 0x70
            payload[flags + bus * 3 + 2] &= 0x7c
        for channel in range(10):
            pair = channel // 2
            pan = (0 if channel % 2 == 0 else 0x8000) if pair in (bus, 4) else 0x4000
            number(0x80 + (bus * 10 + channel) * 4, 4, pan)
            number(0x120 + (bus * 10 + channel) * 6, 6, 0x200000 if pair == bus else 0)
        payload[0x404 + bus] |= 1
        for channel in range(2):
            number(0x408 + (bus * 2 + channel) * 6, 6, 0x200000)
            if payload[0x43e + bus] & 1:
                number(0x44a + (bus * 2 + channel) * 6, 6, 0x200000)
    payload[0x210:0x24c] = bytes(60)
    return payload


def interrupt(_signum, _frame):
    raise KeyboardInterrupt


def main():
    root = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bin-dir', type=Path, default=root / 'build/strict')
    parser.add_argument('--port', default='hw:CARD=OCTACAPTURE,DEV=0,SUBDEV=1')
    parser.add_argument('--write', action='store_true', help='apply Direct Out and restore the complete original state')
    parser.add_argument('--linked-masters', action='store_true', help='test active I/O links with reduced master levels')
    args = parser.parse_args()
    if args.linked_masters and not args.write:
        parser.error('--linked-masters requires --write')

    def state():
        return json.loads(run([args.bin_dir / 'octa-state', args.port]))

    before = state()
    original = before
    if not args.write:
        print('Complete bulk state read. Use --write for Direct Out/readback/recovery checks.')
        return
    require(bytes.fromhex(before['payload_hex'])[2] < 3, 'Direct Out requires 44.1, 48 or 96 kHz')
    signal.signal(signal.SIGTERM, interrupt)
    hardware = root / 'build/hardware'
    hardware.mkdir(parents=True, exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix='direct-out-', dir=hardware))
    baseline = directory / 'original.syx'
    baseline.write_bytes(raw(before))
    baseline.chmod(0o600)
    protected = directory / 'existing.syx'
    protected.write_bytes(b'preserve existing recovery file')
    try:
        if args.linked_masters:
            prepared = bytearray.fromhex(before['payload_hex'])
            for bus in range(4):
                prepared[0x43e + bus] |= 1
                for source in range(2):
                    for side in range(2):
                        offset = 0x408 + source * 0x42 + (bus * 2 + side) * 6
                        prepared[offset:offset + 6] = bytes([1, 0, 0, 0, 0, 0])
            fixture = directory / 'linked-master-state.syx'
            fixture.write_bytes(raw({'payload_hex': prepared.hex()}))
            fixture.chmod(0o600)
            run([args.bin_dir / 'octa-preset', 'restore', fixture, args.port])
            before = state()
            require(bytes.fromhex(before['payload_hex']) == prepared, 'Linked-master setup differs')
        error = run([args.bin_dir / 'octa-preset', 'direct-out', protected, args.port], expected=1)
        require(b'create recovery snapshot' in error
                and protected.read_bytes() == b'preserve existing recovery file'
                and state() == before, 'Backup collision changed file or device state')
        print('PASS backup collision: existing file and complete device state unchanged', flush=True)
        recovery = directory / 'recovery.syx'
        output = run([args.bin_dir / 'octa-preset', 'direct-out', recovery, args.port])
        require(recovery.read_bytes() == raw(before), 'Raw recovery file differs from baseline')
        require(recovery.stat().st_mode & 0o077 == 0, 'Recovery file is not private')
        observed = bytes.fromhex(state()['payload_hex'])
        expected = target(before)
        mismatches = [f'{index:03x}: {old:02x}!={new:02x}'
                      for index, (old, new) in enumerate(zip(observed, expected, strict=True)) if old != new]
        require(observed == expected, f'Direct Out full-state mismatch: {mismatches[:20]}')
        print(output.decode().strip(), flush=True)
        print('PASS Direct Out: complete raw state matches; coaxial route and unrelated bits preserved', flush=True)
    finally:
        run([args.bin_dir / 'octa-preset', 'restore', baseline, args.port])
        require(state() == original, f'Complete raw restoration failed; recovery: {baseline}')
        print('PASS exact restoration: original raw state unchanged', flush=True)


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, OSError, ValueError, subprocess.SubprocessError, KeyboardInterrupt) as error:
        print(f'FAIL: {error}')
        raise SystemExit(1) from error
