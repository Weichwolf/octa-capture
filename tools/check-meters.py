#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Check meter JSON, graceful shutdown, exclusive CTRL access and unchanged state."""

import argparse
from contextlib import contextmanager
import json
import math
import os
from pathlib import Path
import selectors
import signal
import subprocess
import sys
import time


def require(condition, description):
    if not condition:
        raise RuntimeError(description)


def run(command, timeout=8):
    return subprocess.run(command, capture_output=True, timeout=timeout, check=False)


def state(args):
    result = run([str(args.bin_dir / 'octa-state'), args.port])
    require(result.returncode == 0, f'Bulk read failed: {result.stderr.decode(errors="replace")}')
    return json.loads(result.stdout)


def validate_packet(line):
    packet = json.loads(line)
    levels = packet['input_levels']
    decibels = packet['input_db']
    require(len(levels) == len(decibels) == 8, 'Expected eight input levels and dB values')
    for level, displayed in zip(levels, decibels, strict=True):
        require(type(level) is int and 0 <= level <= 16383, 'Invalid raw input level')
        expected = 20 * math.log10(level / 16383) if level else -96
        require(type(displayed) in (int, float) and math.isfinite(displayed)
                and math.isclose(displayed, expected, abs_tol=0.001), 'Incorrect display conversion')


def command(args, duration):
    return [str(args.bin_dir / 'octa-meters'), str(duration), args.port]


def stopped(args):
    # A passive read must find no lingering meter stream. Do not send any MIDI.
    result = run(['amidi', '-p', args.port, '-d', '-t', '1'], timeout=4)
    require(result.returncode == 0 and not result.stdout and not result.stderr,
            f'CTRL was not quiet after shutdown: {result!r}')


@contextmanager
def observer(args, duration=60000):
    process = subprocess.Popen(command(args, duration), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        yield process
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate(timeout=2)
        if process.stdout is not None:
            process.stdout.close()
        if process.stderr is not None:
            process.stderr.close()


def first_packet(process):
    pending = bytearray()
    deadline = time.monotonic() + 4
    with selectors.DefaultSelector() as selector:
        selector.register(process.stdout, selectors.EVENT_READ)
        while time.monotonic() < deadline:
            if not selector.select(max(0, deadline - time.monotonic())):
                break
            chunk = os.read(process.stdout.fileno(), 4096)
            require(chunk, 'Observer ended before the first complete packet')
            pending.extend(chunk)
            if b'\n' in pending:
                line, _, remainder = pending.partition(b'\n')
                validate_packet(line)
                return bytes(remainder)
            require(len(pending) <= 4096, 'Unbounded or incomplete JSON packet')
    raise RuntimeError('Timed out waiting for the first meter packet')


def check_normal(args):
    result = run(command(args, 2000))
    require(result.returncode == 0 and b'meter output disabled' in result.stderr,
            f'Normal observation failed: {result!r}')
    lines = result.stdout.splitlines()
    require(lines, 'Normal observation received no input levels')
    for line in lines:
        validate_packet(line)
    stopped(args)
    print(f'Normal completion: {len(lines)} valid packets', flush=True)


def check_signals(args):
    for signum in (signal.SIGINT, signal.SIGTERM):
        with observer(args) as process:
            first_packet(process)
            started = time.monotonic()
            process.send_signal(signum)
            _, errors = process.communicate(timeout=5)
            require(process.returncode == 0 and b'meter output disabled' in errors,
                    f'{signum.name} did not finish cleanly: {errors!r}')
        stopped(args)
        print(f'{signum.name}: stopped in {time.monotonic() - started:.3f} s (including quiet check)',
              flush=True)


def check_pipe(args):
    with observer(args) as process:
        first_packet(process)
        process.stdout.close()
        process.stdout = None
        _, errors = process.communicate(timeout=5)
        require(process.returncode == 1, f'Closed stdout was not reported: {errors!r}')
    stopped(args)
    print('Closed output pipe: stopped and CTRL quiet', flush=True)


def check_exclusive(args):
    with observer(args, duration=2000) as process:
        remainder = first_packet(process)
        second = run(command(args, 500))
        require(second.returncode == 1 and b'open CTRL' in second.stderr,
                f'Competing observer was not rejected at open: {second!r}')
        output, errors = process.communicate(timeout=5)
        require(process.returncode == 0 and b'meter output disabled' in errors,
                f'Competing open disturbed the active observer: {errors!r}')
        for line in (remainder + output).splitlines():
            validate_packet(line)
    stopped(args)
    print('Exclusive CTRL access: second observer rejected; first completed', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bin-dir', type=Path, default=Path('build/strict'))
    parser.add_argument('--port', default='hw:CARD=OCTACAPTURE,DEV=0,SUBDEV=1')
    args = parser.parse_args()
    baseline = state(args)
    try:
        check_normal(args)
        check_signals(args)
        check_pipe(args)
        check_exclusive(args)
    finally:
        require(state(args) == baseline, 'Complete bulk state changed during meter checks')
    print('All meter checks passed; complete bulk state unchanged.')


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        print(f'Meter check failed: {error}', file=sys.stderr)
        sys.exit(1)
