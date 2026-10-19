#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Exercise USB runtime suspend/resume with regular-user ALSA/MIDI probes."""

import argparse
import copy
import json
import os
from pathlib import Path
import runpy
import select
import signal
import subprocess
import tempfile
import time

from runtime_pm import find_device, identity, read

METERS = runpy.run_path(str(Path(__file__).with_name('check-pcm-meters.py')))


def wait_suspended(device, timeout=8):
    deadline = time.monotonic() + timeout
    consecutive = 0
    while time.monotonic() < deadline:
        status = read(device, 'power/runtime_status')
        consecutive = consecutive + 1 if status == 'suspended' else 0
        if consecutive == 2:
            return int(read(device, 'power/runtime_active_time'))
        time.sleep(0.02)
    usage = read(device, 'power/runtime_usage')
    raise RuntimeError(f'USB did not suspend: status={status}, usage={usage}; close competing ALSA/MIDI/USB clients.')


def snapshot(path, state):
    payload = bytes([1, 0, 0, 0]) + bytes.fromhex(state['payload_hex'])
    message = bytes.fromhex('f0411000004d12') + payload + bytes([-sum(payload) % 128, 247])
    with path.open('xb') as output:
        output.write(message)
        output.flush()
        os.fsync(output.fileno())


class PowerLease:
    def __init__(self):
        self.process = None

    def start(self):
        self.process = subprocess.Popen(['sudo', '-n', '/usr/local/sbin/octa-capture-dev', 'runtime-pm'],
                                        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if not select.select([self.process.stdout], [], [], 8)[0]:
            raise RuntimeError('Runtime-PM helper did not publish readiness.')
        line = self.process.stdout.readline(2048)
        if not line:
            _, errors = self.process.communicate(timeout=5)
            self.process = None
            raise RuntimeError('Runtime-PM helper unavailable: ' + errors.decode(errors='replace').strip())
        record = json.loads(line)
        if record.get('ready') is not True or record.get('lease_seconds') != 120:
            raise RuntimeError(f'Unexpected runtime-PM helper readiness: {record}')
        return record['original']

    def stop(self):
        process, self.process = self.process, None
        if process is None:
            return
        # Closing the pipe also releases the helper if the client exits unexpectedly.
        output, errors = process.communicate(timeout=8)
        if process.returncode:
            raise RuntimeError(f'Runtime-PM helper exited {process.returncode}: {errors.decode(errors="replace")}')
        lines = [json.loads(line) for line in output.splitlines()]
        if lines != [{'restored': True, 'reason': 'closed'}]:
            raise RuntimeError(f'Runtime-PM helper restoration unverified: {lines}')


def rate_state(before, rate):
    expected = copy.deepcopy(before)
    payload = bytearray.fromhex(before['payload_hex'])
    payload[2] = (44100, 48000, 96000, 192000).index(rate)
    expected['payload_hex'] = payload.hex()
    return expected


def probes(check, device, rate, expected):
    for name in ('status', 'midi', 'pcm'):
        active_before = wait_suspended(device)
        if name == 'status':
            status = json.loads(check.run('octa-status'))
            if status['sample_rate_hz'] != rate or not status['clock_active'] or status['clock_source'] != 'internal':
                raise RuntimeError(f'Clock changed across runtime resume: {status}')
        elif name == 'midi':
            check.run('octa-state')
        else:
            try:
                check.start()
                check.streams(('capture', 'playback'), close_meter=True)
            finally:
                check.stop()
        active_after = int(read(device, 'power/runtime_active_time'))
        if active_after <= active_before:
            raise RuntimeError(f'{name}: no measured USB runtime-active interval after resume.')
        if json.loads(check.run('octa-state')) != expected:
            raise RuntimeError(f'{rate} Hz {name}: full raw state changed across runtime resume.')
        print(f'PASS {rate} Hz {name}: observed USB suspend and verified resume, '
              f'{active_after - active_before} ms active.', flush=True)


def restore(check, device, before, status, policy, recovery, original_device):
    if identity(device) != original_device:
        raise RuntimeError(f'Device identity changed; refusing restoration to a replacement; recovery: {recovery}')
    if json.loads(check.run('octa-state')) != before or json.loads(check.run('octa-status')) != status:
        check.run('octa-audio-check', status['sample_rate_hz'], 'capture')
        check.run('octa-preset', 'restore', recovery)
    if json.loads(check.run('octa-state')) != before or json.loads(check.run('octa-status')) != status:
        raise RuntimeError(f'Original state/clock not restored; recovery: {recovery}')
    if any(read(device, 'power/' + key) != value for key, value in policy.items()):
        raise RuntimeError('Original USB power policy was not restored.')
    print(f'PASS full raw/clock/power-policy restoration; recovery: {recovery}', flush=True)


def exercise(check, device, directory, rates):
    lease = PowerLease()
    original_device = identity(device)
    before = json.loads(check.run('octa-state'))
    status = json.loads(check.run('octa-status'))
    if status['clock_source'] != 'internal' or not status['clock_active']:
        raise RuntimeError('This check requires a stable internal clock.')
    recovery = directory / 'original.syx'
    snapshot(recovery, before)
    original_policy = {name: read(device, 'power/' + name) for name in ('control', 'autosuspend_delay_ms')}
    try:
        original = lease.start()
        if original != {'device': device.name, 'control': original_policy['control'],
                        'autosuspend_delay_ms': int(original_policy['autosuspend_delay_ms'])}:
            raise RuntimeError('Policy lease belongs to a different device or changed policy.')
        for rate in (44100, 48000, 96000, 192000) if rates else (status['sample_rate_hz'],):
            if identity(device) != original_device:
                raise RuntimeError('USB device identity changed before the rate probe.')
            audio = json.loads(check.run('octa-audio-check', rate, 'capture'))
            if audio['error'] != 0 or not audio['timing_ok']:
                raise RuntimeError(f'PCM rate setup failed: {audio}')
            probes(check, device, rate, rate_state(before, rate))
    finally:
        try:
            check.stop()
        finally:
            try:
                lease.stop()
            finally:
                restore(check, device, before, status, original_policy, recovery, original_device)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bin-dir', type=Path, default=Path('build/strict'))
    parser.add_argument('--rates', action='store_true', help='exercise all four rates instead of the current rate')
    parser.add_argument('--output', type=Path, default=Path('build/hardware'))
    args = parser.parse_args()
    if os.geteuid() == 0:
        parser.error('Run this checker as the regular development user; only the fixed policy helper uses sudo.')
    os.umask(0o077)
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    device = find_device()
    args.output.mkdir(parents=True, exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix='runtime-pm-', dir=args.output))
    check = METERS['Check'](args.bin_dir)
    with check.output:
        exercise(check, device, directory, args.rates)


if __name__ == '__main__':
    try:
        main()
    except (OSError, RuntimeError, ValueError, AssertionError, subprocess.SubprocessError) as error:
        print(f'FAIL: {error}')
        raise SystemExit(1) from error
