# SPDX-License-Identifier: GPL-2.0-only
"""Keep the hardware checker's 192 kHz exception limited to the observed byte."""

from pathlib import Path
import runpy
import unittest

CHECK = runpy.run_path(str(Path(__file__).resolve().parent.parent / 'tools/check-preset-import.py'))


def snapshot(payload):
    return {'payload_hex': bytes(payload).hex()}


class ImportReadback(unittest.TestCase):
    def test_exact_reply_and_only_the_previous_prefix_byte_at_192_khz(self):
        for rate in range(4):
            payload = bytearray((index * 7 + 3) % 128 for index in range(1146))
            payload[2], payload[4] = rate, 0
            target = CHECK['raw_state'](snapshot(payload))
            before = bytearray(payload)
            before[4] = 5
            self.assertTrue(CHECK['application_matches'](snapshot(payload), target, snapshot(before)))
            retained = bytearray(payload)
            retained[4] = 5
            self.assertEqual(CHECK['application_matches'](snapshot(retained), target, snapshot(before)), rate == 3)
            retained[4] = 6
            self.assertFalse(CHECK['application_matches'](snapshot(retained), target, snapshot(before)))

    def test_each_other_payload_byte_still_requires_an_exact_match(self):
        payload = bytearray(1146)
        payload[2] = 3
        target = CHECK['raw_state'](snapshot(payload))
        before = bytearray(payload)
        before[4] = 5
        for retained in (False, True):
            for offset in range(1146):
                if offset == 4:
                    continue
                changed = bytearray(payload)
                changed[4] = 5 if retained else 0
                changed[offset] ^= 1
                with self.subTest(retained=retained, offset=offset):
                    self.assertFalse(CHECK['application_matches'](snapshot(changed), target, snapshot(before)))


if __name__ == '__main__':
    unittest.main()
