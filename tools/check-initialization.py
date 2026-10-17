#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Check mixer/reverb/patchbay initialization and exact restoration on OCTA-CAPTURE."""

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
            output, errors = process.communicate(timeout=25)
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


def scope(before, after, name):
    old, new = (bytes.fromhex(state['payload_hex']) for state in (before, after))
    spans = {'reverb': (0x0a, 0x15), 'direct-mix': (0x58, 0x47a), 'patchbay': (4, 0x0a)}
    start, end = spans[name]
    require(old[:start] == new[:start] and old[end:] == new[end:],
            f'{name}: unrelated raw state changed')
    if name == 'patchbay':
        require(new[5:10] == bytes([0, 5, 6, 7, 8]), 'Patchbay defaults differ from manual')
        require(new[4] in (old[4], 0), 'Unexpected bulk property change after TxDMix(0)')


def mutation(snapshot, name):
    """Change one bounded value so a repeated initialization must actually reset it."""
    payload = bytes.fromhex(snapshot['payload_hex'])
    if name == 'reverb':
        offset, digits, command, maximum = 0x0c, 1, ['reverb', 'pre-delay', '1'], 12
    elif name == 'direct-mix':
        offset, digits, command, maximum = 0x88, 4, ['mixer', 'input', 'A', '3', 'pan'], 0x8000
    else:
        offset, digits, command, maximum = 6, 1, ['route', '2'], 8
    value = 0
    for byte in payload[offset:offset + digits]:
        value = value * 16 + byte
    return command + [str((value + 1) % (maximum + 1))]


def interrupt(_signum, _frame):
    raise KeyboardInterrupt


def main():
    root = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bin-dir', type=Path, default=root / 'build/strict')
    parser.add_argument('--port', default='hw:CARD=OCTACAPTURE,DEV=0,SUBDEV=1')
    parser.add_argument('--write', action='store_true', help='initialize three DSP groups, verify and restore raw state')
    args = parser.parse_args()

    def state():
        return json.loads(run([args.bin_dir / 'octa-state', args.port]))

    before = state()
    if not args.write:
        print('Complete bulk state read. Use --write for initialization/readback/recovery checks.')
        return
    require(bytes.fromhex(before['payload_hex'])[2] < 3, 'These checks require 44.1, 48 or 96 kHz')
    signal.signal(signal.SIGTERM, interrupt)
    hardware = root / 'build/hardware'
    hardware.mkdir(parents=True, exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix='initialization-', dir=hardware))
    baseline = directory / 'original.syx'
    baseline.write_bytes(raw(before))
    baseline.chmod(0o600)
    protected = directory / 'existing.syx'
    protected.write_bytes(b'preserve existing recovery file')
    try:
        for name in ('reverb', 'patchbay', 'direct-mix'):
            error = run([args.bin_dir / 'octa-preset', 'initialize', name, protected, args.port], expected=1)
            require(b'create recovery snapshot' in error
                    and protected.read_bytes() == b'preserve existing recovery file'
                    and state() == before, f'{name}: backup collision changed file or device')
            recovery = directory / f'{name}-recovery.syx'
            run([args.bin_dir / 'octa-preset', 'initialize', name, recovery, args.port])
            require(recovery.read_bytes() == baseline.read_bytes(), f'{name}: backup differs from baseline')
            require(recovery.stat().st_mode & 0o077 == 0, 'Recovery file is not private')
            defaults = state()
            scope(before, defaults, name)
            (directory / f'{name}-defaults.json').write_text(json.dumps(defaults))
            run([args.bin_dir / 'octa-dsp', *mutation(defaults, name), args.port])
            changed = state()
            require(changed != defaults, f'{name}: test mutation did not change state')
            repeated = directory / f'{name}-repeated.syx'
            run([args.bin_dir / 'octa-preset', 'initialize', name, repeated, args.port])
            require(repeated.read_bytes() == raw(changed), f'{name}: second backup differs')
            require(state() == defaults, f'{name}: repeated initialization did not restore identical defaults')
            run([args.bin_dir / 'octa-preset', 'restore', baseline, args.port])
            require(state() == before, f'{name}: complete raw restoration failed')
            print(f'PASS {name}: scope, backup collision, private backup, reset from changed state and exact restoration',
                  flush=True)
    finally:
        run([args.bin_dir / 'octa-preset', 'restore', baseline, args.port])
        require(state() == before, f'Complete raw restoration failed; recovery: {baseline}')
        print('PASS exact restoration: original raw state unchanged', flush=True)


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, OSError, ValueError, subprocess.SubprocessError, KeyboardInterrupt) as error:
        print(f'FAIL: {error}')
        raise SystemExit(1) from error
