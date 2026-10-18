# SPDX-License-Identifier: GPL-2.0-only
"""Hardware-check recovery after PCM acquisition/clock restoration failures."""

from contextlib import redirect_stdout
import copy
import importlib.util
import io
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

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


class StreamVerification(unittest.TestCase):
    def setUp(self):
        self.check = check.Check(Path('/unused'), period=256, buffer=1024)
        self.check.rate = 48000
        self.streams = [{'name': name, 'process': Mock(pid=123 + index, returncode=0, poll=Mock(return_value=0)),
                         'channels': channels, 'error': io.BytesIO()}
                        for index, (name, channels) in enumerate((('arecord', 12), ('aplay', 10)))]

    def test_completed_streams_without_observed_kernel_parameters_fail(self):
        with patch.object(check, 'owned_parameters', return_value=None):
            with self.assertRaisesRegex(RuntimeError, 'actual ALSA parameters were not observed'):
                self.check.observe_streams(self.streams, 2)

    def test_xrun_diagnostics_fail_even_when_alsa_utils_returns_success(self):
        self.streams[1]['error'] = io.BytesIO(b'underrun!!!\n')
        with patch.object(check, 'owned_parameters', return_value={'period_frames': 256}):
            with self.assertRaisesRegex(RuntimeError, 'aplay failed/XRUN'):
                self.check.observe_streams(self.streams, 2)

    def test_nonzero_stream_exit_is_not_accepted_as_sample_integrity(self):
        self.streams[0]['process'].poll.return_value = 1
        with patch.object(check, 'owned_parameters', return_value={'period_frames': 256}):
            with self.assertRaisesRegex(RuntimeError, 'arecord failed: exit 1'):
                self.check.observe_streams(self.streams, 2)

    def test_xrun_written_between_polling_iterations_cannot_be_lost_at_exit(self):
        stream = self.streams[0]
        completions = iter((False, True))
        def complete():
            if not next(completions):
                return None
            stream['error'].write(b'overrun!!!\n')
            return 0
        stream['process'].poll.side_effect = complete
        with patch.object(check, 'owned_parameters', return_value={'period_frames': 256}), \
                patch.object(check.time, 'sleep'):
            with self.assertRaisesRegex(RuntimeError, 'arecord failed/XRUN'):
                self.check.observe_streams([stream], 2)

    def test_invalid_geometry_cannot_open_the_device(self):
        for options in (['--period-size', '0'], ['--period-size', '63'],
                        ['--buffer-size', '65537'], ['--period-size', '256', '--buffer-size', '7681']):
            with self.subTest(options=options), patch.object(check, 'Check') as device, \
                    patch.object(sys, 'argv', ['check-mixer-audio.py', *options]), redirect_stdout(io.StringIO()), \
                    patch('sys.stderr', new_callable=io.StringIO), self.assertRaises(SystemExit) as result:
                check.main()
            self.assertEqual(result.exception.code, 2)
            device.assert_not_called()


if __name__ == '__main__':
    unittest.main()
