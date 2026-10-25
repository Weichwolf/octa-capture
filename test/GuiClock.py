# SPDX-License-Identifier: GPL-2.0-only
"""Offline clock restrictions and fresh status checks before desktop audio changes."""

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from gui.application import Application, available_sample_rates
from gui.backend import DeviceError


def status(rate=48000, source=0, active=1):
    packet = bytes([1, 0, 0]) + rate.to_bytes(3, 'little') + bytes([source, active])
    return {'raw_status_hex': packet.hex(), 'sample_rate_hz': rate,
            'clock_source': 'external' if source & 1 else 'internal', 'clock_active': active != 0}


class Availability(unittest.TestCase):
    def test_original_windows_and_macos_availability_vectors(self):
        fixture = Path(__file__).with_name('data') / 'rate-availability.json'
        cases = json.loads(fixture.read_text())['cases']
        self.assertEqual(len(cases), 64)
        for row in cases:
            with self.subTest(status=row['status']):
                self.assertEqual(list(available_sample_rates(row['status'])), row['allowed'])

    def test_supported_rates_and_all_setting_byte_values(self):
        rates = (44100, 48000, 96000, 192000)
        for rate in rates:
            for source in range(256):
                for active in (0, 1):
                    self.assertEqual(available_sample_rates(status(rate, source, active)),
                                     (rate,) if source else rates)

    def test_missing_malformed_or_inconsistent_status_cannot_offer_rate_changes(self):
        valid = status()
        cases = (None, False, [], {}, {'raw_status_hex': []},
                 {**valid, 'raw_status_hex': '01'}, {**valid, 'raw_status_hex': 'xx' * 8},
                 {**valid, 'raw_status_hex': '00' + valid['raw_status_hex'][2:]},
                 {**valid, 'raw_status_hex': valid['raw_status_hex'] + ' '},
                 {**valid, 'sample_rate_hz': True}, {**valid, 'sample_rate_hz': 48000.0},
                 {**valid, 'sample_rate_hz': 96000}, status(0), status(88200))
        for value in cases:
            with self.subTest(value=value):
                self.assertEqual(available_sample_rates(value), ())


class Actions(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.application = Application('/missing/bin', Path(self.directory.name) / 'state')
        self.application.bridge = Mock()
        self.application.bridge.auto_sense_activity.return_value = None
        self.application._status = status()
        self.application._status_updated = time.monotonic()

    def test_clock_change_after_render_is_rechecked_before_any_audio_or_recovery_side_effect(self):
        with patch('gui.application.subprocess.run', return_value=Mock(stdout=json.dumps(status(source=1)))) as read, \
                patch('gui.application.change_rate') as change, patch.object(self.application, '_directory') as directory:
            with self.assertRaisesRegex(DeviceError, 'device clock.*48000'):
                self.application.action({'action': 'sample-rate', 'rate': 96000})
        self.assertEqual(read.call_args.args[0], ['/missing/bin/octa-status', 'hw:CARD=OCTACAPTURE'])
        change.assert_not_called()
        directory.assert_not_called()
        self.application.bridge.close.assert_not_called()
        self.assertEqual(self.application._status['clock_source'], 'external')
        self.assertFalse(self.application._transaction)

    def test_failed_fresh_read_cannot_use_the_previous_internal_clock_cache(self):
        with patch('gui.application.subprocess.run', side_effect=subprocess.CalledProcessError(1, 'octa-status')), \
                patch('gui.application.change_rate') as change:
            with self.assertRaises(DeviceError):
                self.application.action({'action': 'sample-rate', 'rate': 96000})
        change.assert_not_called()
        self.application.bridge.close.assert_not_called()
        self.assertFalse(self.application.data_dir.exists())
        self.assertIsNone(self.application._status)

    def test_internal_and_current_external_rates_reach_the_selected_audio_backend(self):
        for source, requested in ((0, 96000), (1, 48000), (2, 48000), (255, 48000)):
            with self.subTest(source=source, requested=requested), \
                    patch('gui.application.subprocess.run', return_value=Mock(stdout=json.dumps(status(source=source)))), \
                    patch('gui.application.change_rate') as change, \
                    patch.object(self.application, '_directory', return_value=Path('/unused')), \
                    patch.object(self.application, 'snapshot', return_value={'ok': True}):
                self.assertEqual(self.application.action({'action': 'sample-rate', 'rate': requested}), {'ok': True})
                change.assert_called_once_with(Path('/missing/bin'), 'hw:CARD=OCTACAPTURE', requested)
                self.assertFalse(self.application._transaction)


if __name__ == '__main__':
    unittest.main()
