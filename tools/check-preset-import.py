#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Check preset import/readback and exact raw restoration on OCTA-CAPTURE."""

import argparse
import json
from pathlib import Path
import signal
import struct
import subprocess
import tempfile


def run(command, expected=0):
    with subprocess.Popen(list(map(str, command)), stdout=subprocess.PIPE, stderr=subprocess.PIPE) as process:
        try:
            output, errors = process.communicate(timeout=20)
        except BaseException:
            process.terminate()
            try:
                process.communicate(timeout=20)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate(timeout=5)
            raise
        if process.returncode != expected:
            raise RuntimeError(f'{command}: status {process.returncode}: {errors.decode(errors="replace")}')
        return errors if expected else output


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def raw_state(snapshot):
    payload = bytes.fromhex(snapshot['payload_hex'])
    body = bytes([1, 0, 0, 0]) + payload
    return bytes.fromhex('f0 41 10 00 00 4d 12') + body + bytes([-sum(body) % 128, 0xf7])


def application_matches(snapshot, target, before):
    actual = raw_state(snapshot)
    if actual == target:
        return True
    # At 192 kHz the device may retain only its preceding prefix byte 4.
    if len(target) != 1159 or target[13] != 3:
        return False
    retained = bytearray(target)
    retained[15] = bytes.fromhex(before['payload_hex'])[4]
    retained[-2] = -sum(retained[7:-2]) % 128
    return actual == retained


def interrupt(_signum, _frame):
    raise KeyboardInterrupt


def main():
    root = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bin-dir', type=Path, default=root / 'build/strict')
    parser.add_argument('--port', default='hw:CARD=OCTACAPTURE,DEV=0,SUBDEV=1')
    parser.add_argument('--write', action='store_true', help='apply current preset, pan and attack changes, restoring each')
    args = parser.parse_args()
    preset = args.bin_dir / 'octa-preset'

    def state():
        return json.loads(run([args.bin_dir / 'octa-state', args.port]))

    before = state()
    hardware = root / 'build/hardware'
    hardware.mkdir(parents=True, exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix='preset-import-', dir=hardware))
    baseline = directory / 'original.syx'
    baseline.write_bytes(raw_state(before))
    baseline.chmod(0o600)
    original = directory / 'original.octa'
    original.write_bytes(run([preset, 'export', args.port]))
    unchanged_target = run([preset, 'prepare', original, baseline])
    require(state() == before, 'Read-only preparation changed the complete bulk state')
    if not args.write:
        print('Read/export/preparation passed. Use --write for three apply/readback/raw-restore checks.')
        return
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, interrupt)
    changed = bytearray(original.read_bytes())
    pan_offset = 379
    pan = struct.unpack_from('<I', changed, pan_offset)[0]
    new_pan = pan - 1 if pan else 1
    struct.pack_into('<I', changed, pan_offset, new_pan)
    protected = directory / 'existing.syx'
    protected.write_bytes(b'existing recovery snapshot')
    rejected = run([preset, 'apply', original, protected, args.port], expected=1)
    require(b'create recovery snapshot' in rejected and protected.read_bytes() == b'existing recovery snapshot'
            and state() == before, 'Existing recovery file was overwritten or state changed')
    print('PASS backup collision: existing file and complete device state unchanged', flush=True)
    modified = directory / 'pan.octa'
    modified.write_bytes(changed)
    modified_target = run([preset, 'prepare', modified, baseline])
    expected = bytearray(unchanged_target[11:-2])
    expected[0x80:0x84] = bytes((new_pan >> shift) & 15 for shift in (12, 8, 4, 0))
    require(modified_target[11:-2] == expected, 'Pan preset changes other prepared fields')
    attack_data = bytearray(original.read_bytes())
    attack_offset = 109
    attack = struct.unpack_from('<I', attack_data, attack_offset)[0]
    new_attack = attack - 1 if attack else 1
    struct.pack_into('<I', attack_data, attack_offset, new_attack)
    attack_file = directory / 'attack.octa'
    attack_file.write_bytes(attack_data)
    attack_target = run([preset, 'prepare', attack_file, baseline])
    expected = bytearray(unchanged_target[11:-2])
    expected[0x30] = new_attack
    require(attack_target[11:-2] == expected, 'Attack preset changes other prepared fields')
    for name, path, target in (('current', original, unchanged_target), ('pan', modified, modified_target),
                               ('attack', attack_file, attack_target)):
        recovery = directory / f'{name}-recovery.syx'
        try:
            run([preset, 'apply', path, recovery, args.port])
            require(recovery.read_bytes() == baseline.read_bytes(), 'Recovery file differs from original raw state')
            require(recovery.stat().st_mode & 0o077 == 0, 'Recovery file is not private')
            require(application_matches(state(), target, before),
                    f'{name}: complete readback differs from prepared target')
            print(f'PASS {name}: complete import/readback and private raw snapshot', flush=True)
        finally:
            run([preset, 'restore', baseline, args.port])
            require(state() == before, 'Raw recovery did not restore the complete original state')
    print('PASS: three preset imports and exact raw restorations; complete bulk state unchanged.')


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, OSError, ValueError, subprocess.SubprocessError, KeyboardInterrupt) as error:
        print(f'FAIL: {error}')
        raise SystemExit(1) from error
