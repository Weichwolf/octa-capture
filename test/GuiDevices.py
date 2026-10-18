# SPDX-License-Identifier: GPL-2.0-only
"""Offline sysfs discovery and selected-device lifecycle checks."""

from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from gui.application import Application
from gui.backend import DeviceError, InvalidControl
from gui.devices import Device, discover


class Discovery(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.sound = self.root / 'sound'
        self.sound.mkdir()

    def card(self, index=0, card_id='OCTACAPTURE', usb_name='1-1', vendor='0582', product='0120', serial='UNIT0001'):
        card = self.sound / f'card{index}'
        card.mkdir()
        (card / 'id').write_text(card_id)
        usb = self.root / usb_name
        usb.mkdir(exist_ok=True)
        (usb / 'idVendor').write_text(vendor)
        (usb / 'idProduct').write_text(product)
        if serial is not None:
            (usb / 'serial').write_text(serial)
        interface = usb / f'{usb_name}:1.0'
        interface.mkdir(exist_ok=True)
        (card / 'device').symlink_to(interface, target_is_directory=True)
        return card

    def test_order_identity_and_all_supported_port_aliases(self):
        self.card(2, 'OCTACAPTURE_1', '2-3.1', serial='UNIT0002')
        self.card()
        devices = discover(self.sound)
        self.assertEqual([device.card_index for device in devices], [0, 2])
        self.assertEqual(devices[1].identity, 'serial:UNIT0002')
        self.assertEqual(devices[1].port, 'hw:CARD=OCTACAPTURE_1,DEV=0,SUBDEV=1')
        self.assertEqual(devices[1].name, 'OCTA-CAPTURE (UNIT0002)')
        for port in ('hw:CARD=OCTACAPTURE_1,DEV=0,SUBDEV=1', 'hw:CARD=2,DEV=0,SUBDEV=1', 'hw:2,0,1'):
            self.assertTrue(devices[1].document(port)['selected'])
            self.assertFalse(devices[0].document(port)['selected'])

    def test_other_usb_products_and_sound_entries_are_excluded(self):
        self.card(0, vendor='1234')
        self.card(1, 'OTHER', '1-2', product='0121')
        self.card(256, 'TOOMANY', '1-3')
        (self.sound / 'controlC0').mkdir()
        (self.sound / 'cardx').mkdir()
        self.assertEqual(discover(self.sound), ())

    def test_missing_serial_uses_alsa_identity(self):
        self.card(serial=None)
        self.assertEqual(discover(self.sound)[0].name, 'OCTA-CAPTURE (OCTACAPTURE)')
        self.assertEqual(discover(self.sound)[0].identity, 'usb:1-1')

    def test_invalid_or_oversized_attributes_are_skipped(self):
        card = self.card()
        for value in ('../bad', 'a,b', 'x' * 129, '', '\x00'):
            with self.subTest(card_id=value):
                (card / 'id').write_text(value)
                self.assertEqual(discover(self.sound), ())
        (card / 'id').write_bytes(b'\xff')
        self.assertEqual(discover(self.sound), ())
        (card / 'id').write_text('OCTACAPTURE')
        for value in ('bad\nserial', 'x' * 129):
            (self.root / '1-1' / 'serial').write_text(value)
            self.assertEqual(discover(self.sound), ())

    def test_hot_unplug_missing_links_and_absent_sysfs(self):
        card = self.card()
        (card / 'device').unlink()
        (card / 'device').symlink_to(self.root / 'gone')
        self.assertEqual(discover(self.sound), ())
        self.assertEqual(discover(self.root / 'absent'), ())
        (card / 'device').unlink()
        (card / 'device').symlink_to(card / 'device')
        self.assertEqual(discover(self.sound), ())


class Selection(unittest.TestCase):
    def setUp(self):
        self.application = Application('/missing/bin', '/unused', 'hw:0,0,1')
        self.old = Mock()
        self.old.auto_sense_activity.return_value = None
        self.application.bridge = self.old
        self.devices = (Device('1-1', 'OCTACAPTURE', 0, 'FIRST'),
                        Device('1-2', 'OCTACAPTURE_1', 2, 'SECOND'))

    def test_selection_closes_old_peer_and_queries_only_new_card(self):
        new = Mock()
        new.auto_sense_activity.return_value = None
        new.request.return_value = {'state': {'new': True}}
        new.meters.return_value = None
        self.application._remember({'old': True})
        self.application._status = {'old': True}
        self.application._clock_status = Mock(return_value=None)
        def create(executable, port):
            self.old.close.assert_called_once_with()
            self.assertEqual(executable, Path('/missing/bin/octa-bridge'))
            self.assertEqual(port, self.devices[1].port)
            return new
        with patch('gui.application.discover', return_value=self.devices), \
                patch('gui.application.Bridge', side_effect=create):
            result = self.application.select_device({'id': '1-2'})
        self.assertEqual(result['state'], {'new': True})
        self.assertEqual(self.application.port, self.devices[1].port)
        self.assertEqual(self.application.control_port, 'hw:CARD=OCTACAPTURE_1')
        self.assertIsNone(self.application._status)
        self.assertIs(self.application.bridge, new)
        new.request.assert_called_once_with('state')
        self.old.request.assert_not_called()

    def test_invalid_removed_or_client_supplied_ports_do_not_close_peer(self):
        with patch('gui.application.discover', return_value=self.devices):
            for body in (None, [], {}, {'id': True}, {'id': ''}, {'id': 'x' * 97},
                         {'id': '1-9'}, {'id': '1-2', 'port': 'hw:other'}, {'port': 'hw:0,0,1'}):
                with self.subTest(body=body), self.assertRaises(InvalidControl):
                    self.application.select_device(body)
            self.assertTrue(self.application.devices()['devices'][0]['selected'])
        with patch('gui.application.discover', return_value=()):
            with self.assertRaises(InvalidControl):
                self.application.select_device({'id': '1-2'})
        self.old.close.assert_not_called()
        self.assertEqual(self.application.port, 'hw:0,0,1')

    def test_active_calibration_and_shutdown_prevent_selection(self):
        self.application._auto_job = Mock()
        self.application._auto_job.require_available.side_effect = DeviceError('Auto-Sens is active.')
        with patch('gui.application.discover') as discovery:
            with self.assertRaises(DeviceError):
                self.application.select_device({'id': '1-2'})
            discovery.assert_not_called()
        self.application._auto_job = None
        self.application._closed = True
        with self.assertRaises(DeviceError):
            self.application.select_device({'id': '1-2'})
        self.old.close.assert_not_called()

    def test_open_failure_drops_old_state_and_retains_selected_card_for_retry(self):
        new = Mock()
        new.auto_sense_activity.return_value = None
        new.request.side_effect = DeviceError('Disconnected during selection.')
        self.application._remember({'old': True})
        with patch('gui.application.discover', return_value=self.devices), \
                patch('gui.application.Bridge', return_value=new):
            with self.assertRaises(DeviceError):
                self.application.select_device({'id': '1-2'})
        self.assertIsNone(self.application._cached)
        self.assertEqual(self.application.port, self.devices[1].port)
        self.old.close.assert_called_once_with()
        self.assertIs(self.application.bridge, new)

    def test_reconnect_follows_serial_when_alsa_card_identity_changes(self):
        self.application._selected_identity = 'serial:FIRST'
        replacement = Device('serial:FIRST', 'OCTACAPTURE_2', 3, 'FIRST')
        new = Mock()
        new.auto_sense_activity.return_value = None
        new.request.return_value = {'state': {'reconnected': True}}
        new.meters.return_value = None
        self.application._clock_status = Mock(return_value=None)
        with patch('gui.application.discover', return_value=(replacement,)), \
                patch('gui.application.Bridge', return_value=new):
            self.assertEqual(self.application.snapshot()['state'], {'reconnected': True})
            self.assertTrue(self.application.devices()['devices'][0]['selected'])
        self.assertEqual(self.application.port, replacement.port)
        self.old.close.assert_called_once_with()

    def test_absent_selected_serial_never_opens_another_unit_at_the_old_card_index(self):
        self.application._selected_identity = 'serial:GONE'
        other = Device('serial:OTHER', 'OCTACAPTURE', 0, 'OTHER')
        with patch('gui.application.discover', return_value=(other,)):
            with self.assertRaises(DeviceError):
                self.application.snapshot()
            self.assertFalse(self.application.devices()['devices'][0]['selected'])
        self.old.request.assert_not_called()
        self.old.close.assert_called_once_with()

    def test_rate_action_recomputes_alsa_arguments_after_card_renumbering(self):
        self.application._selected_identity = 'serial:FIRST'
        replacement = Device('serial:FIRST', 'OCTACAPTURE_2', 3, 'FIRST')
        with patch('gui.application.discover', return_value=(replacement,)), \
                patch('gui.application.Bridge'), \
                patch.object(self.application, '_directory', return_value=Path('/unused')), \
                patch.object(self.application, 'snapshot', return_value={'restored': True}), \
                patch.object(self.application, '_run') as run:
            self.assertEqual(self.application.action({'action': 'sample-rate', 'rate': 48000}), {'restored': True})
        run.assert_called_once_with(['/missing/bin/octa-audio-check', '48000', 'prepare-capture',
                                     'hw:CARD=OCTACAPTURE_2,DEV=0'])

    def test_all_live_operations_check_selected_identity_before_io_or_backup_creation(self):
        self.application._selected_identity = 'serial:GONE'
        operations = (
            lambda: self.application.change({'kind': 'preamp', 'channel': 0, 'parameter': 'gain', 'value': 1}),
            self.application.export_preset,
            lambda: self.application.import_preset(bytes(1639)),
            lambda: self.application.action({'action': 'initialize', 'target': 'reverb'}),
            lambda: self.application.action({'action': 'sample-rate', 'rate': 48000}),
            lambda: self.application.start_auto_sense({'channels': 1}),
        )
        with patch('gui.application.discover', return_value=()), \
                patch.object(self.application, '_directory') as directory, \
                patch.object(self.application, '_run') as run:
            for operation in operations:
                with self.subTest(operation=operation), self.assertRaises(DeviceError):
                    operation()
            directory.assert_not_called()
            run.assert_not_called()
        self.old.request.assert_not_called()


if __name__ == '__main__':
    unittest.main()
