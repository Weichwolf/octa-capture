#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Read the kernel-owned OCTA-CAPTURE status control and check its boundaries."""

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import subprocess
import time

CONTROL = 'iface=CARD,name=OCTA-CAPTURE Status'


def read_status(card):
    result = subprocess.run(['amixer', '-c', card, 'cget', CONTROL], capture_output=True,
                            timeout=3, check=False)
    if result.returncode:
        raise RuntimeError(result.stderr.decode(errors='replace'))
    output = result.stdout.decode('ascii')
    if 'type=BYTES,access=r--v----,values=8' not in output:
        raise RuntimeError('Status control is not read-only/volatile with exactly eight bytes')
    payload = bytes(int(value.strip(), 16) for value in output.split(': values=', 1)[1].strip().split(','))
    if len(payload) != 8 or payload[0] != 1 or int.from_bytes(payload[3:6], 'little') == 0:
        raise RuntimeError('Invalid coherent status packet')
    return payload


def stress_reader(card, deadline):
    count = 0
    while time.monotonic() < deadline:
        read_status(card)
        count += 1
        time.sleep(0.02)
    return count


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--card', default='OCTACAPTURE')
    parser.add_argument('--stress', action='store_true', help='four concurrent status readers for two seconds')
    args = parser.parse_args()
    packet = read_status(args.card)
    rejected = subprocess.run(['amixer', '-c', args.card, 'cset', CONTROL, ','.join(['0x00'] * 8)],
                              capture_output=True, timeout=3, check=False)
    if rejected.returncode == 0:
        raise RuntimeError('The read-only status control accepted a write')
    counts = [1]
    if args.stress:
        deadline = time.monotonic() + 2
        with ThreadPoolExecutor(max_workers=4) as executor:
            counts = list(executor.map(lambda _: stress_reader(args.card, deadline), range(4)))
        if any(count == 0 for count in counts):
            raise RuntimeError('Not every concurrent status reader completed a read')
    print(json.dumps({'sample_rate_hz': int.from_bytes(packet[3:6], 'little'),
                      'raw_status_hex': packet.hex(), 'read_only': True,
                      'valid_reads': 1 + sum(counts) if args.stress else 1,
                      'per_reader_reads': counts, 'concurrent_readers': len(counts)}))


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, OSError, ValueError, IndexError, subprocess.SubprocessError) as error:
        print(f'FAIL: {error}')
        raise SystemExit(1) from error
