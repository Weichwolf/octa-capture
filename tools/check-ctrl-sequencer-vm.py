#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Check private CTRL/public DIN policy on the isolated ALSA sequencer VM."""

import argparse
import gzip
import fcntl
from pathlib import Path
import runpy
import re
import shutil
import stat
import subprocess
import tempfile

BASE = runpy.run_path(str(Path(__file__).with_name('check-kernel-vm.py')))
MARKER = 'OCTA_CTRL_SEQ_VM_PASS'


def init_script(modules, release):
    lines = ['#!/bin/busybox sh', 'set -eu', 'export PATH=/bin', '/bin/busybox --install -s /bin',
             'mount -t proc proc /proc', 'mount -t sysfs sysfs /sys',
             'mount -t devtmpfs devtmpfs /dev', 'mount -t debugfs debugfs /sys/kernel/debug',
             "trap 'echo OCTA_CTRL_SEQ_VM_FAIL; poweroff -f' EXIT",
             f'test "$(uname -r)" = "{release}"', 'zcat /proc/config.gz > /tmp/config']
    lines += [f"grep -qx 'CONFIG_{name}=y' /tmp/config"
              for name in (*BASE['REQUIRED'], 'SND_SEQUENCER')]
    lines += ["grep -qx 'CONFIG_SND_SEQ_MIDI=m' /tmp/config"]
    dependencies = [name for name in modules if name not in ('snd_usb_audio', 'snd_octa_capture')]
    lines += [f'insmod /modules/{name}.ko' for name in dependencies]
    lines += BASE['usb_functions']()
    lines += ['for mode in normal expansion; do', '  usb_start "$mode"',
              '  /bin/ctrl-sequencer-fixture', '  usb_stop', '  echo "OCTA_CTRL_SEQ_UNIT_PASS $mode"', 'done']
    lines += [f'rmmod {name}' for name in reversed(dependencies)]
    lines += ['trap - EXIT', f'echo {MARKER}', 'poweroff -f', 'sleep 5', 'exit 1']
    return ('\n'.join(lines) + '\n').encode()


def archive(path, args, modules, release):
    entry = BASE['entry']
    content = bytearray()
    for name in ('bin', 'dev', 'proc', 'sys', 'tmp', 'modules'):
        content += entry(name, mode=stat.S_IFDIR | 0o755)
    content += entry('dev/console', mode=stat.S_IFCHR | 0o600, major=5, minor=1)
    for name, binary in (('busybox', args.busybox), ('usb-fixture', args.usb_fixture),
                         ('ctrl-sequencer-fixture', args.seq_fixture)):
        content += entry('bin/' + name, BASE['static_executable'](binary.resolve(strict=True)), stat.S_IFREG | 0o755)
    content += entry('init', init_script(modules, release), stat.S_IFREG | 0o755)
    for name, module in modules.items():
        content += entry('modules/' + name + '.ko', module.read_bytes())
    content += entry('TRAILER!!!')
    path.write_bytes(gzip.compress(content, mtime=0))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    parser.add_argument('--qemu', type=Path, default=shutil.which('qemu-system-x86_64'))
    parser.add_argument('--busybox', type=Path, required=True)
    parser.add_argument('--usb-fixture', type=Path, required=True)
    parser.add_argument('--seq-fixture', type=Path, required=True)
    parser.add_argument('--bios', type=Path)
    parser.add_argument('--firmware-dir', type=Path)
    args = parser.parse_args()
    if args.qemu is None:
        parser.error('QEMU is required.')
    directory = args.directory.resolve(strict=True)
    with (directory / 'build.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        kernel = directory / 'kernel-build'
        release = (kernel / 'include/config/kernel.release').read_text().strip()
        if not re.fullmatch(r'6\.12\.[0-9]+-ctrl-seq-octa-kasan-vm', release):
            raise ValueError('Use the private --control-sequencer build profile.')
        config = (kernel / '.config').read_text().splitlines()
        if (any(f'CONFIG_{name}=y' not in config for name in (*BASE['REQUIRED'], 'SND_SEQUENCER')) or
                'CONFIG_SND_SEQ_MIDI=m' not in config):
            raise ValueError('Required sanitizer/sequencer configuration is missing.')
        standalone = Path((directory / 'module-path').read_text().strip())
        modules = BASE['module_order'](kernel, standalone, release, ('snd_seq_midi',))
        run = Path(tempfile.mkdtemp(prefix='check-', dir=directory))
        initrd = run / 'initramfs.cpio.gz'
        archive(initrd, args, modules, release)
        command = [str(args.qemu.resolve(strict=True)), '-nodefaults', '-display', 'none',
                   '-serial', 'stdio', '-monitor', 'none', '-nic', 'none', '-no-reboot',
                   '-machine', 'pc,accel=kvm', '-cpu', 'host', '-m', '2048', '-smp', '2',
                   '-kernel', str(kernel / 'arch/x86/boot/bzImage'), '-initrd', str(initrd),
                   '-append', 'console=ttyS0,115200 panic=1 oops=panic panic_on_warn=1 kasan.fault=panic slab_nomerge']
        if args.bios:
            command += ['-bios', str(args.bios.resolve(strict=True))]
        if args.firmware_dir:
            command += ['-L', str(args.firmware_dir.resolve(strict=True))]
        log = run / 'console.log'
        with log.open('w') as output:
            result = subprocess.run(command, stdout=output, stderr=subprocess.STDOUT, check=False, timeout=120,
                                    pass_fds=(lock.fileno(),))
        text = log.read_text(errors='replace')
        lines = text.splitlines()
        if (result.returncode or lines.count(MARKER) != 1 or 'FAIL' in text or BASE['REPORT'].search(text) or
                lines.count('OCTA_CTRL_SEQ_PRIVATE_PASS') != 2 or
                any(lines.count(f'OCTA_CTRL_SEQ_UNIT_PASS {mode}') != 1 for mode in ('normal', 'expansion'))):
            raise RuntimeError(f'CTRL sequencer regression failed; inspect {log}')
        print(f'PASS base/EXP private CTRL subscriptions rejected; raw CTRL available and DIN bridgeable. {log}')


if __name__ == '__main__':
    main()
