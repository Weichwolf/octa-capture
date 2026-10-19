#!/usr/bin/python3 -I
# SPDX-License-Identifier: GPL-2.0-only
"""Bounded, device-specific runtime-PM policy lease for the development helper."""

from contextlib import contextmanager, ExitStack
import fcntl
import json
import os
from pathlib import Path
import re
import select
import signal
import sys

LEASE_SECONDS = 120


def read(device, name):
    return (device / name).read_text().strip()


def identity(device):
    stat = device.stat()
    return stat.st_dev, stat.st_ino, read(device, 'busnum'), read(device, 'devnum')


def read_attribute(descriptor):
    os.lseek(descriptor, 0, os.SEEK_SET)
    return os.read(descriptor, 64).decode('ascii').strip()


def write_attribute(descriptor, value):
    data = value.encode('ascii')
    os.lseek(descriptor, 0, os.SEEK_SET)
    if os.write(descriptor, data) != len(data):
        raise OSError('Short sysfs policy write.')


def find_device(root=Path('/sys/bus/usb/devices')):
    devices = []
    for device in root.iterdir():
        if not re.fullmatch(r'[0-9]+-[0-9]+(?:\.[0-9]+)*', device.name):
            continue
        if read(device, 'idVendor') == '0582' and read(device, 'idProduct') == '0120':
            devices.append(device)
    if len(devices) != 1:
        raise RuntimeError(f'Expected exactly one OCTA-CAPTURE, found {len(devices)}.')
    device = devices[0]
    owners = {interface.joinpath('driver').resolve(strict=True).name
              for interface in root.glob(device.name + ':*') if interface.joinpath('driver').is_symlink()}
    if owners != {'snd-octa-capture'}:
        raise RuntimeError(f'OCTA-CAPTURE must use only snd-octa-capture; found {sorted(owners)}.')
    return device


@contextmanager
def temporary_policy(device):
    with ExitStack() as resources:
        directory = os.open(device, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
        resources.callback(os.close, directory)
        before = identity(device)
        stat = os.fstat(directory)
        if (stat.st_dev, stat.st_ino) != before[:2]:
            raise RuntimeError('USB device changed while opening the policy directory.')
        attributes = {}
        for name in ('control', 'autosuspend_delay_ms'):
            descriptor = os.open('power/' + name, os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW, dir_fd=directory)
            resources.callback(os.close, descriptor)
            attributes[name] = descriptor
        control = read_attribute(attributes['control'])
        delay = read_attribute(attributes['autosuspend_delay_ms'])
        if control not in ('on', 'auto') or not re.fullmatch(r'-?[0-9]{1,10}', delay):
            raise RuntimeError('Unexpected original runtime-PM policy.')
        try:
            # Pinned descriptors cannot follow a bus-path replacement during unplug/replug.
            write_attribute(attributes['autosuspend_delay_ms'], '0')
            write_attribute(attributes['control'], 'auto')
            yield {'device': device.name, 'control': control, 'autosuspend_delay_ms': int(delay)}
        finally:
            if identity(device) != before:
                raise RuntimeError('USB device identity changed; refusing to write a replacement device.')
            errors = []
            for name, value in (('control', 'on'), ('autosuspend_delay_ms', delay), ('control', control)):
                try:
                    write_attribute(attributes[name], value)
                except OSError as error:
                    errors.append(f'{name}: {error}')
            if errors:
                raise RuntimeError('Power-policy restoration failed: ' + '; '.join(errors))
            if read_attribute(attributes['control']) != control or read_attribute(attributes['autosuspend_delay_ms']) != delay:
                raise RuntimeError('Restored power-policy readback differs.')


def lease(device, input_fd, output, timeout=LEASE_SECONDS):
    with temporary_policy(device) as original:
        print(json.dumps({'ready': True, 'original': original, 'lease_seconds': timeout}), file=output, flush=True)
        ready, _, _ = select.select([input_fd], [], [], timeout)
        if ready:
            os.read(input_fd, 1)  # EOF or any byte releases the lease; no commands are accepted.
    print(json.dumps({'restored': True, 'reason': 'closed' if ready else 'timeout'}), file=output, flush=True)


def interrupted(_signal, _frame):
    raise KeyboardInterrupt


def main():
    if os.geteuid() != 0 or len(sys.argv) != 1:
        raise RuntimeError('Use sudo octa-capture-dev runtime-pm; no device/path arguments are accepted.')
    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    # The root-owned /run parent prevents user-created lock files or path substitution.
    descriptor = os.open('/run/octa-capture-runtime-pm.lock', os.O_CREAT | os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        lease(find_device(), sys.stdin.fileno(), sys.stdout)
    finally:
        os.close(descriptor)


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        raise SystemExit(130) from None
    except (OSError, RuntimeError, ValueError) as error:
        print(f'FAIL: {error}', file=sys.stderr)
        raise SystemExit(1) from error
