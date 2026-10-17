#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Check Auto-Sens notification flow and complete state recovery on hardware."""

import argparse
import json
from pathlib import Path
import signal
import subprocess
import tempfile
import time


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
        if process.returncode:
            raise RuntimeError(f'{command}: {process.returncode}: {errors.decode(errors="replace")}')
        return output


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
    parser.add_argument('--write', action='store_true', help='run/cancel and run/finish input 1; restore complete state')
    args = parser.parse_args()

    def state():
        return json.loads(run([args.bin_dir / 'octa-state', args.port]))

    before = state()
    if not args.write:
        print('Bulk state read. Use --write for short Auto-Sens runs with complete raw restoration.')
        return
    signal.signal(signal.SIGTERM, interrupt)
    hardware = root / 'build/hardware'
    hardware.mkdir(parents=True, exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix='auto-sense-', dir=hardware))
    baseline = directory / 'original.syx'
    baseline.write_bytes(raw(before))
    baseline.chmod(0o600)
    for action in ('cancel', 'finish'):
        snapshot = directory / f'{action}-recovery.syx'
        try:
            output = json.loads(run([args.bin_dir / 'octa-auto-sense', '1', '500', action, snapshot, args.port]))
            require(snapshot.read_bytes() == baseline.read_bytes(), 'Saved raw snapshot differs from baseline')
            require(output['action'] == action and output['notifications'] >= 2 and
                    len(output['sensitivity_indices']) == 8, 'Missing Auto-Sens result/notifications')
            current = state()
            if action == 'cancel':
                require(current == before, 'Cancel did not restore the complete original raw state')
            else:
                original = bytes.fromhex(before['payload_hex'])
                other = bytearray.fromhex(current['payload_hex'])
                for channel in range(8):
                    if channel == 0 or (channel == 1 and original[0x25] & 1):
                        other[0x1d + channel] = original[0x1d + channel]
                require(other == original, 'Finish changed fields outside selected sensitivity/stereo peer')
            print(f'PASS {action}: activity/completion and full state bounds checked', flush=True)
        finally:
            run([args.bin_dir / 'octa-preset', 'restore-auto', baseline, args.port])
            require(state() == before, 'Complete raw restoration failed')
    for signum in (signal.SIGINT, signal.SIGTERM):
        snapshot = directory / f'signal-{signum}-recovery.syx'
        process = subprocess.Popen(list(map(str, [args.bin_dir / 'octa-auto-sense', '1', '5000',
                                                  'finish', snapshot, args.port])),
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            deadline = time.monotonic() + 8
            while not snapshot.exists() and process.poll() is None and time.monotonic() < deadline:
                time.sleep(0.005)
            require(snapshot.exists(), 'No recovery snapshot before interruption')
            time.sleep(0.1)
            process.send_signal(signum)
            _, errors = process.communicate(timeout=15)
            require(process.returncode == 130 and b'Original raw state restored and verified.' in errors,
                    f'Interrupted Auto-Sens did not verify recovery: {errors!r}')
            require(state() == before, 'Interruption changed the complete original raw state')
            print(f'PASS {signal.Signals(signum).name}: cancellation and full raw recovery', flush=True)
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.communicate(timeout=15)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.communicate(timeout=5)
            run([args.bin_dir / 'octa-preset', 'restore-auto', baseline, args.port])
            require(state() == before, 'Final complete raw restoration failed')
    print('PASS: Auto-Sens checks complete; original raw state unchanged.')


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, OSError, ValueError, subprocess.SubprocessError, KeyboardInterrupt) as error:
        print(f'FAIL: {error}')
        raise SystemExit(1) from error
