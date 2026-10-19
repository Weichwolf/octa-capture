#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Boot the isolated sanitizer kernel and check module load/unload without USB passthrough."""

import argparse
import fcntl
import gzip
from pathlib import Path
import re
import shutil
import signal
import stat
import struct
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parent.parent
MARKER = 'OCTA_VM_MODULE_LIFECYCLE_PASS'
REQUIRED = ('KASAN', 'UBSAN', 'FAULT_INJECTION', 'USB_DUMMY_HCD', 'USB_RAW_GADGET', 'IKCONFIG_PROC')
REPORT = re.compile(r'BUG:|WARNING:|KASAN:|UBSAN:|Oops:|Kernel panic|general protection fault')


def entry(name, data=b'', mode=stat.S_IFREG | 0o644, major=0, minor=0):
    encoded = name.encode() + b'\0'
    fields = (1, mode, 0, 0, 1, 0, len(data), 0, 0, major, minor, len(encoded), 0)
    header = b'070701' + ''.join(f'{value:08x}' for value in fields).encode()
    prefix = header + encoded
    return prefix + bytes(-len(prefix) % 4) + data + bytes(-len(data) % 4)


def static_busybox(path):
    image = path.read_bytes()
    if len(image) < 64 or image[:6] != b'\x7fELF\x02\x01' or struct.unpack_from('<H', image, 18)[0] != 62:
        raise ValueError('An x86_64 Linux BusyBox ELF executable is required.')
    offset = struct.unpack_from('<Q', image, 32)[0]
    size, count = struct.unpack_from('<HH', image, 54)
    if size < 56 or not count or offset + size * count > len(image):
        raise ValueError('Invalid BusyBox ELF program headers.')
    if any(struct.unpack_from('<I', image, offset + index * size)[0] == 3 for index in range(count)):
        raise ValueError('BusyBox must be statically linked; its interpreter cannot run in this initramfs.')
    return image


def module_order(kernel, standalone, release):
    modinfo = shutil.which('modinfo') or '/usr/sbin/modinfo'
    paths = {path.stem.replace('-', '_'): path for path in kernel.rglob('*.ko')}
    paths['snd_octa_capture'] = standalone / 'snd-octa-capture.ko'
    builtin = {Path(line).stem.replace('-', '_') for line in (kernel / 'modules.builtin').read_text().splitlines()}
    ordered, visiting = {}, set()

    def visit(name):
        if name in ordered or name in builtin:
            return
        if name in visiting or name not in paths:
            raise ValueError(f'Unresolved or cyclic kernel module dependency: {name}')
        visiting.add(name)
        path = paths[name]
        magic = subprocess.check_output([modinfo, '-F', 'vermagic', str(path)], text=True).split()
        if not magic or magic[0] != release:
            raise ValueError(f'Module does not match the VM kernel: {path}')
        depends = subprocess.check_output([modinfo, '-F', 'depends', str(path)], text=True).strip()
        for dependency in filter(None, depends.split(',')):
            visit(dependency.replace('-', '_'))
        visiting.remove(name)
        ordered[name] = path

    for name in ('snd_usb_audio', 'snd_octa_capture'):
        visit(name)
    return ordered


def init_script(modules, release):
    dependencies = [name for name in modules if name not in ('snd_usb_audio', 'snd_octa_capture')]
    lines = ['#!/bin/busybox sh', 'set -eu', 'export PATH=/bin', '/bin/busybox --install -s /bin',
             'mount -t proc proc /proc', 'mount -t sysfs sysfs /sys',
             'mount -t devtmpfs devtmpfs /dev', 'mount -t debugfs debugfs /sys/kernel/debug',
             "trap 'echo OCTA_VM_MODULE_LIFECYCLE_FAIL; poweroff -f' EXIT",
             f'test "$(uname -r)" = "{release}"',
             'test -c /dev/raw-gadget', 'test -d /sys/class/udc/dummy_udc.0',
             'zcat /proc/config.gz > /tmp/config']
    lines += [f"grep -qx 'CONFIG_{name}=y' /tmp/config" for name in REQUIRED]
    lines += [f'insmod /modules/{name}.ko' for name in dependencies]
    lines += ['for iteration in 1 2 3; do', '  insmod /modules/snd_usb_audio.ko',
              '  insmod /modules/snd_octa_capture.ko', '  test -d /sys/module/snd_usb_audio',
              '  test -d /sys/module/snd_octa_capture', '  rmmod snd_octa_capture',
              '  rmmod snd_usb_audio', '  test ! -d /sys/module/snd_octa_capture',
              '  test ! -d /sys/module/snd_usb_audio', '  echo "OCTA_VM_MODULE_CYCLE $iteration"', 'done']
    lines += [f'rmmod {name}' for name in reversed(dependencies)]
    lines += ['trap - EXIT', f'echo {MARKER}', 'poweroff -f', 'sleep 5', 'exit 1']
    return ('\n'.join(lines) + '\n').encode()


def initramfs(path, busybox, modules, release):
    content = bytearray()
    for name in ('bin', 'dev', 'proc', 'sys', 'tmp', 'modules'):
        content += entry(name, mode=stat.S_IFDIR | 0o755)
    content += entry('dev/console', mode=stat.S_IFCHR | 0o600, major=5, minor=1)
    content += entry('bin/busybox', static_busybox(busybox), stat.S_IFREG | 0o755)
    content += entry('init', init_script(modules, release), stat.S_IFREG | 0o755)
    for name, module in modules.items():
        content += entry('modules/' + name + '.ko', module.read_bytes())
    content += entry('TRAILER!!!')
    path.write_bytes(gzip.compress(content, mtime=0))


def interrupted(_number, _frame):
    # subprocess.run kills/reaps its child when this exception interrupts it.
    raise KeyboardInterrupt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path, help='completed tools/build-kernel-vm.py output directory')
    parser.add_argument('--qemu', type=Path, default=shutil.which('qemu-system-x86_64'))
    parser.add_argument('--busybox', type=Path, required=True, help='statically linked x86_64 BusyBox')
    parser.add_argument('--bios', type=Path, help='optional explicit SeaBIOS image')
    parser.add_argument('--firmware-dir', type=Path, help='optional QEMU firmware/ROM search directory')
    parser.add_argument('--accel', choices=('kvm', 'tcg'), default='kvm')
    parser.add_argument('--timeout', type=int, default=120)
    args = parser.parse_args()
    if args.qemu is None or not 1 <= args.timeout <= 600:
        parser.error('QEMU and a timeout of 1..600 seconds are required.')
    directory = args.directory.resolve(strict=True)
    with (directory / 'build.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        kernel = directory / 'kernel-build'
        release = (kernel / 'include/config/kernel.release').read_text().strip()
        if not re.fullmatch(r'6\.12\.[0-9]+-octa-kasan-vm', release):
            raise ValueError('Expected the private Linux 6.12 sanitizer VM kernel.')
        config = (kernel / '.config').read_text().splitlines()
        if any(f'CONFIG_{name}=y' not in config for name in REQUIRED):
            raise ValueError('Required VM sanitizer/gadget configuration is missing.')
        standalone = Path((directory / 'module-path').read_text().strip())
        modules = module_order(kernel, standalone, release)
        run = Path(tempfile.mkdtemp(prefix='check-', dir=directory))
        archive = run / 'initramfs.cpio.gz'
        initramfs(archive, args.busybox.resolve(strict=True), modules, release)
        command = [str(args.qemu.resolve(strict=True)), '-nodefaults', '-display', 'none',
                   '-serial', 'stdio', '-monitor', 'none', '-nic', 'none', '-no-reboot',
                   '-machine', 'pc,accel=' + args.accel, '-cpu', 'host' if args.accel == 'kvm' else 'max',
                   '-m', '2048', '-smp', '2', '-kernel', str(kernel / 'arch/x86/boot/bzImage'),
                   '-initrd', str(archive), '-append',
                   'console=ttyS0,115200 earlycon=uart8250,io,0x3f8,115200 '
                   'panic=1 oops=panic panic_on_warn=1 kasan.fault=panic slab_nomerge']
        if args.bios:
            command += ['-bios', str(args.bios.resolve(strict=True))]
        if args.firmware_dir:
            command += ['-L', str(args.firmware_dir.resolve(strict=True))]
        log = run / 'console.log'
        print(f'Running isolated VM with no USB passthrough or network. Console: {log}', flush=True)
        previous = signal.signal(signal.SIGTERM, interrupted)
        try:
            with log.open('w') as output:
                result = subprocess.run(command, stdout=output, stderr=subprocess.STDOUT, timeout=args.timeout, check=False)
        finally:
            signal.signal(signal.SIGTERM, previous)
        text = log.read_text(errors='replace')
        cycles = all(f'OCTA_VM_MODULE_CYCLE {number}' in text for number in (1, 2, 3))
        if result.returncode or MARKER not in text or not cycles or REPORT.search(text):
            raise RuntimeError(f'VM module lifecycle/sanitizer check failed; inspect {log}')
        print('PASS sanitizer VM boot and three generic/standalone module load/unload cycles. No device streaming was tested.')


if __name__ == '__main__':
    main()
