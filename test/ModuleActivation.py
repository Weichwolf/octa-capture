# SPDX-License-Identifier: GPL-2.0-only
"""Exercise root activation against fake sysfs in an isolated user/mount namespace.

Requires bubblewrap and unprivileged user namespaces. The host root is read-only;
/sys, /dev and networking are replaced, and modprobe is a recording stub.
"""

from pathlib import Path
import shlex
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent


class Activation(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='octa activation ')
        self.addCleanup(self.temporary.cleanup)
        self.fixture = Path(self.temporary.name)
        self.sysfs = self.fixture / 'sys'
        self.devices = self.sysfs / 'bus/usb/devices'
        self.drivers = self.sysfs / 'bus/usb/drivers'
        self.devices.mkdir(parents=True)
        for name in ('snd-usb-audio', 'snd-octa-capture', 'unexpected'):
            driver = self.drivers / name
            driver.mkdir(parents=True)
            (driver / 'unbind').write_text('')
            (driver / 'bind').write_text('')
        (self.fixture / 'bin').mkdir()
        loader = self.fixture / 'bin/modprobe'
        loader.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> ' +
                          shlex.quote(str(self.fixture / 'modprobe.log')) + '\n')
        loader.chmod(0o755)
        self.command = ['bwrap', '--unshare-all', '--ro-bind', '/', '/', '--dev', '/dev', '--proc', '/proc',
                        '--bind', str(self.sysfs), '/sys', '--bind', str(self.fixture), str(self.fixture),
                        '--setenv', 'PATH', str(self.fixture / 'bin') + ':/usr/bin:/bin', '--uid', '0', '--gid', '0',
                        '/bin/bash', str(ROOT / 'tools/activate-module.sh')]

    def device(self, name='1-1', vendor='0582', product='0120', owner='snd-usb-audio', count=4):
        device = self.devices / name
        device.mkdir()
        (device / 'idVendor').write_text(vendor)
        (device / 'idProduct').write_text(product)
        interfaces = []
        for index in range(count):
            interface = self.devices / f'{name}:1.{index}'
            interface.mkdir()
            if owner:
                (interface / 'driver').symlink_to(f'/sys/bus/usb/drivers/{owner}')
            interfaces.append(interface)
        return interfaces

    def invoke(self, *arguments):
        return subprocess.run([*self.command, *arguments], capture_output=True, text=True, timeout=5)

    def no_driver_writes(self):
        for name in ('snd-usb-audio', 'snd-octa-capture', 'unexpected'):
            for attribute in ('bind', 'unbind'):
                self.assertEqual((self.drivers / name / attribute).read_text(), '')

    def test_generic_device_is_rebound_and_other_usb_devices_are_ignored(self):
        self.device()
        self.device('2-9', '1234', '5678', owner='unexpected')
        result = self.invoke()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.fixture / 'modprobe.log').read_text(), 'snd-octa-capture\n')
        self.assertEqual((self.drivers / 'snd-octa-capture/bind').read_text(), '1-1:1.0')
        self.assertEqual((self.drivers / 'snd-usb-audio/unbind').read_text(), '1-1:1.3')
        self.assertEqual((self.drivers / 'unexpected/unbind').read_text(), '')

    def test_check_is_read_only_for_both_owners(self):
        interfaces = self.device()
        self.assertEqual(self.invoke('--check').returncode, 1)
        self.no_driver_writes()
        for interface in interfaces:
            (interface / 'driver').unlink()
            (interface / 'driver').symlink_to('/sys/bus/usb/drivers/snd-octa-capture')
        self.assertEqual(self.invoke('--check').returncode, 0)
        self.no_driver_writes()
        self.assertFalse((self.fixture / 'modprobe.log').exists())

    def test_active_device_does_not_get_unbound(self):
        self.device(owner='snd-octa-capture')
        self.assertEqual(self.invoke().returncode, 0)
        self.no_driver_writes()

    def test_unclaimed_device_binds_without_unbind(self):
        self.device(owner=None)
        self.assertEqual(self.invoke().returncode, 0)
        self.assertEqual((self.drivers / 'snd-octa-capture/bind').read_text(), '1-1:1.0')
        self.assertEqual((self.drivers / 'snd-usb-audio/unbind').read_text(), '')

    def test_unknown_and_mixed_owners_are_refused_before_driver_writes(self):
        interfaces = self.device(owner='unexpected')
        self.assertEqual(self.invoke().returncode, 1)
        self.no_driver_writes()
        for index, interface in enumerate(interfaces):
            (interface / 'driver').unlink()
            (interface / 'driver').symlink_to('/sys/bus/usb/drivers/' + ('snd-usb-audio' if index else 'snd-octa-capture'))
        self.assertEqual(self.invoke().returncode, 1)
        self.no_driver_writes()

    def test_failed_binding_attempts_to_restore_only_a_previous_generic_owner(self):
        self.device()
        binding = self.drivers / 'snd-octa-capture/bind'
        binding.unlink()
        binding.symlink_to('/dev/full')
        result = self.invoke()
        self.assertEqual(result.returncode, 1)
        self.assertIn('Restored the previous generic driver binding.', result.stderr)
        self.assertEqual((self.drivers / 'snd-usb-audio/bind').read_text(), '1-1:1.0')
        self.assertEqual((self.fixture / 'modprobe.log').read_text(), 'snd-octa-capture\nsnd-usb-audio\n')

    def test_failed_module_load_cannot_unbind_the_device(self):
        self.device()
        (self.fixture / 'bin/modprobe').write_text('#!/bin/sh\nexit 1\n')
        self.assertEqual(self.invoke().returncode, 1)
        self.no_driver_writes()

    def test_absent_device_or_interfaces_and_bad_arguments_fail_without_writes(self):
        self.assertEqual(self.invoke().returncode, 1)
        self.no_driver_writes()
        self.device(count=0)
        self.assertEqual(self.invoke().returncode, 1)
        self.assertEqual(self.invoke('--invalid').returncode, 2)
        self.no_driver_writes()


if __name__ == '__main__':
    unittest.main()
