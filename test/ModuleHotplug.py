# SPDX-License-Identifier: GPL-2.0-only
"""Evaluate actual udev rules with umockdev in read-only host/user namespaces.

Requires bubblewrap, umockdev and udevadm. No host device nodes are available.
"""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent


class Hotplug(unittest.TestCase):
    def evaluate(self, vendor='0582', product='0120', driver='snd-usb-audio', node='controlC0', action='add'):
        with tempfile.TemporaryDirectory(prefix='octa hotplug ') as temporary:
            root = Path(temporary)
            rules = root / 'rules'
            rules.mkdir()
            shutil.copyfile(ROOT / 'packaging/70-octa-capture.rules', rules / '70-octa-capture.rules')
            shutil.copyfile(ROOT / 'packaging/89-octa-capture-audio.rules', rules / '89-octa-capture-audio.rules')
            device = '/devices/platform/usb-test/usb1/1-1'
            interface = device + '/1-1:1.0'
            card = interface + '/sound/card0'
            syspath = '/sys' + (card + '/' + node if node else card)
            fixture = root / 'sound.umockdev'
            fixture.write_text(
                (f'P: {card}/{node}\nN: snd/{node}\nE: SUBSYSTEM=sound\n'
                 f'E: DEVNAME=/dev/snd/{node}\nE: MAJOR=116\nE: MINOR=5\n\n' if node else '') +
                f'P: {card}\nE: SUBSYSTEM=sound\n\n'
                f'P: {interface}\nE: SUBSYSTEM=usb\nE: DEVTYPE=usb_interface\nE: DRIVER={driver}\n'
                f'L: driver=../../../../../../bus/usb/drivers/{driver}\n\n'
                f'P: {device}\nE: SUBSYSTEM=usb\nE: DEVTYPE=usb_device\n'
                f'A: idVendor={vendor}\nA: idProduct={product}\n')
            command = ['bwrap', '--unshare-all', '--ro-bind', '/', '/', '--dev', '/dev', '--proc', '/proc',
                       '--tmpfs', '/tmp', '--tmpfs', '/run', '--ro-bind', str(root), str(root),
                       '--ro-bind', str(rules), '/usr/lib/udev/rules.d', '--tmpfs', '/etc/udev/rules.d',
                       '--uid', '0', '--gid', '0', 'umockdev-run', '--device', str(fixture), '--',
                       'udevadm', 'test', '--resolve-names=never', '--action=' + action, syspath]
            result = subprocess.run(command, capture_output=True, text=True, timeout=5)
            self.assertEqual(result.returncode, 0, result.stderr)
            return dict(line.strip().split('=', 1) for line in result.stdout.splitlines() if '=' in line)

    def test_generic_octa_requests_a_serialized_activation_service(self):
        properties = self.evaluate()
        self.assertEqual(properties.get('SYSTEMD_WANTS'), 'octa-capture-activate@controlC0.service')
        self.assertIn('systemd', properties.get('TAGS', ''))

    def test_only_octa_cards_defer_pcm_opening_until_the_device_clock_is_read(self):
        for driver in ('snd-usb-audio', 'snd-octa-capture'):
            properties = self.evaluate(node=None, driver=driver)
            self.assertEqual(properties.get('PULSE_PROFILE_SET'), 'octa-capture-pending.conf')
            self.assertEqual(properties.get('PULSE_MODARGS'), 'use_ucm=no profile=off')
        for parameters in ({'node': None, 'vendor': '1234'}, {'node': None, 'product': 'ffff'},
                           {'node': None, 'action': 'remove'}, {'node': 'controlC0'}):
            self.assertNotIn('PULSE_MODARGS', self.evaluate(**parameters))

    def test_dedicated_and_unrelated_devices_never_request_rebinding(self):
        for parameters in ({'driver': 'snd-octa-capture'}, {'driver': 'unrelated-driver'},
                           {'vendor': '1234'}, {'product': 'ffff'},
                           {'node': 'pcmC0D0c'}, {'action': 'remove'}):
            with self.subTest(parameters=parameters):
                self.assertNotIn('SYSTEMD_WANTS', self.evaluate(**parameters))


if __name__ == '__main__':
    unittest.main()
