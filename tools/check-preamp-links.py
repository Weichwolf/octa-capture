#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Check compressor stereo coupling and independent ADC controls with raw recovery."""

import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import tempfile


# Packed flag columns use four low bits per byte; numeric columns use one byte/channel.
PARAMETERS = {
    'low-cut': (0x17, True, False), 'phase': (0x19, True, False),
    'sensitivity': (0x1d, False, False), 'compressor': (0x26, True, True),
    'gate': (0x28, False, True), 'attack': (0x30, False, True),
    'release': (0x38, False, True), 'threshold': (0x40, False, True),
    'ratio': (0x48, False, True), 'gain': (0x50, False, True),
}


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def run(command):
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
        require(process.returncode == 0, f'{command}: {process.returncode}: {errors.decode(errors="replace")}')
        return output


def frame(payload):
    body = bytes([1, 0, 0, 0]) + payload
    return bytes.fromhex('f0 41 10 00 00 4d 12') + body + bytes([-sum(body) % 128, 0xf7])


def set_value(payload, name, channel, value):
    offset, packed, _ = PARAMETERS[name]
    if packed:
        offset += channel // 4
        bit = 1 << (channel % 4)
        payload[offset] = (payload[offset] & ~bit) | (bit if value else 0)
    else:
        payload[offset + channel] = value


def fixture(original, pair, linked=False, asymmetric=False):
    payload = bytearray(original)
    bit = 1 << pair
    payload[0x25] = (payload[0x25] & ~bit) | (bit if linked else 0)
    for side in range(2):
        for name in PARAMETERS:
            set_value(payload, name, pair * 2 + side, int(asymmetric and side == 1))
    return payload


def interrupt(_signum, _frame):
    raise KeyboardInterrupt


def main():
    root = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bin-dir', type=Path, default=root / 'build/strict')
    parser.add_argument('--port', default='hw:CARD=OCTACAPTURE,DEV=0,SUBDEV=1')
    parser.add_argument('--write', action='store_true', help='check four link cycles and 160 scalar coupling cases')
    args = parser.parse_args()

    def state():
        return json.loads(run([args.bin_dir / 'octa-state', args.port]))

    def readback(expected, label):
        observed = bytes.fromhex(state()['payload_hex'])
        differences = [(f'{i:03x}', actual, wanted) for i, (actual, wanted) in
                       enumerate(zip(observed, expected, strict=True)) if actual != wanted]
        require(observed == expected, f'{label}: full-state mismatch {differences[:24]}')

    def write(channel, parameter, value):
        run([args.bin_dir / 'octa-control', str(channel + 1), parameter, str(value), args.port])

    before = state()
    if not args.write:
        print('Complete state read. Use --write for four compressor-link cycles and 160 scalar cases with raw recovery.')
        return
    original = bytes.fromhex(before['payload_hex'])
    require(len(original) == 1146, 'Unexpected bulk payload size')
    require(all(original[offset + byte] & 0xf == 0 for offset in (0x15, 0x1b) for byte in range(2)),
            'This fixture requires phantom power and Hi-Z off on every input')
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, interrupt)
    hardware = root / 'build/hardware'
    hardware.mkdir(parents=True, exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix='preamp-links-', dir=hardware))
    baseline = directory / 'original.syx'
    with baseline.open('xb') as output:
        baseline.chmod(0o600)
        output.write(frame(original))
        output.flush()
        os.fsync(output.fileno())
    case_file = directory / 'test-state.syx'

    def load(payload):
        case_file.write_bytes(frame(payload))
        case_file.chmod(0o600)
        run([args.bin_dir / 'octa-preset', 'restore', case_file, args.port])
        readback(payload, 'test fixture')

    try:
        for pair in range(4):
            configured = fixture(original, pair, asymmetric=True)
            load(configured)
            expected = bytearray(configured)
            expected[0x25] |= 1 << pair
            for name, (_, _, compressor) in PARAMETERS.items():
                if compressor:
                    set_value(expected, name, pair * 2 + 1, 0)
            write(pair * 2, 'stereo-link', 1)
            readback(expected, f'pair {pair + 1}: enable copies only left compressor settings')
            expected[0x25] &= ~(1 << pair)
            write(pair * 2, 'stereo-link', 0)
            readback(expected, f'pair {pair + 1}: disable preserves settings')
            print(f'PASS pair {pair + 1}: link enable/disable, compressor copy and independent ADC controls', flush=True)
            for linked in (False, True):
                configured = fixture(original, pair, linked=linked)
                for side in range(2):
                    channel = pair * 2 + side
                    for name, (_, _, compressor) in PARAMETERS.items():
                        load(configured)
                        expected = bytearray(configured)
                        for target in range(pair * 2, pair * 2 + 2):
                            if target == channel or (linked and compressor):
                                set_value(expected, name, target, 1)
                        write(channel, name, 1)
                        readback(expected, f'{pair=}, {linked=}, {channel=}, {name=}')
                print(f'PASS pair {pair + 1}, linked={linked}: both directions, ten parameters and full raw isolation',
                      flush=True)
    finally:
        run([args.bin_dir / 'octa-preset', 'restore', baseline, args.port])
        require(state() == before, f'Original raw state restoration failed; recovery: {baseline}')
        print('PASS exact restoration: original raw state unchanged', flush=True)


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, OSError, ValueError, subprocess.SubprocessError, KeyboardInterrupt) as error:
        print(f'FAIL: {error}')
        raise SystemExit(1) from error
