# SPDX-License-Identifier: GPL-2.0-only
"""Check browser control bounds and the serialized subprocess transport offline."""

from concurrent.futures import ThreadPoolExecutor
import copy
from pathlib import Path
import sys
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from gui.backend import Bridge, DeviceError, InvalidControl, validate_control


class Controls(unittest.TestCase):
    def test_valid(self):
        examples = [
            ({'kind': 'preamp', 'channel': 0, 'parameter': 'sensitivity', 'value': 100},
             ('preamp', [0, 4, 100])),
            ({'kind': 'preamp', 'channel': 6, 'parameter': 'stereo-link', 'value': 1},
             ('preamp', [6, 5, 1])),
            ({'kind': 'mixer', 'source': 'input', 'bus': 0, 'channel': 9, 'parameter': 'send', 'value': 1},
             ('mixer', [0, 0, 9, 14, 1])),
            ({'kind': 'master', 'source': 'input', 'bus': 3, 'channel': 0, 'parameter': 'io-link', 'value': 1},
             ('master', [0, 3, 0, 13, 1])),
            ({'kind': 'reverb', 'parameter': 'time', 'bank': 4, 'value': 49},
             ('reverb', [2, 4, 49])),
            ({'kind': 'route', 'pair': 4, 'selection': 8}, ('route', [4, 8])),
            ({'kind': 'display', 'bus': 3}, ('display', [3])),
            ({'kind': 'identify'}, ('identify', [])),
            ({'kind': 'monitor', 'channel': 9, 'enabled': False, 'pan': 32768, 'volume': 8388607},
             ('monitor', [9, 0, 32768, 8388607])),
            ({'kind': 'monitor', 'channel': None, 'enabled': True, 'pan': 0, 'volume': 0},
             ('monitor', [127, 1, 0, 0])),
        ]
        for body, expected in examples:
            self.assertEqual(validate_control(body), expected)

    def test_preamp_bounds(self):
        original = {'kind': 'preamp', 'channel': 0, 'parameter': 'sensitivity', 'value': 0}
        for changes in ({'value': True}, {'value': 101}, {'value': -1}, {'value': '20'},
                        {'channel': 256}, {'channel': -1}, {'extra': 1},
                        {'channel': 1, 'parameter': 'stereo-link'},
                        {'channel': 2, 'parameter': 'hi-z'}, {'parameter': 'unknown'}):
            with self.subTest(changes=changes), self.assertRaises(InvalidControl):
                validate_control({**original, **changes})

    def test_context_bounds(self):
        original = {'kind': 'mixer', 'source': 'input', 'bus': 0, 'channel': 0,
                    'parameter': 'volume', 'value': 0}
        for changes in ({'value': 0x800000}, {'parameter': 'pan', 'value': 0x8001},
                        {'parameter': 'link', 'channel': 1}, {'source': 'wrong'},
                        {'bus': 4}, {'parameter': 'send', 'bus': 1},
                        {'parameter': 'send', 'source': 'playback'},
                        {'kind': 'master', 'parameter': 'link', 'channel': 1},
                        {'kind': 'master', 'parameter': 'return', 'bus': 1},
                        {'kind': 'master', 'parameter': 'io-link', 'source': 'playback'}):
            with self.subTest(changes=changes), self.assertRaises(InvalidControl):
                validate_control({**original, **changes})
        for body in (None, [], {}, {'kind': True},
                     {'kind': 'display', 'bus': 4}, {'kind': 'display', 'bus': True},
                     {'kind': 'display', 'bus': -1}, {'kind': 'identify', 'bus': 0},
                     {'kind': 'reverb', 'parameter': 'type', 'bank': 1, 'value': 0},
                     {'kind': 'reverb', 'parameter': 'pre-delay', 'bank': 0, 'value': 0}):
            with self.subTest(body=body), self.assertRaises(InvalidControl):
                validate_control(body)

    def test_monitor_bounds_reject_coercions_unknown_fields_and_unmapped_inputs(self):
        original = {'kind': 'monitor', 'channel': 0, 'enabled': True, 'pan': 0, 'volume': 0}
        for changes in ({'channel': True}, {'channel': -1}, {'channel': 10}, {'channel': 127},
                        {'enabled': 1}, {'enabled': 'true'}, {'pan': True}, {'pan': -1}, {'pan': 32769},
                        {'volume': -1}, {'volume': 8388608}, {'volume': 1.0}, {'extra': 0}):
            with self.subTest(changes=changes), self.assertRaises(InvalidControl):
                validate_control({**original, **changes})


class Transport(unittest.TestCase):
    def test_auto_sense_is_unknown_until_observed_and_invalidated_on_close(self):
        peer = self.bridge('auto-sense')
        self.assertIsNone(peer.auto_sense_activity())
        peer.request('state')
        observed = peer.auto_sense_activity()
        self.assertEqual((observed['channels'], observed['active']), (1, True))
        self.assertGreaterEqual(observed['observed_age_ms'], 0)
        observed['channels'] = 255
        self.assertEqual(peer.auto_sense_activity()['channels'], 1)
        peer.request('preamp', [0, 4, 20])
        self.assertEqual(peer.auto_sense_activity()['channels'], 0)
        self.assertIs(peer.auto_sense_activity()['active'], False)
        peer.close()
        self.assertIsNone(peer.auto_sense_activity())
        peer.request('state')
        self.assertIs(peer.auto_sense_activity()['active'], True)
        peer._process.terminate()
        peer._reader.join(timeout=3)
        self.assertFalse(peer._reader.is_alive())
        self.assertIsNone(peer.auto_sense_activity())

    def test_auto_sense_event_bounds_and_atomic_rejection(self):
        peer = Bridge(Path('/nonexistent/backend'))
        valid = {'event': 'auto-sense', 'channels': 1, 'active': True}
        peer._update_auto_sense(valid)
        for changes in ({'channels': True}, {'channels': -1}, {'channels': 256}, {'channels': 1.0},
                        {'active': 1}, {'active': False}, {'event': 'other'}, {'extra': 0}):
            with self.subTest(changes=changes), self.assertRaises(DeviceError):
                peer._update_auto_sense({**valid, **changes})
            self.assertEqual(peer.auto_sense_activity()['channels'], 1)
        for key in valid:
            with self.subTest(missing=key), self.assertRaises(DeviceError):
                peer._update_auto_sense({name: value for name, value in valid.items() if name != key})
        peer._update_auto_sense({'event': 'auto-sense', 'channels': 0, 'active': False})
        self.assertIs(peer.auto_sense_activity()['active'], False)

    def test_bad_auto_sense_event_fails_the_transport(self):
        peer = self.bridge('auto-sense-bad')
        with self.assertRaises(DeviceError):
            peer.request('state')
        self.assertIsNone(peer.auto_sense_activity())

    def bridge(self, mode='normal'):
        peer = Bridge(Path(__file__).with_name('FakeBridge.py'), mode)
        self.addCleanup(peer.close)
        return peer

    def test_replies_meters_and_restart(self):
        peer = self.bridge()
        self.assertEqual(peer.request('preamp', [0, 4, 20])['state']['values'], [0, 4, 20])
        self.assertEqual(peer.meters()['input_levels'], [0] * 8)
        self.assertGreaterEqual(peer.meters()['age_ms'], 0)
        peer.close()
        self.assertIsNone(peer.meters())
        self.assertEqual(peer.request('state')['state']['operation'], 'state')

    def test_monitor_change_reaches_the_serialized_transport(self):
        peer = self.bridge()
        state = peer.change({'kind': 'monitor', 'channel': None, 'enabled': True,
                             'pan': 16384, 'volume': 2097152})
        self.assertEqual(state, {'operation': 'monitor', 'values': [127, 1, 16384, 2097152]})

    def test_concurrent_requests_do_not_cross_replies(self):
        peer = self.bridge()
        with ThreadPoolExecutor(max_workers=4) as pool:
            values = list(pool.map(lambda value: peer.request('preamp', [0, 4, value])['state']['values'][2],
                                   range(20)))
        self.assertEqual(values, list(range(20)))

    def test_dead_or_wrong_peer_fails_promptly(self):
        for mode in ('bad-id', 'eof'):
            peer = self.bridge(mode)
            start = time.monotonic()
            with self.assertRaises(DeviceError):
                peer.request('state')
            self.assertLess(time.monotonic() - start, 3)

    def test_invalid_control_never_starts_process(self):
        peer = Bridge(Path('/nonexistent/backend'))
        with self.assertRaises(InvalidControl):
            peer.change({'kind': 'preamp', 'channel': 7, 'parameter': 'hi-z', 'value': 1})
        with self.assertRaises(InvalidControl):
            peer.request('state\n1 preamp', [])

    def test_pcm_frames_and_invalid_fields(self):
        peer = Bridge(Path('/nonexistent/backend'))
        direction = {'rate': 48000, 'channels': 12, 'age_ms': 0, 'sequence': 1,
                     'fresh': True, 'peaks': [0x7fffff00] + [0] * 11}
        frame = {'enabled': True, 'idle_stream': True, 'error': 0,
                 'capture': direction,
                 'playback': {**direction, 'channels': 10, 'peaks': [0] * 10}}
        peer._update_pcm(frame)
        received = peer.meters()
        self.assertEqual(received['pcm']['capture']['peaks'][0], 0x7fffff00)
        self.assertGreaterEqual(received['pcm']['received_age_ms'], 0)
        for key, value in (('rate', 1), ('channels', 13), ('age_ms', True), ('sequence', -1),
                           ('fresh', False), ('peaks', [0] * 11), ('peaks', [True] * 12),
                           ('peaks', [0x80000000] * 12), ('peaks', [1] * 12)):
            invalid = copy.deepcopy(frame)
            invalid['capture'][key] = value
            with self.subTest(key=key), self.assertRaises(DeviceError):
                peer._update_pcm(invalid)
        # Stale kernel data is retained, never relabeled as a fresh sample.
        stale = copy.deepcopy(frame)
        stale['capture'].update(fresh=False, age_ms=501)
        peer._update_pcm(stale)
        self.assertFalse(peer.meters()['pcm']['capture']['fresh'])

    def test_short_peaks_survive_latest_frame_replacement(self):
        peer = Bridge(Path('/nonexistent/backend'))
        peer._update_meter({'input_levels': [16383] * 8, 'input_db': [0] * 8})
        peer._update_meter({'input_levels': [0] * 8, 'input_db': [-96] * 8})
        result = peer.meters()
        self.assertEqual(result['input_levels'], [0] * 8)
        self.assertEqual(result['history'][0]['levels'], [16383] * 8)
        self.assertEqual(result['history'][1]['levels'], [0] * 8)
        self.assertLess(result['history'][0]['id'], result['history'][1]['id'])
        for _ in range(100):
            peer._update_meter({'input_levels': [0] * 8, 'input_db': [-96] * 8})
        result = peer.meters()
        self.assertEqual(len(result['history']), 64)
        last_id = result['history'][-1]['id']
        peer.close()
        self.assertIsNone(peer.meters())
        peer._update_meter({'input_levels': [0] * 8, 'input_db': [-96] * 8})
        self.assertGreater(peer.meters()['history'][0]['id'], last_id)


if __name__ == '__main__':
    unittest.main()
