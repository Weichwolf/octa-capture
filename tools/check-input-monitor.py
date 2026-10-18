#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Verify composite monitor writes through the real backend with silent outputs.

Close the control panel and reserve the device before --write. Tests use no
audio files, never toggle phantom/Hi-Z, and restore a durable full raw snapshot.
"""

import argparse
import copy
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from gui.backend import Bridge

RATES = (44100, 48000, 96000, 192000)


def expected_state(before, channel, enabled, pan, volume):
    expected = copy.deepcopy(before)
    payload = bytearray.fromhex(before['payload_hex'])

    def update(index, update_pan):
        target = expected['mixers'][0]['input']['channels'][index]
        target.update(mute=not enabled, volume=volume)
        byte, bit = 0x74 + index // 4, 1 << (index % 4)
        payload[byte] = (payload[byte] & ~bit) | (bit if not enabled else 0)
        if update_pan:
            target['pan'] = pan
            offset = 0x80 + index * 4
            payload[offset:offset + 4] = bytes(int(digit, 16) for digit in f'{pan:04x}')
        offset = 0x120 + index * 6
        payload[offset:offset + 6] = bytes(int(digit, 16) for digit in f'{volume:06x}')

    if channel is None:
        for index in range(10):
            update(index, True)
    else:
        update(channel, True)
        if before['mixers'][0]['input']['pairs'][channel // 2]['linked']:
            update(channel ^ 1, False)
    expected['payload_hex'] = payload.hex()
    return expected


def snapshot(path, state):
    body = bytes([1, 0, 0, 0]) + bytes.fromhex(state['payload_hex'])
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'wb') as output:
        output.write(bytes.fromhex('f0411000004d12') + body + bytes([-sum(body) % 128, 247]))
        output.flush()
        os.fsync(output.fileno())


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


class Check:
    def __init__(self, binary, directory):
        self.binary = binary.resolve()
        self.directory = directory
        self.bridge = Bridge(self.binary / 'octa-bridge')
        self.cases = 0
        self.context = None

    def run(self, name, *arguments):
        result = subprocess.run([str(self.binary / name), *map(str, arguments)],
                                capture_output=True, timeout=30)
        if result.returncode:
            print(f'{name} failed during {self.context}: {result.stderr.decode(errors="replace")[:4096]}',
                  file=sys.stderr, flush=True)
            if name == 'octa-preset' and arguments[0] == 'restore':
                self.failed_restore(Path(arguments[1]))
            result.check_returncode()
        return result.stdout

    def failed_restore(self, recovery):
        result = subprocess.run([str(self.binary / 'octa-state')], capture_output=True, timeout=30)
        if result.returncode:
            print('Failed restore also prevented diagnostic state readback.', file=sys.stderr, flush=True)
            return
        actual = json.loads(result.stdout)
        expected = recovery.read_bytes()[11:-2]
        observed = bytes.fromhex(actual['payload_hex'])
        differences = [{'offset': hex(index), 'expected': old, 'actual': new}
                       for index, (old, new) in enumerate(zip(expected, observed, strict=True)) if old != new]
        report = {'context': self.context, 'recovery': str(recovery), 'actual': actual,
                  'expected_hex': expected.hex(), 'differences': differences}
        descriptor, filename = tempfile.mkstemp(prefix=f'failed-restore-{recovery.stem}-', suffix='.json',
                                              dir=self.directory)
        path = Path(filename)
        with os.fdopen(descriptor, 'w') as output:
            json.dump(report, output, indent=2)
            output.write('\n')
            output.flush()
            os.fsync(output.fileno())
        print(f'Restore differences: {differences[:12]}; diagnostic: {path}', file=sys.stderr, flush=True)

    def state(self):
        return json.loads(self.run('octa-state'))

    def verify_silence(self, state, rate):
        bus = 0 if rate == 192000 else 1
        require(state['output_routes'] == [bus] * 5, 'Physical outputs must use the silent mixer.')
        require(all(state['mixers'][bus][source + '_master']['volume'] == [0, 0]
                    for source in ('input', 'playback')), 'Both output mixer masters must remain zero.')

    def prepare(self, rate, linked, mono):
        bus = 0 if rate == 192000 else 1
        active = 4 if rate == 192000 else 10
        for source in ('input', 'playback'):
            for side in range(2):
                self.bridge.change({'kind': 'master', 'source': source, 'bus': bus,
                                    'channel': side, 'parameter': 'volume', 'value': 0})
        for pair in range(5):
            self.bridge.change({'kind': 'route', 'pair': pair, 'selection': bus})
        for channel in range(0, active, 2):
            for parameter, enabled in (('link', linked), ('mono', mono)):
                self.bridge.change({'kind': 'mixer', 'source': 'input', 'bus': 0,
                                    'channel': channel, 'parameter': parameter, 'value': int(enabled)})
        self.bridge.close()
        state = self.state()
        self.verify_silence(state, rate)
        return state

    def case(self, before, recovery, channel, enabled):
        self.context = {'channel': channel, 'enabled': enabled, 'fixture': str(recovery)}
        pan, volume = 0x2345, 0x123456
        expected = expected_state(before, channel, enabled, pan, volume)
        try:
            actual = self.bridge.change({'kind': 'monitor', 'channel': channel, 'enabled': enabled,
                                         'pan': pan, 'volume': volume})
            require(actual == expected, f'Monitor state/isolation mismatch: {channel=}, {enabled=}')
            self.bridge.close()
            require(self.state() == expected, 'Independent monitor readback differs.')
            self.cases += 1
        finally:
            self.bridge.close()
            if self.state() != before:
                self.run('octa-preset', 'restore', recovery)
            require(self.state() == before, f'Monitor fixture recovery failed: {recovery}')

    def rate(self, rate, links):
        self.run('octa-audio-check', rate, 'capture')
        modes = ((False, False), (False, True), (True, False), (True, True)) if links else ((False, False),)
        for linked, mono in modes:
            before = self.prepare(rate, linked, mono)
            recovery = self.directory / f'fixture-{rate}-{int(linked)}-{int(mono)}.syx'
            snapshot(recovery, before)
            for channel in (*range(4 if rate == 192000 else 10), None):
                for enabled in (False, True):
                    self.case(before, recovery, channel, enabled)
            print(f'PASS monitor at {rate} Hz, {linked=}, {mono=}: individual and all-ten broadcast.', flush=True)

    def restore(self, before, original_rate, recovery):
        self.bridge.close()
        try:
            current = json.loads(self.run('octa-status'))['sample_rate_hz']
            if current != original_rate:
                self.run('octa-audio-check', original_rate, 'capture')
        finally:
            try:
                current = self.state()
            except (OSError, ValueError, subprocess.SubprocessError):
                current = None
            if current != before:
                self.run('octa-preset', 'restore', recovery)
            require(self.state() == before, f'Original full-state recovery failed: {recovery}')
        require(json.loads(self.run('octa-status'))['sample_rate_hz'] == original_rate,
                'Original sample rate was not restored.')
        print('PASS original full state and clock independently restored.', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bin-dir', type=Path, default=ROOT / 'build/strict')
    parser.add_argument('--recovery-dir', type=Path, default=ROOT / 'build/hardware')
    parser.add_argument('--write', action='store_true', help='temporarily silence outputs and verify monitor changes')
    parser.add_argument('--rates', action='store_true', help='all four rates, then restore the original rate')
    parser.add_argument('--links', action='store_true', help='all four mixer link/mono combinations')
    args = parser.parse_args()
    if (args.rates or args.links) and not args.write:
        parser.error('--rates/--links require --write.')
    check = Check(args.bin_dir, None)
    before = check.state()
    if not args.write:
        print('Complete state read. Use --write for isolated composite monitor changes and raw recovery.')
        return
    rate = json.loads(check.run('octa-status'))['sample_rate_hz']
    args.recovery_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    check.directory = Path(tempfile.mkdtemp(prefix='input-monitor-', dir=args.recovery_dir))
    recovery = check.directory / 'original.syx'
    snapshot(recovery, before)
    print(f'Private original-state recovery: {recovery}', flush=True)

    def interrupted(_signum, _frame):
        raise KeyboardInterrupt
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, interrupted)
    try:
        for selected in RATES if args.rates else (rate,):
            check.rate(selected, args.links)
    finally:
        check.restore(before, rate, recovery)
    print(f'PASS {check.cases} composite monitor cases; no recorded audio or phantom/Hi-Z toggles.', flush=True)


if __name__ == '__main__':
    main()
