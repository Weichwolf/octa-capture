#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Check reversible device writes, independent readback, and channel isolation."""

import argparse
import copy
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile


# CLI name, JSON path, maximum wire index. Phantom power and stereo coupling are excluded.
PARAMETERS = (
    ('low-cut', ('low_cut',), 1),
    ('phase', ('phase_inverted',), 1),
    ('sensitivity', ('sensitivity_index',), 100),
    ('gate', ('compressor', 'gate_index'), 50),
    ('attack', ('compressor', 'attack_index'), 25),
    ('release', ('compressor', 'release_index'), 45),
    ('threshold', ('compressor', 'threshold_index'), 50),
    ('ratio', ('compressor', 'ratio_index'), 8),
    ('gain', ('compressor', 'gain_index'), 74),
    ('compressor', ('compressor', 'enabled'), 1),
)
# Reference bulk columns, independent of the C++ writer (see doc/usb-midi-protocol.txt).
BULK_OFFSETS = {'low-cut': 0x17, 'phase': 0x19, 'hi-z': 0x1b, 'sensitivity': 0x1d,
                'compressor': 0x26, 'gate': 0x28, 'attack': 0x30, 'release': 0x38,
                'threshold': 0x40, 'ratio': 0x48, 'gain': 0x50}


def interrupt(_signum, _frame):
    raise KeyboardInterrupt


def run(command):
    result = subprocess.run(command, check=True, capture_output=True, text=True, timeout=8)
    return json.loads(result.stdout)


def state(binary_dir, port):
    return run([str(binary_dir / 'octa-state'), port])


def set_value(binary_dir, port, channel, parameter, value):
    run([str(binary_dir / 'octa-control'), str(channel + 1), parameter, str(value), port])


def expected_state(baseline, channel, parameter, path, value):
    expected = copy.deepcopy(baseline)
    target = expected['preamps'][channel]
    for key in path[:-1]:
        target = target[key]
    packed = type(target[path[-1]]) is bool
    target[path[-1]] = bool(value) if packed else value
    payload = bytearray.fromhex(baseline['payload_hex'])
    offset = BULK_OFFSETS[parameter]
    if packed:
        offset += channel // 4
        bit = 1 << (channel % 4)
        payload[offset] = (payload[offset] & ~bit) | (bit if value else 0)
    else:
        payload[offset + channel] = value
    expected['payload_hex'] = payload.hex()
    return expected


def snapshot(directory, baseline):
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    private = Path(tempfile.mkdtemp(prefix='preamp-sweep-', dir=directory))
    recovery = private / 'original.syx'
    body = bytes([1, 0, 0, 0]) + bytes.fromhex(baseline['payload_hex'])
    with os.fdopen(os.open(recovery, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'wb') as output:
        output.write(bytes.fromhex('f0411000004d12') + body + bytes([-sum(body) % 128, 247]))
        output.flush()
        os.fsync(output.fileno())
    return recovery


def restore(args, baseline, recovery):
    try:
        current = state(args.bin_dir, args.port)
    except (OSError, ValueError, subprocess.SubprocessError):
        current = None
    if current != baseline:
        subprocess.run([str(args.bin_dir / 'octa-preset'), 'restore', str(recovery), args.port],
                       check=True, capture_output=True, timeout=25)
    if state(args.bin_dir, args.port) != baseline:
        raise RuntimeError(f'Full-state restoration failed. Recovery: {recovery}')


def require_silent_outputs(snapshot, active):
    routes = snapshot['output_routes']
    buses = range(1 if active == 4 else 4)
    if len(routes) != 5 or any(route not in buses for route in routes):
        raise RuntimeError('Full-range checks require every output routed to an available silent mixer')
    for bus in routes:
        if any(snapshot['mixers'][bus][source + '_master']['volume'] != [0, 0]
               for source in ('input', 'playback')):
            raise RuntimeError('Full-range checks require both input/playback masters at zero on every output mixer')


def check_parameter(args, baseline, channel, parameter, path, maximum):
    target = baseline['preamps'][channel]
    for key in path[:-1]:
        target = target[key]
    original = target[path[-1]]
    old_value = int(original)
    if not 0 <= old_value <= maximum:
        raise RuntimeError(f'Channel {channel + 1}: {parameter} outside known write range')
    values = range(maximum + 1) if args.all_values else (old_value - 1 if old_value else 1,)
    try:
        for new_value in values:
            expected = expected_state(baseline, channel, parameter, path, new_value)
            set_value(args.bin_dir, args.port, channel, parameter, new_value)
            actual = state(args.bin_dir, args.port)
            if actual != expected:
                raise RuntimeError(f'Channel {channel + 1}: {parameter} {new_value} readback/isolation mismatch')
            print(f'PASS channel {channel + 1}: {parameter} {new_value}', flush=True)
    finally:
        # Also restore if the write reached the device but its reply was lost.
        set_value(args.bin_dir, args.port, channel, parameter, old_value)
        restored = state(args.bin_dir, args.port)
        if restored != baseline:
            raise RuntimeError(f'Channel {channel + 1}: {parameter} restoration mismatch')
    print(f'RESTORED channel {channel + 1}: {parameter} {old_value}', flush=True)


def main():
    root = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bin-dir', type=Path, default=root / 'build/strict')
    parser.add_argument('--port', default='hw:CARD=OCTACAPTURE,DEV=0,SUBDEV=1')
    parser.add_argument('--write', action='store_true', help='run reversible hardware changes')
    parser.add_argument('--hi-z', action='store_true', help='also change and restore input impedance on inputs 1/2; requires --write')
    parser.add_argument('--all-values', action='store_true', help='check every legal index; requires --write')
    parser.add_argument('--channel', type=int, choices=range(1, 9), action='append',
                        help='check only this physical input (repeatable); defaults to all active inputs')
    parser.add_argument('--recovery-dir', type=Path, default=root / 'build/hardware',
                        help='parent of a private raw recovery snapshot')
    args = parser.parse_args()
    if args.hi_z and not args.write:
        parser.error('--hi-z requires --write.')
    if args.all_values and not args.write:
        parser.error('--all-values requires --write.')
    baseline = state(args.bin_dir, args.port)
    rate_index = bytes.fromhex(baseline['payload_hex'])[2]
    if rate_index not in range(4):
        raise RuntimeError('Unknown sample rate; no parameter writes issued')
    active = 4 if rate_index == 3 else 8
    channels = list(dict.fromkeys(channel - 1 for channel in args.channel)) if args.channel else list(range(active))
    if any(channel >= active for channel in channels):
        raise RuntimeError('A selected input is unavailable at the current sample rate; no writes issued')
    hi_z_channels = [channel for channel in channels if channel < 2] if args.hi_z else []
    if args.hi_z and not hi_z_channels:
        parser.error('--hi-z requires input 1 or 2 in the selected channels.')
    checks = len(channels) * (sum(maximum + 1 for _, _, maximum in PARAMETERS)
                              if args.all_values else len(PARAMETERS))
    checks += len(hi_z_channels) * (2 if args.all_values else 1)
    if not args.write:
        print(f'Read succeeded. Use --write for {checks} reversible parameter checks; excludes phantom, Hi-Z, stereo link.')
        return
    if any(baseline['stereo_links'][channel // 2] for channel in channels):
        raise RuntimeError('Automatic channel isolation checks require unlinked preamps')
    if any(baseline['preamps'][channel]['phantom'] for channel in hi_z_channels):
        raise RuntimeError('Hi-Z checks require phantom power already off on inputs 1/2')
    if args.all_values:
        require_silent_outputs(baseline, active)
    recovery = snapshot(args.recovery_dir, baseline)
    print(f'Private full-state recovery: {recovery}', flush=True)
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, interrupt)
    try:
        for channel in channels:
            for parameter, path, maximum in PARAMETERS:
                check_parameter(args, baseline, channel, parameter, path, maximum)
        for channel in hi_z_channels:
            check_parameter(args, baseline, channel, 'hi-z', ('hi_z',), 1)
    finally:
        restore(args, baseline, recovery)
    print(f'PASS: {checks} writes/readbacks; each parameter and complete bulk state restored.')


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError, KeyboardInterrupt) as error:
        print(f'FAIL: {error}', file=sys.stderr)
        if isinstance(error, subprocess.CalledProcessError):
            print(error.stderr, file=sys.stderr)
        sys.exit(1)
