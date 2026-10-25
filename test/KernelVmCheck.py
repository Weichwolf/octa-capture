# SPDX-License-Identifier: GPL-2.0-only
"""Reject incomplete sanitizer VM evidence and non-static guest executables."""

from pathlib import Path
import runpy
import struct
import tempfile
import unittest

CHECK = runpy.run_path(str(Path(__file__).resolve().parent.parent / 'tools/check-kernel-vm.py'))


def console(usb=False):
    lines = [f'OCTA_VM_MODULE_CYCLE {index}' for index in (1, 2, 3)]
    if usb:
        lines += [f'OCTA_VM_USB_CYCLE {index}' for index in (1, 2, 3)]
        lines += ['OCTA_VM_STATUS_READ_PASS'] * 7
        lines += [f'OCTA_VM_STATUS_ERROR_PASS {name}' for name in CHECK['STATUS_ERRORS']]
        lines += ['OCTA_VM_STATUS_ERROR_PASS disconnect', 'OCTA_VM_STATUS_UNPLUG_PASS',
                  'OCTA_VM_STATUS_RECOVERY_PASS', 'OCTA_VM_PCM_FAILURE_PASS']
        lines += ['OCTA_VM_PCM_ALT_STALL 1 2', 'OCTA_VM_PCM_ALT_STALL 0 2'] * 2
    return '\n'.join([*lines, CHECK['MARKER']])


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
                       'OCTA_VM_PCM_ALT_STALL 0 2', CHECK['MARKER']):
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
                       'OCTA_VM_STATUS_UNPLUG_PASS', CHECK['MARKER']):
            self.assertFalse(CHECK['verify_output'](0, text.replace(marker, marker + '0'), True))
            self.assertFalse(CHECK['verify_output'](0, text + '\n' + marker, True))

    def test_reports_and_failure_exit_override_completion_markers(self):
        text = console(True)
        self.assertFalse(CHECK['verify_output'](1, text, True))
        for report in ('BUG:', 'WARNING:', 'KASAN:', 'UBSAN:', 'Oops:',
                       'Kernel panic', 'general protection fault'):
            self.assertFalse(CHECK['verify_output'](0, text + '\n' + report, True), report)


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
