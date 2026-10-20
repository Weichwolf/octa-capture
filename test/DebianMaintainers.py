# SPDX-License-Identifier: GPL-2.0-only
"""Run package configuration in namespaces with private sysfs and recording helpers."""

from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class Maintainers(unittest.TestCase):
    def configure(self, *, device=True, dkms_failure=False, activation_failure=False, action='configure'):
        with tempfile.TemporaryDirectory(prefix='octa package configure ') as directory:
            root = Path(directory)
            log = root / 'calls'
            binary = root / 'bin'
            binary.mkdir()
            sysfs = root / 'sys'
            sysfs.mkdir()
            if device:
                usb = sysfs / 'bus/usb/devices/3-3'
                usb.mkdir(parents=True)
                (usb / 'idVendor').write_text('0582\n')
                (usb / 'idProduct').write_text('0120\n')
            for name, status in (('common.postinst', int(dkms_failure)), ('udevadm', 0),
                                 ('systemctl', 0), ('deb-systemd-invoke', 0),
                                 ('octa-capture-activate', int(activation_failure))):
                file = binary / name
                file.write_text(f'#!/bin/sh\nprintf "%s\\n" "{name} $*" >> "$OCTA_PACKAGE_TEST_LOG"\nexit {status}\n')
                file.chmod(0o755)
            script = root / 'postinst'
            script.write_text((ROOT / 'packaging/trixie-postinst.in').read_text().replace('@VERSION@', '0.1.0'))
            command = ['bwrap', '--unshare-all', '--ro-bind', '/', '/', '--dev', '/dev', '--proc', '/proc',
                       '--tmpfs', '/run', '--dir', '/run/systemd/system', '--tmpfs', '/usr/sbin',
                       '--bind', directory, directory,
                       '--ro-bind', str(sysfs), '/sys', '--setenv', 'PATH', f'{binary}:/usr/bin:/bin',
                       '--setenv', 'OCTA_PACKAGE_TEST_LOG', str(log), '--unsetenv', 'DPKG_ROOT',
                       '--ro-bind', str(binary / 'common.postinst'), '/usr/lib/dkms/common.postinst',
                       '--ro-bind', str(binary / 'octa-capture-activate'), '/usr/sbin/octa-capture-activate',
                       '--uid', '0', '--gid', '0', '/bin/sh', str(script), action]
            result = subprocess.run(command, capture_output=True, text=True, timeout=5)
            return result, log.read_text().splitlines() if log.exists() else []

    def test_configuration_builds_driver_before_hotplug_reload_and_targeted_activation(self):
        result, calls = self.configure()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([line.split()[0] for line in calls],
                         ['common.postinst', 'udevadm', 'udevadm', 'systemctl', 'octa-capture-activate',
                          'deb-systemd-invoke', 'deb-systemd-invoke'])
        self.assertIn('snd-octa-capture 0.1.0', calls[0])

    def test_absent_device_still_configures_without_binding_anything(self):
        result, calls = self.configure(device=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn('octa-capture-activate', calls)

    def test_driver_build_failure_stops_before_any_system_or_usb_change(self):
        result, calls = self.configure(dkms_failure=True)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(len(calls), 1)

    def test_activation_failure_is_reported_and_other_maintainer_actions_are_inert(self):
        result, _calls = self.configure(activation_failure=True)
        self.assertEqual(result.returncode, 1, result.stderr)
        result, calls = self.configure(action='abort-upgrade')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(calls, [])


if __name__ == '__main__':
    unittest.main()
