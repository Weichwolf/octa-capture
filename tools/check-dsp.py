#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Check DSP writes, independent bulk readback, isolation and restoration."""

import argparse
import copy
from dataclasses import dataclass
import json
from pathlib import Path
import signal
import subprocess
import sys


@dataclass(frozen=True)
class Check:
    command: tuple
    path: tuple
    maximum: int
    # Restore the peer too if the device unexpectedly couples a stereo pair.
    peers: tuple = ()
    coupling: tuple = ()


def run(command):
    result = subprocess.run(command, check=True, capture_output=True, text=True, timeout=8)
    return json.loads(result.stdout)


def state(args):
    return run([str(args.bin_dir / 'octa-state'), args.port])


def write(args, command, value):
    run([str(args.bin_dir / 'octa-dsp'), *map(str, command), str(int(value)), args.port])


def value_at(snapshot, path):
    for key in path:
        snapshot = snapshot[key]
    return snapshot


def fields(snapshot):
    result = snapshot.copy()
    del result['payload_hex']
    return result


def checks():
    result = []
    for bus, letter in enumerate('ABCD'):
        for group in ('input', 'playback'):
            for channel in range(10):
                for parameter, maximum in (('mute', 1), ('solo', 1), ('pan', 0x8000), ('volume', 0x7fffff)):
                    path = ('mixers', bus, group, 'channels', channel, parameter)
                    peer = ('mixers', bus, group, 'channels', channel ^ 1, parameter)
                    command = ('mixer', group, letter, channel + 1, parameter)
                    peer_command = ('mixer', group, letter, (channel ^ 1) + 1, parameter)
                    coupling = ('mixers', bus, group, 'pairs', channel // 2, 'linked') if parameter != 'pan' else ()
                    result.append(Check(command, path, maximum, ((peer_command, peer),), coupling))
            for channel in range(2):
                path = ('mixers', bus, group + '_master', 'volume', channel)
                peer = ('mixers', bus, group + '_master', 'volume', channel ^ 1)
                command = ('master', group, letter, channel + 1, 'volume')
                peer_command = ('master', group, letter, (channel ^ 1) + 1, 'volume')
                result.append(Check(command, path, 0x7fffff, ((peer_command, peer),),
                                    ('mixers', bus, group + '_master', 'linked')))
    for channel in range(10):
        peer = ('reverb', 'input_send', channel ^ 1)
        peer_command = ('mixer', 'input', 'A', (channel ^ 1) + 1, 'send')
        result.append(Check(('mixer', 'input', 'A', channel + 1, 'send'),
                            ('reverb', 'input_send', channel), 0x7fffff, ((peer_command, peer),),
                            ('mixers', 0, 'input', 'pairs', channel // 2, 'linked')))
    for bank in range(1, 5):
        result.append(Check(('reverb', 'pre-delay', bank), ('reverb', 'pre_delay_indices', bank), 12))
        result.append(Check(('reverb', 'time', bank), ('reverb', 'time_indices', bank), 49))
    result.append(Check(('master', 'input', 'A', 1, 'return'), ('reverb', 'master_return'), 0x7fffff))
    banks = tuple((('reverb', parameter, bank), ('reverb', field, bank))
                  for parameter, field in (('pre-delay', 'pre_delay_indices'), ('time', 'time_indices'))
                  for bank in range(1, 5))
    result.append(Check(('reverb', 'type', 0), ('reverb', 'type'), 4, banks))
    for pair in range(5):
        result.append(Check(('route', pair + 1), ('output_routes', pair), 8))
    return result


def check(args, baseline, item):
    original = value_at(baseline, item.path)
    new_value = int(original) - 1 if original else 1
    expected = copy.deepcopy(baseline)
    target = expected
    for key in item.path[:-1]:
        target = target[key]
    target[item.path[-1]] = bool(new_value) if isinstance(original, bool) else new_value
    if item.coupling and value_at(baseline, item.coupling):
        for _, path in item.peers:
            target = expected
            for key in path[:-1]:
                target = target[key]
            target[path[-1]] = bool(new_value) if isinstance(original, bool) else new_value
    try:
        write(args, item.command, new_value)
        observed = state(args)
        if fields(observed) != fields(expected):
            raise RuntimeError(f'{item.command}: readback/isolation mismatch')
    finally:
        # Restore even if transmission succeeded but readback failed.
        write(args, item.command, original)
        for command, path in item.peers:
            write(args, command, value_at(baseline, path))
        restored = state(args)
        if restored != baseline:
            raise RuntimeError(f'{item.command}: complete bulk restoration mismatch')
    print(f'PASS {item.command}: {int(original)}->{new_value}->{int(original)}', flush=True)


def interrupt(_signum, _frame):
    raise KeyboardInterrupt


def main():
    root = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bin-dir', type=Path, default=root / 'build/strict')
    parser.add_argument('--port', default='hw:CARD=OCTACAPTURE,DEV=0,SUBDEV=1')
    parser.add_argument('--write', action='store_true', help='change/read/restore scalar DSP parameters')
    args = parser.parse_args()
    baseline = state(args)
    plan = checks()
    if not args.write:
        print(f'Read succeeded. Use --write for {len(plan)} reversible checks; excludes link/mono and persistence/reset.')
        return
    # Validate the whole restoration plan before the first hardware write.
    for item in plan:
        if not 0 <= value_at(baseline, item.path) <= item.maximum:
            raise RuntimeError(f'{item.command}: original value outside known write bounds')
        for command, path in ((item.command, item.path), *item.peers):
            run([str(args.bin_dir / 'octa-dsp'), '--dry-run', *map(str, command),
                 str(int(value_at(baseline, path)))])
    snapshot = root / 'build/hardware/dsp-before.json'
    snapshot.parent.mkdir(parents=True, exist_ok=True)
    snapshot.write_text(json.dumps(baseline, indent=2) + '\n')
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, interrupt)
    for item in plan:
        check(args, baseline, item)
    if state(args) != baseline:
        raise RuntimeError('Final bulk state differs from the original snapshot')
    print(f'PASS: {len(plan)} DSP changes/readbacks/restorations; complete bulk state restored.')


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, subprocess.SubprocessError, KeyboardInterrupt) as error:
        print(f'FAIL: {error}', file=sys.stderr)
        if isinstance(error, subprocess.CalledProcessError):
            print(error.stderr, file=sys.stderr)
        sys.exit(1)
