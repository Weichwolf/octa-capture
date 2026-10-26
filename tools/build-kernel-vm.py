#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Build an isolated sanitizer kernel and matching standalone module; install nothing."""

import argparse
import fcntl
import os
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parent.parent
REQUIRED = ('CONFIG_KASAN=y', 'CONFIG_KASAN_GENERIC=y', 'CONFIG_UBSAN=y', 'CONFIG_UBSAN_ALIGNMENT=y',
            'CONFIG_KCOV=y',
            'CONFIG_FAULT_INJECTION=y', 'CONFIG_FAILSLAB=y', 'CONFIG_FAIL_PAGE_ALLOC=y',
            'CONFIG_FAULT_INJECTION_DEBUG_FS=y', 'CONFIG_SLUB_DEBUG=y',
            'CONFIG_USB_DUMMY_HCD=y', 'CONFIG_USB_RAW_GADGET=y', 'CONFIG_USB_MON=y', 'CONFIG_SND_USB_AUDIO=m',
            'CONFIG_MODULES=y', 'CONFIG_IKCONFIG_PROC=y', 'CONFIG_X86_5LEVEL=y', 'CONFIG_WERROR=y')
PM_REQUIRED = ('CONFIG_PM=y', 'CONFIG_PM_SLEEP=y', 'CONFIG_SUSPEND=y',
               'CONFIG_PM_DEBUG=y', 'CONFIG_PM_ADVANCED_DEBUG=y',
               'CONFIG_FUNCTION_TRACER=y', 'CONFIG_DYNAMIC_FTRACE=y')


def configuration(directory, power_management):
    base = ROOT / 'kernel/vm.config'
    if not power_management:
        return base
    # Keep the existing minimal VM and its calibrated fault ordinals untouched.
    lines = [line for line in base.read_text().splitlines() if not line.startswith('CONFIG_LOCALVERSION=')]
    lines += [*PM_REQUIRED, 'CONFIG_FTRACE=y', 'CONFIG_LOCALVERSION="-pm-octa-kasan-vm"']
    path = directory / 'pm.config'
    path.write_text('\n'.join(lines) + '\n')
    return path


def prepare_source(source, patch, run):
    command = ['patch', '--force', '--fuzz=0', '--directory=' + str(source), '-p1', '--input=' + str(patch)]
    applied = run([*command, '--dry-run', '--reverse'], check=False, capture_output=True)
    if applied.returncode == 0:
        return
    # Validate the entire forward patch before changing any source file.
    run([*command, '--dry-run', '--forward'])
    run([*command, '--forward'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path, help='extracted, matching Linux 6.12 source tree')
    parser.add_argument('directory', type=Path, help='private VM build directory outside the source tree')
    parser.add_argument('--jobs', type=int, choices=range(1, 9), default=2)
    parser.add_argument('--power-management', action='store_true',
                        help='build the separate suspend/runtime-PM profile with function tracing')
    args = parser.parse_args()
    source = args.source.resolve(strict=True)
    directory = args.directory.resolve()
    kernel = directory / 'kernel-build'
    if kernel.is_relative_to(source):
        raise ValueError('The kernel build directory must be outside its source tree.')
    text = (source / 'Makefile').read_text().splitlines()
    if 'VERSION = 6' not in text or 'PATCHLEVEL = 12' not in text:
        raise ValueError('The sanitizer fixture currently targets Linux 6.12.')
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / 'build.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        (directory / 'module-path').unlink(missing_ok=True)
        kernel.mkdir(exist_ok=True)

        def run(command, **kwargs):
            print('+', ' '.join(map(str, command)), flush=True)
            kwargs.setdefault('check', True)
            return subprocess.run(list(map(str, command)), pass_fds=(lock.fileno(),), **kwargs)

        prepare_source(source, ROOT / 'kernel/vm-linux-6.12.patch', run)
        prepare_source(source, ROOT / 'kernel/vm-alignment-linux-6.12.patch', run)
        if args.power_management:
            prepare_source(source, ROOT / 'kernel/vm-pm-linux-6.12.patch', run)
            prepare_source(source, ROOT / 'kernel/vm-pm-alignment-linux-6.12.patch', run)
            prepare_source(source, ROOT / 'kernel/vm-pm-ring-alignment-linux-6.12.patch', run)
        make = ['make', '-C', source, 'O=' + str(kernel), 'LLVM=-19', 'ARCH=x86_64']
        environment = os.environ | {'KCONFIG_ALLCONFIG': str(configuration(directory, args.power_management))}
        run([*make, 'allnoconfig'], env=environment)
        config = (kernel / '.config').read_text().splitlines()
        required = REQUIRED + (PM_REQUIRED if args.power_management else ())
        missing = [option for option in required if option not in config]
        if missing:
            raise ValueError('Required VM kernel options were not enabled: ' + ', '.join(missing))
        run([*make, '-j' + str(args.jobs), 'W=1', 'KCFLAGS=-Werror', 'bzImage', 'modules'])
        stage = Path(tempfile.mkdtemp(prefix='module-', dir=directory))
        module = stage / 'source'
        environment = os.environ | {'KDIR': str(kernel), 'KERNEL_CC': 'clang-19', 'LLVM': '-19'}
        run([ROOT / 'tools/build-module.sh', source, module], env=environment)
        (directory / 'module-path').write_text(str(module) + '\n')
        print('Built an isolated sanitizer kernel and module. Nothing was installed or loaded on the host.', flush=True)


if __name__ == '__main__':
    main()
