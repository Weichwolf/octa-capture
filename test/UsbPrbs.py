# SPDX-License-Identifier: GPL-2.0-only
"""Synthetic USB monitor records and PRBS fault regressions; no device I/O."""

import importlib.util
from pathlib import Path
import random
import struct
import unittest

spec = importlib.util.spec_from_file_location('usb_prbs', Path(__file__).resolve().parent.parent / 'tools/observe-usb-prbs.py')
usb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(usb)


def samples(count):
    state = 1
    result = []
    for _ in range(count):
        result.append(state - 1024)
        state = usb.next_pattern(state)
    return result


def packet(values, endpoint=133, status=0, error=0, device=4, bus=1):
    channels, channel = (12, 10) if endpoint == 133 else (10, 0)
    payload = b''.join(b''.join((value if index == channel else 0).to_bytes(3, 'little', signed=True)
                                for index in range(channels)) for value in values)
    data = struct.pack('<iIII', error, 0, len(payload), 0) + payload
    header = bytearray(64)
    struct.pack_into('<4BH', header, 8, 67 if endpoint == 133 else 83, 0, endpoint, device, bus)
    struct.pack_into('<qi', header, 16, 1234, 5678)
    struct.pack_into('<i', header, 28, status)
    struct.pack_into('<I', header, 36, len(data))
    struct.pack_into('<I', header, 60, 1)
    return header, data


class Patterns(unittest.TestCase):
    def track(self, values):
        reports = []
        tracker = usb.Pattern('synthetic', reports.append)
        for value in values:
            tracker.sample(value, 1.0, 1)
        return tracker, reports

    def test_complete_cycle_and_silence(self):
        for values in (samples(4094), [0] * 1000, [1024] * 1000):
            tracker, reports = self.track(values)
            self.assertEqual(reports, [])
        self.assertEqual(self.track(samples(4094))[0].verified, 4094 - 63)
        self.assertEqual(self.track([0] * 1000)[0].verified, 0)

    def test_short_zero_burst_and_order_jump(self):
        original = samples(500)
        for changed in (original[:200] + [0] * 20 + original[220:],
                        original[:200] + original[230:]):
            tracker, reports = self.track(changed)
            self.assertEqual(tracker.total_errors, 1)
            self.assertEqual(len(reports), 1)
            self.assertNotIn('value', str(reports))
        self.assertTrue(self.track(original[:200] + [0] * 20 + original[220:])[1][0]['continuity_error']['zero_sample'])

    def test_out_of_range_corruption(self):
        original = samples(500)
        tracker, reports = self.track(original[:200] + [-8388608] + original[201:])
        self.assertEqual(tracker.total_errors, 1)
        self.assertEqual(len(reports), 1)

    def test_normal_stop_and_delayed_restart_are_not_gaps(self):
        for tail in ([], [0] * 2000 + samples(400)):
            tracker, reports = self.track(samples(400) + [0] * 2000 + tail)
            self.assertEqual(reports, [])

    def test_error_storage_is_bounded(self):
        tracker, reports = self.track((samples(200) + [0] * 10) * 120)
        self.assertGreater(tracker.total_errors, usb.MAX_ERRORS)
        self.assertEqual(len(tracker.errors), usb.MAX_ERRORS)
        self.assertEqual(len(reports), usb.MAX_ERRORS)


class Records(unittest.TestCase):
    def setUp(self):
        self.reports = []
        self.decoder = usb.Decoder(4, 1, self.reports.append)

    def test_actual_packing_and_both_directions(self):
        for endpoint in (5, 133):
            self.decoder.event(*packet(samples(128), endpoint=endpoint))
        self.assertEqual(self.decoder.output.verified, 65)
        self.assertEqual(self.decoder.input.verified, 65)
        self.assertEqual(self.decoder.invalid, 0)
        self.assertEqual(self.reports, [])

    def test_other_devices_and_buses_are_ignored(self):
        for values in ({'device': 5}, {'bus': 2}):
            self.decoder.event(*packet(samples(128), **values))
        self.assertEqual(self.decoder.events, {})
        self.assertEqual(self.decoder.input.frames, 0)

    def test_large_iso_transfer_remains_valid_and_bounded(self):
        header, data = packet(samples(288))
        # 24 high-speed microframes, each containing twelve 12-channel samples.
        descriptors = b''.join(struct.pack('<iIII', 0, index * 432, 432, 0) for index in range(24))
        data = descriptors + data[16:]
        struct.pack_into('<I', header, 36, len(data))
        struct.pack_into('<I', header, 60, 24)
        self.assertGreater(len(data), 8192)
        self.assertLessEqual(len(data), usb.CAPACITY)
        self.decoder.event(header, data)
        self.assertEqual(self.decoder.input.verified, 225)
        self.assertEqual(self.decoder.invalid, 0)
        self.assertEqual(self.reports, [])

    def test_truncation_and_descriptor_bounds(self):
        header, data = packet(samples(128))
        self.decoder.event(header[:63], data)
        self.decoder.event(header, data[:-1])
        struct.pack_into('<I', header, 60, 0xffffffff)
        self.decoder.event(header, data)
        header, data = packet(samples(128))
        data = bytearray(data)
        struct.pack_into('<I', data, 4, 0xffffffff)
        self.decoder.event(header, data)
        header, data = packet(samples(128))
        data = bytearray(data)
        struct.pack_into('<I', data, 8, 1)
        self.decoder.event(header, data)
        self.assertEqual(self.decoder.invalid, 5)
        self.assertEqual(self.decoder.input.frames, 0)

    def test_packet_errors_and_expected_cancellation(self):
        self.decoder.event(*packet(samples(128), error=-18))
        self.decoder.event(*packet([], status=-104))
        self.decoder.event(*packet([], status=-2))
        self.decoder.event(*packet([], status=-32))
        summary = self.decoder.summary(0, 0)
        self.assertEqual(summary['packet_errors'], {-18: 1})
        self.assertEqual(summary['transfer_errors'], {'(133, -32)': 1})
        self.assertFalse(summary['audio_payload_files'])

    def test_submission_error_is_not_silently_ignored(self):
        header, data = packet([], endpoint=5, status=-28)
        header[8] = 69  # USB monitor E: submission failed before completion.
        self.decoder.event(header, data)
        self.assertEqual(self.decoder.summary(0, 0)['transfer_errors'], {'(5, -28)': 1})

    def test_bounded_malformed_records(self):
        generator = random.Random(82337)
        for _ in range(5000):
            header, data = packet(samples(1))
            for _ in range(generator.randrange(1, 5)):
                if generator.randrange(2):
                    struct.pack_into('<I', header, generator.choice((28, 36, 60)), generator.getrandbits(32))
                else:
                    data = generator.randbytes(generator.randrange(256))
            self.decoder.event(header, data)
        self.assertLessEqual(len(self.decoder.input.errors), usb.MAX_ERRORS)


class QueueTiming(unittest.TestCase):
    def test_overlapping_urbs_and_event_time_selection(self):
        timing = usb.Timing()
        timing.event(123, 83, 10.0)
        timing.event(456, 83, 10.001)
        timing.event(123, 67, 10.006)
        timing.event(456, 67, 10.02)
        # Later completions must not contaminate the snapshot at the first gap.
        gap = timing.at(10.006)
        self.assertEqual(gap['pending'], 1)
        self.assertEqual(gap['maximum_submit_gap_ms'], 1.0)
        self.assertEqual(gap['maximum_completion_latency_ms'], 6.0)
        self.assertEqual(gap['since_submit_ms'], 5.0)
        self.assertEqual(timing.at(10.02)['minimum_pending'], 0)
        self.assertEqual(timing.summary()['maximum_pending'], 2)
        self.assertNotIn('123', str(timing.at(10.006)))

    def test_partial_observation_failed_submit_and_bounded_storage(self):
        timing = usb.Timing()
        timing.event(999, 67, 1.0)
        timing.event(999, 69, 1.001)
        self.assertEqual(timing.summary()['unpaired_completions'], 2)
        self.assertEqual(timing.summary()['submission_errors'], 1)
        self.assertIsNone(timing.at(0.5))
        for identity in range(usb.TIMING_HISTORY + 100):
            timing.event(identity, 83, 2.0 + identity / 1000)
        self.assertEqual(len(timing.pending), usb.MAX_PENDING_URBS)
        self.assertEqual(len(timing.history), usb.TIMING_HISTORY)
        self.assertGreater(timing.summary()['evicted_submits'], 0)

    def test_detected_gap_has_correlated_host_timing(self):
        reports = []
        decoder = usb.Decoder(4, 1, reports.append)
        original = samples(500)
        corrupted = original[:200] + [0] * 20 + original[220:]
        decoder.event(*packet(samples(128), endpoint=5))
        for start in range(0, len(corrupted), 128):
            decoder.event(*packet(corrupted[start:start + 128]))
        self.assertEqual(len(reports), 1)
        timing = reports[0]['continuity_error']['host_timing']
        self.assertEqual(timing['output']['pending'], 1)
        self.assertEqual(timing['input']['pending'], 0)
        self.assertEqual(decoder.summary(0, 0)['incoming_pattern_errors'][0]['host_timing'], timing)

    def test_reordered_timestamps_are_explicit_without_negative_latencies(self):
        timing = usb.Timing()
        timing.event(123, 83, 2.0)
        timing.event(123, 67, 1.0)
        self.assertEqual(timing.summary()['nonmonotonic_events'], 1)
        gap = timing.at(1.0)
        self.assertIsNone(gap['since_submit_ms'])
        self.assertIsNone(gap['maximum_completion_latency_ms'])


if __name__ == '__main__':
    unittest.main()
