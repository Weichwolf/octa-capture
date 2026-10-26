# SPDX-License-Identifier: GPL-2.0-only
"""Reject incomplete sanitizer VM evidence and non-static guest executables."""

from pathlib import Path
import runpy
import struct
import subprocess
import sys
import tempfile
import unittest

CHECK = runpy.run_path(str(Path(__file__).resolve().parent.parent / 'tools/check-kernel-vm.py'))
SCRIPT = Path(__file__).resolve().parent.parent / 'tools/check-kernel-vm.py'


def console(usb=False):
    lines = [f'OCTA_VM_MODULE_CYCLE {index}' for index in (1, 2, 3)]
    if usb:
        lines += [f'OCTA_VM_USB_CYCLE {index}' for index in (1, 2, 3)]
        lines += ['OCTA_VM_STATUS_READ_PASS'] * 10
        lines += ['OCTA_VM_USB_EXPANSION_PASS']
        lines += ['FAULT_INJECTION: forcing a failure.', ' octa_status_get+0x10/0x20 [snd_octa_capture]',
                  'OCTA_VM_STATUS_ALLOCATION_PASS']
        lines += [f'OCTA_VM_STATUS_ERROR_PASS {name}' for name in CHECK['STATUS_ERRORS']]
        lines += ['OCTA_VM_STATUS_ERROR_PASS disconnect', 'OCTA_VM_STATUS_UNPLUG_PASS',
                  'OCTA_VM_STATUS_RECOVERY_PASS', 'OCTA_VM_PCM_FAILURE_PASS']
        lines += ['OCTA_VM_PCM_ALT_STALL 1 2', 'OCTA_VM_PCM_ALT_STALL 0 2'] * 2
        for target, function in (('pcm-meter', 'snd_usb_octa_capture_add_pcm_controls'),
                                 ('audio-stream', 'snd_usb_add_audio_stream')):
            lines += ['FAULT_INJECTION: forcing a failure.', f' {function}+0x10/0x20 [snd_octa_capture]',
                      f'OCTA_VM_PROBE_ALLOCATION_PASS {target}']
    return '\n'.join([*lines, CHECK['MARKER']])


def pm_console():
    lines = console().splitlines()[:-1]
    for mode in ('normal', 'expansion'):
        lines += [f'OCTA_VM_RUNTIME_PM_FILTER_BEGIN {mode}']
        lines += [f'{name} [snd_octa_capture]' for name in CHECK['PM_CALLBACKS']]
        lines += [f'OCTA_VM_RUNTIME_PM_FILTER_END {mode}']
        for cycle in (1, 2, 3):
            case = f'{mode} {cycle}'
            lines += [f'OCTA_VM_RUNTIME_PM_CYCLE_BEGIN {case}', f'OCTA_VM_RUNTIME_PM_SUSPENDED {case}',
                      'OCTA_VM_STATUS_READ_PASS', f'OCTA_VM_RUNTIME_PM_ACTIVE {case}',
                      f'OCTA_VM_RUNTIME_PM_TRACE_BEGIN {case}']
            lines += [f' worker-12 [000] .... 123.456789: {name} <-caller' for name in CHECK['PM_CALLBACKS']]
            lines += [f'OCTA_VM_RUNTIME_PM_TRACE_END {case}', f'OCTA_VM_RUNTIME_PM_CYCLE {case}']
        lines += [f'OCTA_VM_RUNTIME_PM_UNIT_PASS {mode}']
    return '\n'.join([*lines, CHECK['MARKER']])


class PowerEvidence(unittest.TestCase):
    def test_six_ordered_cycles_need_trace_filters_callbacks_and_status_wakeups(self):
        text = pm_console()
        self.assertTrue(CHECK['verify_output'](0, text, True, True))
        self.assertFalse(CHECK['verify_output'](0, text, False, True))
        self.assertFalse(CHECK['verify_output'](0, console(True), True, True))
        self.assertFalse(CHECK['verify_output'](1, text, True, True))

    def test_missing_duplicate_or_fragmented_markers_cannot_pass(self):
        text = pm_console()
        markers = [line for line in text.splitlines() if line.startswith('OCTA_VM_')]
        for marker in markers:
            for changed in (text.replace(marker, '', 1), text + '\n' + marker,
                            text.replace(marker, marker + 'x', 1)):
                self.assertFalse(CHECK['verify_output'](0, changed, True, True), marker)

    def test_callbacks_require_timestamped_module_specific_evidence_in_every_cycle(self):
        text = pm_console()
        for name in CHECK['PM_CALLBACKS']:
            frame = f' worker-12 [000] .... 123.456789: {name} <-caller'
            for changed in (text.replace(frame, '', 1), text.replace(frame, name, 1),
                            text.replace(f'{name} [snd_octa_capture]', f'{name} [snd_usb_audio]', 1)):
                self.assertFalse(CHECK['verify_output'](0, changed, True, True), name)
        self.assertFalse(CHECK['verify_output'](0, text + '\nFAULT_INJECTION: forcing a failure.', True, True))
        self.assertFalse(CHECK['verify_output'](0, text + '\nOCTA_VM_RUNTIME_PM_SUSPEND_TIMEOUT', True, True))

    def test_read_and_callback_order_are_part_of_the_evidence(self):
        text = pm_console()
        shifted = text.replace('OCTA_VM_STATUS_READ_PASS\nOCTA_VM_RUNTIME_PM_ACTIVE normal 1',
                               'OCTA_VM_RUNTIME_PM_ACTIVE normal 1\nOCTA_VM_STATUS_READ_PASS', 1)
        self.assertFalse(CHECK['verify_output'](0, shifted, True, True))
        before = '123.456789: usb_audio_suspend'
        after = '123.456789: usb_audio_resume'
        swapped = text.replace(before, '123.456789: temporary', 1).replace(after, before, 1)
        swapped = swapped.replace('123.456789: temporary', after, 1)
        self.assertFalse(CHECK['verify_output'](0, swapped, True, True))


class PowerGuards(unittest.TestCase):
    def command(self, directory):
        return [sys.executable, str(SCRIPT), str(directory), '--qemu', sys.executable,
                '--busybox', sys.executable, '--runtime-pm']

    def test_runtime_checks_require_the_guest_usb_fixture_before_creating_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = subprocess.run(self.command(root), capture_output=True, text=True, timeout=3)
            self.assertEqual(result.returncode, 2)
            self.assertIn('--runtime-pm requires --usb-fixture', result.stderr)
            self.assertEqual(list(root.iterdir()), [])

    def test_minimal_profile_cannot_be_used_as_power_management_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            kernel = root / 'kernel-build'
            (kernel / 'include/config').mkdir(parents=True)
            (kernel / 'include/config/kernel.release').write_text('6.12.113-octa-kasan-vm\n')
            (kernel / '.config').write_text('\n'.join('CONFIG_' + name + '=y'
                                                      for name in (*CHECK['REQUIRED'], *CHECK['PM_REQUIRED'])))
            result = subprocess.run([*self.command(root), '--usb-fixture', sys.executable],
                                    capture_output=True, text=True, timeout=3)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('separate PM build profile', result.stderr)
            self.assertEqual(sorted(path.name for path in root.iterdir()), ['build.lock', 'kernel-build'])


class Evidence(unittest.TestCase):
    def test_complete_lifecycles_are_accepted(self):
        for usb in (False, True):
            self.assertTrue(CHECK['verify_output'](0, console(usb), usb))

    def test_missing_or_extra_usb_evidence_cannot_pass(self):
        self.assertFalse(CHECK['verify_output'](0, console(), True))
        text = console(True)
        for marker in ('OCTA_VM_MODULE_CYCLE 2', 'OCTA_VM_USB_CYCLE 2',
                       'OCTA_VM_STATUS_READ_PASS', 'OCTA_VM_STATUS_ERROR_PASS disconnect',
                       'OCTA_VM_STATUS_UNPLUG_PASS', 'OCTA_VM_STATUS_RECOVERY_PASS',
                       'OCTA_VM_PCM_FAILURE_PASS', 'OCTA_VM_PCM_ALT_STALL 1 2',
                       'OCTA_VM_PCM_ALT_STALL 0 2', 'OCTA_VM_USB_EXPANSION_PASS',
                       'OCTA_VM_STATUS_ALLOCATION_PASS', 'OCTA_VM_PROBE_ALLOCATION_PASS pcm-meter',
                       'OCTA_VM_PROBE_ALLOCATION_PASS audio-stream', CHECK['MARKER']):
            self.assertFalse(CHECK['verify_output'](0, text.replace(marker, '', 1), True), marker)
        self.assertFalse(CHECK['verify_output'](0, text + '\nOCTA_VM_STATUS_READ_PASS', True))
        self.assertFalse(CHECK['verify_output'](0, text + '\nOCTA_VM_PCM_ALT_STALL 1 2', True))
        self.assertFalse(CHECK['verify_output'](0, text.replace('OCTA_VM_PCM_ALT_STALL 1 2',
                                                              'OCTA_VM_PCM_ALT_STALL 1 3'), True))
        for name in CHECK['STATUS_ERRORS']:
            marker = f'OCTA_VM_STATUS_ERROR_PASS {name}'
            self.assertFalse(CHECK['verify_output'](0, text.replace(marker, '', 1), True), name)
            self.assertFalse(CHECK['verify_output'](0, text + '\n' + marker, True), name)

    def test_marker_fragments_and_duplicate_cycles_are_rejected(self):
        text = console(True)
        for marker in ('OCTA_VM_MODULE_CYCLE 2', 'OCTA_VM_USB_CYCLE 2',
                       'OCTA_VM_STATUS_UNPLUG_PASS', 'OCTA_VM_USB_EXPANSION_PASS', CHECK['MARKER']):
            self.assertFalse(CHECK['verify_output'](0, text.replace(marker, marker + '0'), True))
            self.assertFalse(CHECK['verify_output'](0, text + '\n' + marker, True))

    def test_allocation_evidence_requires_one_fault_in_the_status_callback(self):
        text = console(True)
        for fragment in ('FAULT_INJECTION: forcing a failure.',
                         ' octa_status_get+0x10/0x20 [snd_octa_capture]'):
            self.assertFalse(CHECK['verify_output'](0, text.replace(fragment, '', 1), True), fragment)
        self.assertFalse(CHECK['verify_output'](0, text + '\nFAULT_INJECTION: forcing a failure.', True))
        self.assertFalse(CHECK['verify_output'](0, text + '\nOCTA_VM_STATUS_ALLOCATION_PASS', True))
        self.assertFalse(CHECK['verify_output'](0, text.replace('octa_status_get+', 'unrelated_alloc+'), True))
        self.assertFalse(CHECK['verify_output'](0, text.replace(' octa_status_get+', ' ? octa_status_get+'), True))
        self.assertFalse(CHECK['verify_output'](0, text.replace('[snd_octa_capture]', '[snd_usb_audio]'), True))
        self.assertFalse(CHECK['verify_output'](0, text.replace(
            'OCTA_VM_STATUS_ALLOCATION_PASS', '').replace('FAULT_INJECTION:',
            'OCTA_VM_STATUS_ALLOCATION_PASS\nFAULT_INJECTION:'), True))

    def test_reports_and_failure_exit_override_completion_markers(self):
        text = console(True)
        self.assertFalse(CHECK['verify_output'](1, text, True))
        for report in ('BUG:', 'WARNING:', 'KASAN:', 'UBSAN:', 'Oops:',
                       'Kernel panic', 'general protection fault', 'OCTA_VM_MODULE_LIFECYCLE_FAIL'):
            self.assertFalse(CHECK['verify_output'](0, text + '\n' + report, True), report)


    def test_probe_failures_require_their_own_confirmed_allocation_stacks(self):
        text = console(True)
        for target, function in (('pcm-meter', 'snd_usb_octa_capture_add_pcm_controls'),
                                 ('audio-stream', 'snd_usb_add_audio_stream')):
            marker = f'OCTA_VM_PROBE_ALLOCATION_PASS {target}'
            frame = f' {function}+0x10/0x20 [snd_octa_capture]'
            for replacement in ('', frame.replace(' ', ' ? ', 1), frame.replace(function, 'unrelated')):
                changed = text.replace(frame, replacement)
                self.assertFalse(CHECK['verify_output'](0, changed, True), (target, replacement))
            self.assertFalse(CHECK['verify_output'](0, text + '\n' + marker, True))
            moved = text.replace(frame, '').replace(marker, marker + '\n' + frame)
            self.assertFalse(CHECK['verify_output'](0, moved, True), target)
        swapped = text.replace('PASS pcm-meter', 'PASS temporary').replace('PASS audio-stream', 'PASS pcm-meter')
        swapped = swapped.replace('PASS temporary', 'PASS audio-stream')
        self.assertFalse(CHECK['verify_output'](0, swapped, True))


class Executable(unittest.TestCase):
    def image(self):
        data = bytearray(120)
        data[:6] = b'\x7fELF\x02\x01'
        struct.pack_into('<H', data, 18, 62)
        struct.pack_into('<Q', data, 32, 64)
        struct.pack_into('<HH', data, 54, 56, 1)
        struct.pack_into('<I', data, 64, 1)
        return data

    def read(self, data):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'guest'
            path.write_bytes(data)
            return CHECK['static_executable'](path)

    def test_static_x86_64_image(self):
        data = self.image()
        self.assertEqual(self.read(data), data)

    def test_dynamic_interpreter_is_rejected(self):
        data = self.image()
        struct.pack_into('<I', data, 64, 3)
        with self.assertRaisesRegex(ValueError, 'statically linked'):
            self.read(data)

    def test_invalid_architecture_or_header_bounds(self):
        for offset, form, value in ((4, 'B', 1), (5, 'B', 2), (18, 'H', 183),
                                    (32, 'Q', 121), (54, 'H', 55), (56, 'H', 0)):
            data = self.image()
            struct.pack_into('<' + form, data, offset, value)
            with self.subTest(offset=offset), self.assertRaises(ValueError):
                self.read(data)
        with self.assertRaises(ValueError):
            self.read(self.image()[:-1])


if __name__ == '__main__':
    unittest.main()
