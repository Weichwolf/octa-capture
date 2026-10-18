# SPDX-License-Identifier: GPL-2.0-only
"""Hardware-check recovery after PCM acquisition/clock restoration failures."""

from contextlib import redirect_stdout
import copy
import importlib.util
import io
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location(
    'mixer_audio_check', Path(__file__).resolve().parent.parent / 'tools/check-mixer-audio.py')
check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)


class Device:
    def __init__(self, clock_failure=False, restore_failure=False):
        self.original = {'output_routes': [0, 1, 2, 3, 0]}
        self.current = copy.deepcopy(self.original)
        self.clock_failure = clock_failure
        self.restore_failure = restore_failure
        self.clock_calls = 0
        self.restore_attempts = 0
        self.closed = False
        self.bridge = self

    def close(self):
        self.closed = True

    def state(self):
        return copy.deepcopy(self.current)

    def json(self, name, *_args):
        if name == 'octa-state':
            return self.state()
        if name == 'octa-status':
            return {'sample_rate_hz': 48000}
        if name == 'octa-audio-check':
            self.clock_calls += 1
            if self.clock_failure and self.clock_calls > 1:
                raise RuntimeError('PCM clock recovery busy')
            return {}
        raise AssertionError(name)

    def run(self, name, action, _recovery):
        assert (name, action) == ('octa-preset', 'restore')
        self.restore_attempts += 1
        if self.restore_failure:
            raise RuntimeError('Raw restore failed')
        self.current = copy.deepcopy(self.original)

    def silence_outputs(self):
        self.current['output_routes'] = [1] * 5

    def prepare_return(self):
        pass

    def pattern(self, *_args):
        raise RuntimeError('Capture busy')


class Recovery(unittest.TestCase):
    def run_device(self, device, error):
        with patch.object(check, 'Check', return_value=device), \
                patch.object(check, 'snapshot', return_value=Path('/unused/recovery.syx')), \
                patch.object(sys, 'argv', ['check-mixer-audio.py', '--write', '--integrity-seconds', '2']), \
                redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(RuntimeError, error):
                check.main()
        self.assertTrue(device.closed)
        self.assertEqual(device.restore_attempts, 1)

    def test_capture_failure_restores_complete_state_and_remains_a_failure(self):
        device = Device()
        self.run_device(device, 'Capture busy')
        self.assertEqual(device.current, device.original)

    def test_clock_failure_cannot_skip_raw_restoration(self):
        device = Device(clock_failure=True)
        self.run_device(device, 'PCM clock recovery busy')
        self.assertEqual(device.current, device.original)

    def test_failed_raw_restoration_never_reports_success(self):
        self.run_device(Device(clock_failure=True, restore_failure=True), 'Raw restore failed')


if __name__ == '__main__':
    unittest.main()
