#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Check isolated sanitizer module/optional synthetic USB lifecycles without USB passthrough."""

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
REQUIRED = ('KASAN', 'KASAN_GENERIC', 'UBSAN', 'UBSAN_ALIGNMENT', 'FAULT_INJECTION',
            'FAILSLAB', 'FAULT_INJECTION_DEBUG_FS', 'SLUB_DEBUG',
            'USB_DUMMY_HCD', 'USB_RAW_GADGET', 'USB_MON', 'IKCONFIG_PROC')
REPORT = re.compile(r'BUG:|WARNING:|KASAN:|UBSAN:|Oops:|Kernel panic|general protection fault|'
                    r'OCTA_VM_MODULE_LIFECYCLE_FAIL')
STATUS_ERRORS = ('empty', 'short', 'phase', 'zero-rate', 'missing-zero', 'missing-one', 'timeout')
PM_REQUIRED = ('PM', 'PM_SLEEP', 'SUSPEND', 'PM_DEBUG', 'PM_ADVANCED_DEBUG',
               'FUNCTION_TRACER', 'DYNAMIC_FTRACE')
PM_CALLBACKS = ('usb_audio_suspend', 'snd_usb_octa_capture_pcm_suspend',
                'usb_audio_resume', 'snd_usb_octa_capture_pcm_resume')
PM_CASES = tuple(f'{mode} {cycle}' for mode in ('normal', 'expansion') for cycle in (1, 2, 3))


def entry(name, data=b'', mode=stat.S_IFREG | 0o644, major=0, minor=0):
    encoded = name.encode() + b'\0'
    fields = (1, mode, 0, 0, 1, 0, len(data), 0, 0, major, minor, len(encoded), 0)
    header = b'070701' + ''.join(f'{value:08x}' for value in fields).encode()
    prefix = header + encoded
    return prefix + bytes(-len(prefix) % 4) + data + bytes(-len(data) % 4)


def static_executable(path):
    image = path.read_bytes()
    if len(image) < 64 or image[:6] != b'\x7fELF\x02\x01' or struct.unpack_from('<H', image, 18)[0] != 62:
        raise ValueError('An x86_64 Linux ELF executable is required.')
    offset = struct.unpack_from('<Q', image, 32)[0]
    size, count = struct.unpack_from('<HH', image, 54)
    if size < 56 or not count or offset + size * count > len(image):
        raise ValueError('Invalid ELF program headers.')
    if any(struct.unpack_from('<I', image, offset + index * size)[0] == 3 for index in range(count)):
        raise ValueError('VM executables must be statically linked; no interpreter is included.')
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


def usb_functions():
    lines = ['usb_start() {', '  insmod /modules/snd_octa_capture.ko',
            '  /bin/usb-fixture gadget "${1:-normal}" &', '  gadget=$!',
            '  for _ in $(seq 1 20); do',
            '    test ! -c /dev/snd/controlC0 || break', '    sleep 1', '  done',
            '  test -c /dev/snd/controlC0', '}', 'usb_finish() {',
            '  wait "$gadget" || test "$?" -eq 143',
            '  for _ in $(seq 1 10); do',
            '    test -d /sys/class/sound/card0 || break', '    sleep 1', '  done',
            '  test ! -d /sys/class/sound/card0', '  rmmod snd_octa_capture',
            '}', 'usb_stop() {', '  kill "$gadget"', '  usb_finish', '}']
    return lines


def usb_script():
    lines = usb_functions() + ['for iteration in 1 2 3; do', '  usb_start normal',
            '  /bin/usb-fixture read-status &', '  reader_one=$!',
            '  /bin/usb-fixture read-status &', '  reader_two=$!',
            '  wait "$reader_one"', '  wait "$reader_two"', '  usb_stop',
            '  echo "OCTA_VM_USB_CYCLE $iteration"', 'done']
    lines += ['usb_start expansion',
              'test "$(cat /sys/bus/usb/devices/1-1/idProduct)" = 0121',
              '/bin/usb-fixture read-status expansion', 'usb_stop',
              'echo OCTA_VM_USB_EXPANSION_PASS']
    lines += ['for scenario in ' + ' '.join(STATUS_ERRORS) + '; do',
              '  usb_start "$scenario"', '  /bin/usb-fixture read-status "$scenario"',
              '  usb_stop', 'done']
    lines += ['usb_start timeout', '/bin/usb-fixture disconnect-read "$gadget"', 'usb_finish',
              'usb_start normal', '/bin/usb-fixture reject-pcm', '/bin/usb-fixture allocation-status',
              '/bin/usb-fixture read-status', 'usb_stop',
              'echo OCTA_VM_STATUS_RECOVERY_PASS']
    lines += ['for target in pcm-meter audio-stream; do', '  usb_start normal',
              '  /bin/usb-fixture allocation-probe "$target"', '  usb_stop', 'done']
    return lines


def usb_pm_script():
    lines = usb_functions() + [
        'mount -t tracefs tracefs /sys/kernel/tracing',
        'trace=/sys/kernel/tracing', 'device=/sys/bus/usb/devices/1-1',
        'wait_suspended() {', '  stable=0', '  for _ in $(seq 1 100); do',
        '    if test "$(cat "$device/power/runtime_status")" = suspended; then',
        '      stable=$((stable + 1))', '      test "$stable" -lt 2 || return 0',
        '    else', '      stable=0', '    fi', '    sleep 0.1', '  done',
        '  echo OCTA_VM_RUNTIME_PM_SUSPEND_TIMEOUT',
        '  for node in "$device"/power "$device":*/power; do',
        '    for field in control runtime_status runtime_usage runtime_active_kids wakeup; do',
        '      test -r "$node/$field" || continue', '      echo "$node/$field"',
        '      cat "$node/$field"', '    done', '  done', '  cat "$trace/trace"', '  return 1', '}',
        'for mode in normal expansion; do', '  usb_start "$mode"',
        '  test "$(readlink -f "$device:1.0/driver")" = /sys/bus/usb/drivers/snd-octa-capture',
        '  product=0120', '  test "$mode" != expansion || product=0121',
        '  test "$(cat "$device/idProduct")" = "$product"',
        '  echo on > "$device/power/control"']
    filters = ' '.join(f'"{function}:mod:snd_octa_capture"' for function in PM_CALLBACKS)
    lines += ["  printf '%s\\n' " + filters + ' > "$trace/set_ftrace_filter"']
    lines += ['  echo "OCTA_VM_RUNTIME_PM_FILTER_BEGIN $mode"', '  cat "$trace/set_ftrace_filter"',
              '  echo "OCTA_VM_RUNTIME_PM_FILTER_END $mode"']
    for function in PM_CALLBACKS:
        lines += [f'  grep -Fx "{function} [snd_octa_capture]" "$trace/set_ftrace_filter" > /dev/null']
    lines += ['  echo function > "$trace/current_tracer"',
              '  for cycle in 1 2 3; do', '    echo on > "$device/power/control"',
              '    test "$(cat "$device/power/runtime_status")" = active',
              '    echo 0 > "$trace/tracing_on"', '    echo > "$trace/trace"',
              '    echo 1 > "$trace/tracing_on"', '    echo "OCTA_VM_RUNTIME_PM_CYCLE_BEGIN $mode $cycle"',
              '    echo 0 > "$device/power/autosuspend_delay_ms"',
              '    echo auto > "$device/power/control"', '    wait_suspended',
              '    echo 5000 > "$device/power/autosuspend_delay_ms"',
              '    test "$(cat "$device/power/runtime_status")" = suspended',
              '    echo "OCTA_VM_RUNTIME_PM_SUSPENDED $mode $cycle"',
              '    /bin/usb-fixture read-status "$mode"',
              '    test "$(cat "$device/power/runtime_status")" = active',
              '    echo "OCTA_VM_RUNTIME_PM_ACTIVE $mode $cycle"',
              '    echo on > "$device/power/control"', '    echo 0 > "$trace/tracing_on"',
              '    echo "OCTA_VM_RUNTIME_PM_TRACE_BEGIN $mode $cycle"', '    cat "$trace/trace"',
              '    echo "OCTA_VM_RUNTIME_PM_TRACE_END $mode $cycle"',
              '    echo "OCTA_VM_RUNTIME_PM_CYCLE $mode $cycle"', '  done',
              '  echo nop > "$trace/current_tracer"', '  echo > "$trace/set_ftrace_filter"',
              '  echo 0 > "$device/power/autosuspend_delay_ms"', '  echo auto > "$device/power/control"',
              '  wait_suspended', '  usb_stop', '  echo "OCTA_VM_RUNTIME_PM_UNIT_PASS $mode"', 'done']
    return lines


def init_script(modules, release, usb=False, runtime_pm=False):
    dependencies = [name for name in modules if name not in ('snd_usb_audio', 'snd_octa_capture')]
    lines = ['#!/bin/busybox sh', 'set -eu', 'export PATH=/bin', '/bin/busybox --install -s /bin',
             'mount -t proc proc /proc', 'mount -t sysfs sysfs /sys',
             'mount -t devtmpfs devtmpfs /dev', 'mount -t debugfs debugfs /sys/kernel/debug',
             "trap 'echo OCTA_VM_MODULE_LIFECYCLE_FAIL; poweroff -f' EXIT",
             f'test "$(uname -r)" = "{release}"',
             'test -c /dev/raw-gadget', 'test -d /sys/class/udc/dummy_udc.0',
             'zcat /proc/config.gz > /tmp/config']
    lines += [f"grep -qx 'CONFIG_{name}=y' /tmp/config" for name in REQUIRED]
    if runtime_pm:
        lines += [f"grep -qx 'CONFIG_{name}=y' /tmp/config" for name in PM_REQUIRED]
    lines += [f'insmod /modules/{name}.ko' for name in dependencies]
    lines += ['for iteration in 1 2 3; do', '  insmod /modules/snd_usb_audio.ko',
              '  insmod /modules/snd_octa_capture.ko', '  test -d /sys/module/snd_usb_audio',
              '  test -d /sys/module/snd_octa_capture', '  rmmod snd_octa_capture',
              '  rmmod snd_usb_audio', '  test ! -d /sys/module/snd_octa_capture',
              '  test ! -d /sys/module/snd_usb_audio', '  echo "OCTA_VM_MODULE_CYCLE $iteration"', 'done']
    if runtime_pm:
        lines += usb_pm_script()
    elif usb:
        lines += usb_script()
    lines += [f'rmmod {name}' for name in reversed(dependencies)]
    lines += ['trap - EXIT', f'echo {MARKER}', 'poweroff -f', 'sleep 5', 'exit 1']
    return ('\n'.join(lines) + '\n').encode()


def initramfs(path, busybox, modules, release, usb_fixture=None, runtime_pm=False):
    content = bytearray()
    for name in ('bin', 'dev', 'proc', 'sys', 'tmp', 'modules'):
        content += entry(name, mode=stat.S_IFDIR | 0o755)
    content += entry('dev/console', mode=stat.S_IFCHR | 0o600, major=5, minor=1)
    content += entry('bin/busybox', static_executable(busybox), stat.S_IFREG | 0o755)
    if usb_fixture:
        content += entry('bin/usb-fixture', static_executable(usb_fixture), stat.S_IFREG | 0o755)
    content += entry('init', init_script(modules, release, usb_fixture is not None, runtime_pm), stat.S_IFREG | 0o755)
    for name, module in modules.items():
        content += entry('modules/' + name + '.ko', module.read_bytes())
    content += entry('TRAILER!!!')
    path.write_bytes(gzip.compress(content, mtime=0))


def interrupted(_number, _frame):
    # subprocess.run kills/reaps its child when this exception interrupts it.
    raise KeyboardInterrupt


def allocation_evidence(text, function, marker):
    boundary = r'FAULT_INJECTION:|OCTA_VM_(?:STATUS_ALLOCATION_PASS|PROBE_ALLOCATION_PASS)'
    segment = r'(?:(?!' + boundary + r').)*'
    return re.search(r'FAULT_INJECTION: forcing a failure\.' + segment + r'\n[ \t]+' + re.escape(function) +
                     r'\+[^\n]*\[snd_octa_capture\]' + segment + re.escape(marker), text, re.DOTALL) is not None


def pm_evidence(text):
    lines = text.splitlines()
    phases = ('CYCLE_BEGIN', 'SUSPENDED', 'ACTIVE', 'TRACE_BEGIN', 'TRACE_END', 'CYCLE')
    if (lines.count('OCTA_VM_STATUS_READ_PASS') != len(PM_CASES) or 'FAULT_INJECTION:' in text or
            'OCTA_VM_RUNTIME_PM_SUSPEND_TIMEOUT' in text or
            any(sum(line.startswith(f'OCTA_VM_RUNTIME_PM_{phase} ') for line in lines) != len(PM_CASES)
                for phase in phases)):
        return False
    if any(sum(line.startswith(f'OCTA_VM_RUNTIME_PM_{phase} ') for line in lines) != 2
           for phase in ('FILTER_BEGIN', 'FILTER_END', 'UNIT_PASS')):
        return False
    filters_end = {}
    previous_unit = -1
    for mode in ('normal', 'expansion'):
        if lines.count(f'OCTA_VM_RUNTIME_PM_UNIT_PASS {mode}') != 1:
            return False
        start, end = (f'OCTA_VM_RUNTIME_PM_FILTER_{edge} {mode}' for edge in ('BEGIN', 'END'))
        if (lines.count(start) != 1 or lines.count(end) != 1 or lines.index(start) >= lines.index(end) or
                lines.index(start) <= previous_unit):
            return False
        filters = lines[lines.index(start) + 1:lines.index(end)]
        if any(filters.count(f'{function} [snd_octa_capture]') != 1 for function in PM_CALLBACKS):
            return False
        filters_end[mode] = lines.index(end)
        previous_unit = lines.index(f'OCTA_VM_RUNTIME_PM_UNIT_PASS {mode}')
        if previous_unit <= filters_end[mode]:
            return False
    previous = -1
    for case in PM_CASES:
        markers = [f'OCTA_VM_RUNTIME_PM_{phase} {case}' for phase in phases]
        if any(lines.count(marker) != 1 for marker in markers):
            return False
        positions = [lines.index(marker) for marker in markers]
        if positions != sorted(positions) or positions[0] <= max(previous, filters_end[case.split()[0]]):
            return False
        if lines[positions[1] + 1:positions[2]].count('OCTA_VM_STATUS_READ_PASS') != 1:
            return False
        segment = '\n'.join(lines[positions[3] + 1:positions[4]])
        calls = [re.search(r'\b[0-9]+\.[0-9]+:\s+' + function + r'(?:\s|\+|\[|$)', segment)
                 for function in PM_CALLBACKS]
        if any(call is None for call in calls) or [call.start() for call in calls] != sorted(call.start() for call in calls):
            return False
        if positions[-1] >= lines.index(f'OCTA_VM_RUNTIME_PM_UNIT_PASS {case.split()[0]}'):
            return False
        previous = positions[-1]
    return True


def verify_output(result, text, usb, runtime_pm=False):
    lines = text.splitlines()
    cycles = all(lines.count(f'OCTA_VM_MODULE_CYCLE {number}') == 1 for number in (1, 2, 3))
    if runtime_pm:
        cycles = cycles and usb and pm_evidence(text)
    elif usb:
        cycles = cycles and all(lines.count(f'OCTA_VM_USB_CYCLE {number}') == 1 for number in (1, 2, 3))
        cycles = cycles and lines.count('OCTA_VM_STATUS_READ_PASS') == 10
        cycles = cycles and lines.count('OCTA_VM_USB_EXPANSION_PASS') == 1
        cycles = cycles and all(lines.count(f'OCTA_VM_STATUS_ERROR_PASS {name}') == 1 for name in STATUS_ERRORS)
        cycles = cycles and lines.count('OCTA_VM_STATUS_ERROR_PASS disconnect') == 1
        cycles = cycles and lines.count('OCTA_VM_STATUS_UNPLUG_PASS') == 1
        cycles = cycles and lines.count('OCTA_VM_STATUS_RECOVERY_PASS') == 1
        cycles = cycles and lines.count('OCTA_VM_PCM_FAILURE_PASS') == 1
        cycles = cycles and lines.count('OCTA_VM_PCM_ALT_STALL 1 2') == 2
        cycles = cycles and lines.count('OCTA_VM_PCM_ALT_STALL 0 2') == 2
        cycles = cycles and sum(line.startswith('OCTA_VM_PCM_ALT_STALL ') for line in lines) == 4
        cycles = cycles and lines.count('OCTA_VM_STATUS_ALLOCATION_PASS') == 1
        cycles = cycles and len(re.findall(r'FAULT_INJECTION: forcing a failure\.', text)) == 3
        cycles = cycles and allocation_evidence(text, 'octa_status_get', 'OCTA_VM_STATUS_ALLOCATION_PASS')
        for target, function in (('pcm-meter', 'snd_usb_octa_capture_add_pcm_controls'),
                                 ('audio-stream', 'snd_usb_add_audio_stream')):
            marker = f'OCTA_VM_PROBE_ALLOCATION_PASS {target}'
            cycles = cycles and lines.count(marker) == 1 and allocation_evidence(text, function, marker)
    return not result and lines.count(MARKER) == 1 and cycles and not REPORT.search(text)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path, help='completed tools/build-kernel-vm.py output directory')
    parser.add_argument('--qemu', type=Path, default=shutil.which('qemu-system-x86_64'))
    parser.add_argument('--busybox', type=Path, required=True, help='statically linked x86_64 BusyBox')
    parser.add_argument('--usb-fixture', type=Path, help='optional static kernel_usb_fixture for synthetic status/disconnect checks')
    parser.add_argument('--runtime-pm', action='store_true', help='check the separate PM profile with traced suspend/resume')
    parser.add_argument('--bios', type=Path, help='optional explicit SeaBIOS image')
    parser.add_argument('--firmware-dir', type=Path, help='optional QEMU firmware/ROM search directory')
    parser.add_argument('--accel', choices=('kvm', 'tcg'), default='kvm')
    parser.add_argument('--timeout', type=int, default=120)
    args = parser.parse_args()
    if args.qemu is None or not 1 <= args.timeout <= 600:
        parser.error('QEMU and a timeout of 1..600 seconds are required.')
    if args.runtime_pm and args.usb_fixture is None:
        parser.error('--runtime-pm requires --usb-fixture.')
    directory = args.directory.resolve(strict=True)
    with (directory / 'build.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        kernel = directory / 'kernel-build'
        release = (kernel / 'include/config/kernel.release').read_text().strip()
        if not re.fullmatch(r'6\.12\.[0-9]+(?:-pm)?-octa-kasan-vm', release):
            raise ValueError('Expected the private Linux 6.12 sanitizer VM kernel.')
        config = (kernel / '.config').read_text().splitlines()
        required = REQUIRED + (PM_REQUIRED if args.runtime_pm else ())
        if any(f'CONFIG_{name}=y' not in config for name in required):
            raise ValueError('Required VM sanitizer/gadget configuration is missing.')
        if args.runtime_pm and not release.endswith('-pm-octa-kasan-vm'):
            raise ValueError('Use the separate PM build profile for runtime suspend/resume checks.')
        standalone = Path((directory / 'module-path').read_text().strip())
        modules = module_order(kernel, standalone, release)
        run = Path(tempfile.mkdtemp(prefix='check-', dir=directory))
        archive = run / 'initramfs.cpio.gz'
        fixture = args.usb_fixture.resolve(strict=True) if args.usb_fixture else None
        initramfs(archive, args.busybox.resolve(strict=True), modules, release, fixture, args.runtime_pm)
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
        if not verify_output(result.returncode, text, fixture is not None, args.runtime_pm):
            scope = 'runtime PM' if args.runtime_pm else 'module lifecycle'
            raise RuntimeError(f'VM {scope}/sanitizer check failed; inspect {log}')
        print('PASS sanitizer VM boot and three generic/standalone module load/unload cycles. No device streaming was tested.')
        if args.runtime_pm:
            print('PASS six synthetic runtime suspend/status-wake cycles across base/EXP, traced driver/PCM callbacks, 120 status replies and suspended-device teardown.')
            print('The legal gadget does not model audio streaming, CTRL MIDI or physical power management.')
        elif fixture:
            print('PASS three synthetic USB probe/concurrent-status/disconnect cycles (120 status reads). No physical device was tested.')
            print('PASS synthetic 0582:0121 expansion binding, 20 status reads and teardown. Actual EXP descriptors/streaming remain unverified.')
            print('PASS seven deterministic status errors with exact ALSA errno and untouched caller buffers. No fuzzing was run.')
            print('PASS in-flight disconnect confirmed by status URB submission/completion and ALSA error readback.')
            print('PASS 20 valid status reads after the error/disconnect scenarios and a fresh module/device lifecycle.')
            print('PASS playback/capture PCM setup errors, repeated HW_FREE/reconfiguration and status recovery after four modeled alternate stalls.')
            print('PASS one cache/task-filtered status allocation failure with ENOMEM, untouched caller bytes and same-handle recovery; kernel stack confirms octa_status_get.')
            print('PASS PCM-meter/audio-stream probe allocation failures with ENOMEM, no registered card and rebind/status recovery; kernel stacks confirm both target allocations.')


if __name__ == '__main__':
    main()
