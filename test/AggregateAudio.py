# SPDX-License-Identifier: GPL-2.0-only
"""Offline aggregate selection/clock guards and private-file failure cases."""

from dataclasses import replace
import io
import json
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from gui.aggregate import Clock, configuration, inspect, main, Plan, read_clock, write_new
from gui.devices import Device

BASE = Device('serial:BASE', 'OCTACAPTURE', 0, 'BASE')
EXP = Device('serial:EXP', 'OCTACAPTUREEXP', 1, 'EXP', '0121')


def status(rate=48000, external=True, active=True):
    packet = bytes((1, 0, 0)) + rate.to_bytes(3, 'little') + bytes((int(external), int(active)))
    return {'sample_rate_hz': rate, 'clock_source': 'external' if external else 'internal',
            'clock_active': active, 'raw_status_hex': packet.hex()}


class Preparation(unittest.TestCase):
    def setUp(self):
        discovery = patch('gui.aggregate.discover', return_value=(BASE, EXP))
        self.discovery = discovery.start()
        self.addCleanup(discovery.stop)

    def test_two_fresh_reads_allow_each_reference_rate_without_changing_clock(self):
        for rate in (44100, 48000, 96000):
            with self.subTest(rate=rate), patch('gui.aggregate.read_clock', side_effect=[
                    Clock(rate, True, True), Clock(rate, False, False)] * 2) as read:
                plan = inspect('/missing', BASE.identity, EXP.identity)
                self.assertEqual(plan.rate, rate)
                self.assertTrue(plan.document()['experimental'])
                self.assertEqual([call.args[1] for call in read.call_args_list], [BASE, EXP] * 2)

    def test_exp_may_itself_follow_an_active_external_clock(self):
        with patch('gui.aggregate.read_clock', return_value=Clock(48000, True, True)):
            self.assertEqual(inspect('/missing', BASE.identity, EXP.identity).rate, 48000)

    def test_missing_ambiguous_or_wrong_personality_is_rejected_before_io(self):
        for devices in ((), (BASE,), (EXP,), (BASE, BASE, EXP), (BASE, replace(EXP, product_id='0120')),
                        (BASE, replace(EXP, card_id=BASE.card_id))):
            self.discovery.return_value = devices
            with self.subTest(devices=devices), patch('gui.aggregate.read_clock') as read:
                with self.assertRaises(ValueError):
                    inspect('/missing', BASE.identity, EXP.identity)
                read.assert_not_called()

    def test_unavailable_or_mismatched_clock_refuses_configuration(self):
        pairs = ((Clock(192000, True, True), Clock(192000, False, True)),
                 (Clock(48000, True, True), Clock(96000, False, True)),
                 (Clock(48000, False, True), Clock(48000, False, True)),
                 (Clock(48000, True, False), Clock(48000, False, True)),
                 (Clock(48000, True, True), Clock(48000, True, False)))
        for pair in pairs:
            with self.subTest(pair=pair), patch('gui.aggregate.read_clock', side_effect=pair * 2):
                with self.assertRaises(ValueError):
                    inspect('/missing', BASE.identity, EXP.identity)

    def test_clock_transition_or_card_renumbering_during_preparation_is_rejected(self):
        clocks = [Clock(48000, True, True), Clock(48000, False, True)]
        with patch('gui.aggregate.read_clock', side_effect=[*clocks, Clock(96000, True, True), clocks[1]]):
            with self.assertRaises(ValueError):
                inspect('/missing', BASE.identity, EXP.identity)
        self.discovery.side_effect = [(BASE, EXP), (replace(BASE, card_index=2, card_id='NEW'), EXP)]
        with patch('gui.aggregate.read_clock', side_effect=clocks * 2):
            with self.assertRaises(ValueError):
                inspect('/missing', BASE.identity, EXP.identity)

    def test_renderer_rejects_invalid_rates_roles_and_configuration_injection(self):
        for rate in (True, 44101, 192000, '48000', 48000.0):
            with self.subTest(rate=rate), self.assertRaises(ValueError):
                configuration(BASE, EXP, rate)
        for base, exp in ((EXP, BASE), (BASE, BASE), (BASE, replace(EXP, identity=BASE.identity)),
                          (replace(BASE, card_id='x"\n type null'), EXP)):
            with self.subTest(base=base, exp=exp), self.assertRaises(ValueError):
                configuration(base, exp, 48000)


class ClockResponse(unittest.TestCase):
    def read(self, document):
        output = json.dumps(document).encode()
        with patch('gui.aggregate.subprocess.run', return_value=subprocess.CompletedProcess([], 0, output)) as run:
            clock = read_clock('/missing', BASE)
            run.assert_called_once_with(['/missing/octa-status', 'hw:CARD=OCTACAPTURE'],
                                        capture_output=True, check=True, timeout=5)
            return clock

    def test_raw_packet_and_decoded_fields_must_agree(self):
        self.assertEqual(self.read(status()), Clock(48000, True, True))
        for changes in ({'sample_rate_hz': True}, {'sample_rate_hz': 96000}, {'clock_source': 'internal'},
                        {'clock_active': 1}, {'clock_active': False}, {'raw_status_hex': '00' * 8},
                        {'raw_status_hex': 'xx' * 8}, {'raw_status_hex': '01'}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.read({**status(), **changes})
        for value in ([], None, {}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.read(value)
        with self.assertRaises(ValueError):
            self.read(status(rate=0))


class Command(unittest.TestCase):
    def test_regular_user_inspection_is_read_only_and_root_is_rejected_before_device_io(self):
        arguments = ['aggregate', '--base', BASE.identity, '--expansion', EXP.identity]
        with patch('sys.argv', arguments), patch('gui.aggregate.os.geteuid', return_value=1000), \
                patch('gui.aggregate.inspect', return_value=Plan(BASE, EXP, 48000)), \
                patch('gui.aggregate.write_new') as write, patch('sys.stdout', new_callable=io.StringIO) as output:
            self.assertEqual(main(), 0)
            self.assertEqual(json.loads(output.getvalue())['capture_channels'], 24)
            write.assert_not_called()
        with patch('sys.argv', arguments), patch('gui.aggregate.os.geteuid', return_value=0), \
                patch('gui.aggregate.inspect') as prepare, patch('sys.stderr', new_callable=io.StringIO):
            self.assertEqual(main(), 1)
            prepare.assert_not_called()

    def test_invalid_pair_never_creates_a_configuration(self):
        arguments = ['aggregate', '--base', BASE.identity, '--expansion', EXP.identity,
                     '--write-config', '/unused/alsa.conf']
        with patch('sys.argv', arguments), patch('gui.aggregate.os.geteuid', return_value=1000), \
                patch('gui.aggregate.inspect', side_effect=ValueError('Missing pair')), \
                patch('gui.aggregate.write_new') as write, patch('sys.stderr', new_callable=io.StringIO):
            self.assertEqual(main(), 1)
            write.assert_not_called()


class PrivateFile(unittest.TestCase):
    def test_new_file_is_private_and_existing_files_or_symlinks_are_never_replaced(self):
        with tempfile.TemporaryDirectory() as name:
            path = Path(name) / 'alsa.conf'
            write_new(path, b'private configuration')
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            with self.assertRaises(FileExistsError):
                write_new(path, b'replacement')
            link = Path(name) / 'link'
            link.symlink_to(path)
            with self.assertRaises(FileExistsError):
                write_new(link, b'replacement')
            self.assertEqual(path.read_bytes(), b'private configuration')
            self.assertTrue(link.is_symlink())

    def test_failed_sync_removes_only_the_new_incomplete_file(self):
        with tempfile.TemporaryDirectory() as name:
            path = Path(name) / 'alsa.conf'
            with patch('gui.aggregate.os.fsync', side_effect=OSError('Injected sync failure')):
                with self.assertRaises(OSError):
                    write_new(path, b'configuration')
            self.assertFalse(path.exists())


if __name__ == '__main__':
    unittest.main()
