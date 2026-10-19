# SPDX-License-Identifier: GPL-2.0-only
"""Offline policy-lease checks; no USB, sudo or sysfs writes."""

import io
import json
import os
from pathlib import Path
import runpy
import sys
import tempfile
import unittest
from unittest.mock import patch

MODULE = runpy.run_path(str(Path(__file__).resolve().parent.parent / 'tools/runtime_pm.py'))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'tools'))
CHECK = runpy.run_path(str(Path(__file__).resolve().parent.parent / 'tools/check-runtime-pm.py'))
GLOBALS = MODULE['temporary_policy'].__wrapped__.__globals__


class Policy(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.device = self.root / '3-3'
        self.device.mkdir()
        for name, value in {'idVendor': '0582', 'idProduct': '0120', 'busnum': '3', 'devnum': '4',
                            'power/control': 'on', 'power/autosuspend_delay_ms': '2000'}.items():
            path = self.device / name
            path.parent.mkdir(exist_ok=True)
            path.write_text(value)
        interface = self.root / '3-3:1.0'
        interface.mkdir()
        driver = self.root / 'drivers/snd-octa-capture'
        driver.mkdir(parents=True)
        (interface / 'driver').symlink_to(driver)
        self.write_attribute = GLOBALS['write_attribute']
        self.writes = []
        def fake_sysfs_write(descriptor, value):
            self.writes.append((Path(os.readlink(f'/proc/self/fd/{descriptor}')).name, value))
            self.write_attribute(descriptor, value)
            # Ordinary fixture files need truncation; real sysfs store callbacks replace the attribute.
            os.ftruncate(descriptor, os.lseek(descriptor, 0, os.SEEK_CUR))
        self.fake_write = fake_sysfs_write
        writer = patch.dict(GLOBALS, write_attribute=fake_sysfs_write)
        writer.start()
        self.addCleanup(writer.stop)

    def assert_restored(self, control='on', delay='2000'):
        self.assertEqual(MODULE['read'](self.device, 'power/control'), control)
        self.assertEqual(MODULE['read'](self.device, 'power/autosuspend_delay_ms'), delay)

    def test_one_device_and_dedicated_binding(self):
        self.assertEqual(MODULE['find_device'](self.root), self.device)
        (self.root / '3-3:1.0/driver').unlink()
        with self.assertRaisesRegex(RuntimeError, 'only snd-octa-capture'):
            MODULE['find_device'](self.root)

    def test_foreign_and_ambiguous_devices_rejected(self):
        (self.device / 'idProduct').write_text('0121')
        with self.assertRaisesRegex(RuntimeError, 'found 0'):
            MODULE['find_device'](self.root)
        (self.device / 'idProduct').write_text('0120')
        second = self.root / '3-4'
        second.mkdir()
        (second / 'idVendor').write_text('0582')
        (second / 'idProduct').write_text('0120')
        with self.assertRaisesRegex(RuntimeError, 'found 2'):
            MODULE['find_device'](self.root)
        self.assert_restored()

    def test_policy_restored_on_success_error_and_signal(self):
        for error in (None, RuntimeError('probe failed'), KeyboardInterrupt()):
            with self.subTest(error=type(error).__name__):
                def use():
                    with MODULE['temporary_policy'](self.device) as original:
                        self.assertEqual(original, {'device': '3-3', 'control': 'on', 'autosuspend_delay_ms': 2000})
                        self.assert_restored('auto', '0')
                        if error is not None:
                            raise error
                if error is None:
                    use()
                else:
                    with self.assertRaises(type(error)):
                        use()
                self.assert_restored()

    def test_original_auto_and_negative_delay_retained(self):
        (self.device / 'power/control').write_text('auto')
        (self.device / 'power/autosuspend_delay_ms').write_text('-1')
        with MODULE['temporary_policy'](self.device):
            self.assert_restored('auto', '0')
        self.assert_restored('auto', '-1')

    def test_partial_activation_failure_rolls_back(self):
        def fail_once(descriptor, data):
            if Path(os.readlink(f'/proc/self/fd/{descriptor}')).name == 'control' and data == 'auto':
                raise PermissionError('simulated policy refusal')
            return self.fake_write(descriptor, data)
        with patch.dict(GLOBALS, write_attribute=fail_once):
            with self.assertRaisesRegex(PermissionError, 'simulated policy refusal'):
                with MODULE['temporary_policy'](self.device):
                    self.fail('Failed activation must not yield.')
        self.assert_restored()

    def test_replaced_device_is_never_reconfigured(self):
        with self.assertRaisesRegex(RuntimeError, 'replacement device'):
            with MODULE['temporary_policy'](self.device):
                (self.device / 'devnum').write_text('5')
        self.assert_restored('auto', '0')  # Replacement attributes were not touched.

    def test_activation_descriptors_cannot_follow_a_replacement_path(self):
        replaced = False
        def replace_after_first_write(descriptor, value):
            nonlocal replaced
            self.fake_write(descriptor, value)
            if not replaced:
                replaced = True
                self.device.rename(self.root / 'retired')
                (self.device / 'power').mkdir(parents=True)
                for name, data in {'busnum': '3', 'devnum': '5', 'power/control': 'on',
                                   'power/autosuspend_delay_ms': '2000'}.items():
                    (self.device / name).write_text(data)
        with patch.dict(GLOBALS, write_attribute=replace_after_first_write):
            with self.assertRaisesRegex(RuntimeError, 'replacement device'):
                with MODULE['temporary_policy'](self.device):
                    pass
        self.assert_restored()  # Activation wrote only pinned attributes on the retired device.

    def test_policy_attribute_symlinks_rejected_before_mutation(self):
        control = self.device / 'power/control'
        control.unlink()
        target = self.root / 'unrelated'
        target.write_text('on')
        control.symlink_to(target)
        with self.assertRaises(OSError):
            with MODULE['temporary_policy'](self.device):
                self.fail('A symlink attribute must not yield.')
        self.assertEqual(self.writes, [])
        self.assert_restored()

    def test_bad_original_policy_rejected_before_writes(self):
        (self.device / 'power/control').write_text('other')
        with self.assertRaisesRegex(RuntimeError, 'Unexpected original'):
            with MODULE['temporary_policy'](self.device):
                self.fail('Invalid policy must not yield.')
        self.assert_restored('other')

    def test_restoration_attempts_every_attribute_after_error(self):
        def fail_delay(descriptor, data):
            if data == '2000':
                raise PermissionError('simulated restore refusal')
            return self.fake_write(descriptor, data)
        with patch.dict(GLOBALS, write_attribute=fail_delay):
            with self.assertRaisesRegex(RuntimeError, 'restoration failed'):
                with MODULE['temporary_policy'](self.device):
                    pass
        self.assertEqual(self.writes[-1], ('control', 'on'))
        self.assert_restored('on', '0')

    def test_broken_client_output_still_restores_policy(self):
        reader, writer = os.pipe()
        os.close(writer)
        try:
            output = io.StringIO()
            with patch.object(output, 'write', side_effect=BrokenPipeError):
                with self.assertRaises(BrokenPipeError):
                    MODULE['lease'](self.device, reader, output)
        finally:
            os.close(reader)
        self.assert_restored()

    def test_root_and_argument_guards_precede_mutation(self):
        for uid, arguments in ((1000, ['runtime-pm']), (0, ['runtime-pm', '/different/device'])):
            with self.subTest(uid=uid, arguments=arguments):
                with patch.object(MODULE['os'], 'geteuid', return_value=uid), patch.object(MODULE['sys'], 'argv', arguments):
                    with self.assertRaisesRegex(RuntimeError, 'no device/path arguments'):
                        MODULE['main']()
                self.assert_restored()

    def test_eof_and_timeout_restore_policy(self):
        for timeout in (False, True):
            with self.subTest(timeout=timeout):
                reader, writer = os.pipe()
                os.close(writer)
                try:
                    output = io.StringIO()
                    if timeout:
                        with patch.object(MODULE['select'], 'select', return_value=([], [], [])) as select_ready:
                            MODULE['lease'](self.device, reader, output)
                            select_ready.assert_called_once_with([reader], [], [], 120)
                    else:
                        MODULE['lease'](self.device, reader, output)
                    ready, restored = map(json.loads, output.getvalue().splitlines())
                    self.assertIs(ready['ready'], True)
                    self.assertEqual(ready['lease_seconds'], 120)
                    self.assertEqual(restored, {'restored': True, 'reason': 'timeout' if timeout else 'closed'})
                finally:
                    os.close(reader)
                self.assert_restored()

    def test_suspend_observation_requires_consecutive_samples(self):
        (self.device / 'power/runtime_status').write_text('suspended')
        (self.device / 'power/runtime_active_time').write_text('123')
        with patch.object(CHECK['time'], 'sleep') as sleep:
            self.assertEqual(CHECK['wait_suspended'](self.device), 123)
            sleep.assert_called_once_with(0.02)

    def test_busy_device_reports_competing_clients(self):
        (self.device / 'power/runtime_status').write_text('active')
        (self.device / 'power/runtime_usage').write_text('1')
        with patch.object(CHECK['time'], 'monotonic', side_effect=(0, 0, 9)), patch.object(CHECK['time'], 'sleep'):
            with self.assertRaisesRegex(RuntimeError, 'status=active, usage=1; close competing'):
                CHECK['wait_suspended'](self.device)
        self.assert_restored()

    def test_durable_recovery_has_valid_framing_and_checksum(self):
        payload = bytes(1146)
        path = self.root / 'recovery.syx'
        CHECK['snapshot'](path, {'payload_hex': payload.hex()})
        frame = path.read_bytes()
        self.assertEqual(frame[:11], bytes.fromhex('f0411000004d1201000000'))
        self.assertEqual(frame[11:-2], payload)
        self.assertEqual(sum(frame[7:-1]) % 128, 0)
        self.assertEqual(frame[-1], 247)
        with self.assertRaises(FileExistsError):
            CHECK['snapshot'](path, {'payload_hex': payload.hex()})

    def test_rate_expectation_preserves_all_other_raw_and_decoded_values(self):
        before = {'payload_hex': bytes([9, 8, 1, 7, 6]).hex(), 'nested': {'values': [1, 2]}}
        for index, rate in enumerate((44100, 48000, 96000, 192000)):
            expected = CHECK['rate_state'](before, rate)
            self.assertEqual(expected['payload_hex'], bytes([9, 8, index, 7, 6]).hex())
            self.assertEqual(expected['nested'], before['nested'])
            expected['nested']['values'][0] = 99
            self.assertEqual(before['nested']['values'][0], 1)

    def test_backend_recovery_refuses_replacement_before_running_commands(self):
        original = MODULE['identity'](self.device)
        (self.device / 'devnum').write_text('5')
        class Backend:
            def run(self, *_arguments):
                raise AssertionError('No backend command may run against the replacement.')
        with self.assertRaisesRegex(RuntimeError, 'refusing restoration to a replacement'):
            CHECK['restore'](Backend(), self.device, {}, {}, {}, self.root / 'snapshot.syx', original)
        self.assert_restored()


if __name__ == '__main__':
    unittest.main()
