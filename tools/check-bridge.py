#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Check persistent GUI bridge state/meter sharing, typed controls and recovery."""

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from gui.backend import Bridge, DeviceError, InvalidControl


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def run(command):
    return subprocess.run(list(map(str, command)), capture_output=True, check=True, timeout=20).stdout


def frame(state):
    body = bytes([1, 0, 0, 0]) + bytes.fromhex(state['payload_hex'])
    return bytes.fromhex('f0 41 10 00 00 4d 12') + body + bytes([-sum(body) % 128, 0xf7])


def check_controls(bridge, before):
    require(not any(before['stereo_links']), 'Scalar fixture requires compressor stereo links off')
    preamp = before['preamps'][0]
    parameters = [
        ('low-cut', int(preamp['low_cut']), 1), ('phase', int(preamp['phase_inverted']), 1),
        ('sensitivity', preamp['sensitivity_index'], 100),
        ('compressor', int(preamp['compressor']['enabled']), 1),
        *[(name, preamp['compressor'][name + '_index'], maximum) for name, maximum in
          [('gate', 50), ('attack', 25), ('release', 45), ('threshold', 50), ('ratio', 8), ('gain', 74)]],
    ]
    for parameter, original, maximum in parameters:
        changed = bridge.change({'kind': 'preamp', 'channel': 0, 'parameter': parameter,
                                 'value': (original + 1) % (maximum + 1)})
        require(bridge.request('state')['state'] == changed, f'{parameter}: independent reply mismatch')
        restored = bridge.change({'kind': 'preamp', 'channel': 0, 'parameter': parameter, 'value': original})
        require(restored == before, f'{parameter}: complete original state not restored')
    mixer = before['mixers'][0]
    examples = [
        {'kind': 'mixer', 'source': 'input', 'bus': 0, 'channel': 0, 'parameter': 'pan',
         'value': mixer['input']['channels'][0]['pan']},
        {'kind': 'master', 'source': 'input', 'bus': 0, 'channel': 0, 'parameter': 'volume',
         'value': mixer['input_master']['volume'][0]},
        {'kind': 'reverb', 'parameter': 'type', 'bank': 0, 'value': before['reverb']['type']},
        {'kind': 'route', 'pair': 0, 'selection': before['output_routes'][0]},
    ]
    for example in examples:
        require(bridge.change(example) == before, f'{example["kind"]}: same-value command changed state')
    print('PASS ten safe preamp changes/restorations and four typed DSP command families', flush=True)


def main():
    root = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bin-dir', type=Path, default=root / 'build/strict')
    parser.add_argument('--port', default='hw:CARD=OCTACAPTURE,DEV=0,SUBDEV=1')
    parser.add_argument('--write', action='store_true')
    args = parser.parse_args()
    bridge = Bridge(args.bin_dir / 'octa-bridge', args.port)
    before = json.loads(run([args.bin_dir / 'octa-state', args.port]))
    baseline = None
    if args.write:
        directory = root / 'build/hardware'
        directory.mkdir(parents=True, exist_ok=True)
        recovery = Path(tempfile.mkdtemp(prefix='bridge-', dir=directory))
        baseline = recovery / 'original.syx'
        with baseline.open('xb') as output:
            baseline.chmod(0o600)
            output.write(frame(before))
            output.flush()
            os.fsync(output.fileno())
    try:
        require(bridge.request('state')['state'] == before, 'Bridge differs from independent bulk reader')
        deadline = time.monotonic() + 3
        while bridge.meters() is None and time.monotonic() < deadline:
            time.sleep(0.02)
        require(bridge.meters() is not None, 'No actual meter frame received')
        for _ in range(10):
            require(bridge.request('state')['state'] == before, 'Meter/query sharing changed state')
        try:
            bridge.change({'kind': 'preamp', 'channel': 7, 'parameter': 'hi-z', 'value': 1})
        except InvalidControl:
            pass
        else:
            raise RuntimeError('Invalid selector was accepted')
        require(bridge.request('state')['state'] == before, 'Rejected control changed state')
        if args.write:
            check_controls(bridge, before)
        require(bridge.meters()['age_ms'] < 1000, 'Meter stream stalled during control queries')
        print('PASS persistent meter/query traffic, serial replies and invalid-selector rejection', flush=True)
    finally:
        bridge.close()
        if baseline is not None:
            run([args.bin_dir / 'octa-preset', 'restore', baseline, args.port])
        require(json.loads(run([args.bin_dir / 'octa-state', args.port])) == before,
                f'Original raw state mismatch; recovery: {baseline}')
        print('PASS exact original state and released exclusive CTRL port', flush=True)


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, DeviceError, InvalidControl, OSError, ValueError, subprocess.SubprocessError) as error:
        print(f'FAIL: {error}')
        raise SystemExit(1) from error
