# SPDX-License-Identifier: GPL-2.0-only
"""Check browser control bounds and the serialized subprocess transport offline."""

from concurrent.futures import ThreadPoolExecutor
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
                     {'kind': 'reverb', 'parameter': 'type', 'bank': 1, 'value': 0},
                     {'kind': 'reverb', 'parameter': 'pre-delay', 'bank': 0, 'value': 0}):
            with self.subTest(body=body), self.assertRaises(InvalidControl):
                validate_control(body)


class Transport(unittest.TestCase):
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


if __name__ == '__main__':
    unittest.main()
