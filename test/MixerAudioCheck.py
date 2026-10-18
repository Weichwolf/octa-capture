# SPDX-License-Identifier: GPL-2.0-only
"""Hardware-check recovery after PCM acquisition/clock restoration failures."""

from contextlib import redirect_stdout
import copy
import importlib.util
import io
from pathlib import Path
import struct
import sys
import unittest
from unittest.mock import Mock, patch

spec = importlib.util.spec_from_file_location(
    'mixer_audio_check', Path(__file__).resolve().parent.parent / 'tools/check-mixer-audio.py')
check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)
from pcm_integrity import AMPLITUDE, IntegrityError, interleave_sequence, make_sequence, verify_sequence


class IntegritySequence(unittest.TestCase):
    def setUp(self):
        self.reference = make_sequence(20000)
        self.samples = list(struct.unpack('<20000h', self.reference))

    def test_delayed_capture_has_one_exact_reference_segment(self):
        for start in (0, 781, 2047, 7000):
            with self.subTest(start=start):
                self.assertEqual(verify_sequence(self.reference, self.samples[start:start + 4096]), start)

    def test_dropped_duplicated_reordered_and_zeroed_blocks_fail(self):
        original = self.samples[500:500 + 8192]
        faults = {
            'one dropped sample': original[:2048] + original[2049:],
            'one duplicated sample': original[:2048] + original[2047:],
            '2047-sample forward jump': original[:2048] + original[4095:],
            '2047-sample backward jump': original[:2048] + original[1:],
            'reordered USB block': original[:2048] + original[2176:2304] + original[2048:2176] + original[2304:],
            'zeroed USB block': original[:2048] + [0] * 128 + original[2176:],
            'single changed sample': original[:2048] + [original[2048] ^ 1] + original[2049:],
        }
        for name, values in faults.items():
            with self.subTest(fault=name), self.assertRaises(IntegrityError):
                verify_sequence(self.reference, values)

    def test_absent_ambiguous_short_and_out_of_bounds_captures_fail(self):
        cases = ((self.reference, [0] * 64), (b'\x01\0' * 128, [1] * 64),
                 (self.reference, self.samples[:31]), (self.reference, [32767] * 64),
                 (self.reference, self.samples[-64:] + [0]), (b'\0', [0] * 64))
        for reference, values in cases:
            with self.subTest(values=values[:3], length=len(values)), self.assertRaises(IntegrityError):
                verify_sequence(reference, values)

    def test_all_ten_sources_pack_signed_quiet_samples_and_silence_other_sources(self):
        reference = self.reference[:128]
        values = self.samples[:64]
        self.assertLess(min(values), 0)
        self.assertGreater(max(values), 0)
        self.assertTrue(all(-AMPLITUDE <= value < AMPLITUDE for value in self.samples))
        for channel in range(10):
            payload = interleave_sequence(reference, channel)
            self.assertEqual(len(payload), 64 * 30)
            for frame, expected in enumerate(values):
                decoded = [int.from_bytes(payload[frame * 30 + offset:frame * 30 + offset + 3],
                                          'little', signed=True) for offset in range(0, 30, 3)]
                self.assertEqual(decoded, [expected if index == channel else 0 for index in range(10)])

    def test_invalid_lengths_geometry_and_amplitude_cannot_generate_audio(self):
        for frames in (0, -1, True, 60 * 96000 + 1, 1.5):
            with self.subTest(frames=frames), self.assertRaises(ValueError):
                make_sequence(frames)
        for reference, channel in ((b'', 0), (b'\0', 0), (self.reference, -1), (self.reference, 10),
                                   (self.reference, True), (struct.pack('<h', 1024), 0),
                                   (struct.pack('<h', -1025), 0)):
            with self.subTest(channel=channel, length=len(reference)), self.assertRaises(ValueError):
                interleave_sequence(reference, channel)


class IntegrityCapture(unittest.TestCase):
    def measure(self, corrupt, realtime=False):
        device = check.Check(Path('/unused'), aperiodic=True, realtime=realtime)
        device.rate = 100
        sinks = {}

        def process(command, **options):
            if realtime:
                self.assertEqual(command[:2], [sys.executable, str(Path(check.__file__).with_name('pcm_realtime.py'))])
            if 'arecord' in command:
                sinks['capture'] = options['stdout']
            else:
                payload = options['stdin'].read()
                audio = bytearray(200 * 36)
                for frame in range(200):
                    audio[frame * 36 + 30:frame * 36 + 33] = payload[frame * 30 + 27:frame * 30 + 30]
                if corrupt:
                    audio[120 * 36 + 30] ^= 1
                sinks['capture'].write(audio)
                sinks['capture'].flush()
            return Mock(stdout=None, stderr=None, poll=Mock(return_value=0))

        with patch.object(device, 'verify_silence'), patch.object(device, 'observe_streams', return_value={}), \
                patch.object(check.subprocess, 'Popen', side_effect=process):
            return device.measure(9, 1024, pattern=True, duration=2)

    def test_full_capture_path_compares_the_selected_source_to_its_unique_reference(self):
        result = self.measure(False)
        self.assertEqual(result[0]['reference_start_frame'], 25)
        self.assertEqual((result[1]['minimum'], result[1]['maximum']), (0, 0))

    def test_corrupted_capture_fails_without_needing_an_xrun(self):
        with self.assertRaisesRegex(check.AudioMismatch, 'sample/order mismatch'):
            self.measure(True)

    def test_realtime_mode_wraps_both_audio_processes(self):
        self.assertEqual(self.measure(False, realtime=True)[0]['reference_start_frame'], 25)


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
        scheduling = patch.object(check, 'scheduling_parameters', return_value={'policy': 'SCHED_OTHER', 'priority': 0})
        self.scheduling = scheduling.start()
        self.addCleanup(scheduling.stop)
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

    def test_realtime_demotion_fails_even_without_an_xrun(self):
        self.check.realtime = True
        self.streams[0]['process'].poll.return_value = None
        self.scheduling.side_effect = [{'policy': 'SCHED_RR', 'priority': 5}, RuntimeError('Scheduling demoted')]
        with patch.object(check, 'owned_parameters', return_value={'period_frames': 256}):
            with self.assertRaisesRegex(RuntimeError, 'Scheduling demoted'):
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
                        ['--buffer-size', '65537'], ['--period-size', '256', '--buffer-size', '7681'],
                        ['--aperiodic'], ['--aperiodic', '--write'], ['--realtime']):
            with self.subTest(options=options), patch.object(check, 'Check') as device, \
                    patch.object(sys, 'argv', ['check-mixer-audio.py', *options]), redirect_stdout(io.StringIO()), \
                    patch('sys.stderr', new_callable=io.StringIO), self.assertRaises(SystemExit) as result:
                check.main()
            self.assertEqual(result.exception.code, 2)
            device.assert_not_called()


if __name__ == '__main__':
    unittest.main()
