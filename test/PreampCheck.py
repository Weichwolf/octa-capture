# SPDX-License-Identifier: GPL-2.0-only
"""Preamp sweep isolation and recovery regressions, without hardware access."""

from contextlib import redirect_stdout
import copy
import importlib.util
import io
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('preamp_check', Path(__file__).resolve().parent.parent / 'tools/check-preamps.py')
check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)


def fixture(rate=2):
    payload = bytearray(1146)
    payload[2] = rate
    return {'preamps': [{'low_cut': False, 'phantom': False, 'hi_z': False,
                         'compressor': {'ratio_index': 5}} for _ in range(8)],
            'stereo_links': [False] * 4, 'payload_hex': payload.hex()}


class Sweeps(unittest.TestCase):
    def run_check(self, all_values=True, failure=None):
        baseline = fixture()
        current = copy.deepcopy(baseline)
        writes = []
        args = SimpleNamespace(bin_dir=Path('/unused'), port='fake', all_values=all_values)

        def write(_directory, _port, channel, parameter, value):
            self.assertEqual((channel, parameter), (3, 'ratio'))
            writes.append(value)
            current['preamps'][channel]['compressor']['ratio_index'] = value
            if failure == 'lost-reply' and value == 2:
                raise RuntimeError('Write succeeded; reply lost.')
            if failure == 'other-channel' and value == 2:
                current['preamps'][0]['compressor']['ratio_index'] = 4
            if value == 5:
                current['preamps'][0]['compressor']['ratio_index'] = 5

        with patch.object(check, 'set_value', side_effect=write), \
                patch.object(check, 'state', side_effect=lambda *_args: copy.deepcopy(current)), \
                redirect_stdout(io.StringIO()):
            if failure:
                with self.assertRaises(RuntimeError):
                    check.check_parameter(args, baseline, 3, 'ratio', ('compressor', 'ratio_index'), 8)
            else:
                check.check_parameter(args, baseline, 3, 'ratio', ('compressor', 'ratio_index'), 8)
        self.assertEqual(current, baseline)
        return writes

    def test_every_legal_index_and_final_restore(self):
        self.assertEqual(self.run_check(), list(range(9)) + [5])

    def test_original_short_check(self):
        self.assertEqual(self.run_check(all_values=False), [4, 5])

    def test_lost_reply_restores_after_mid_sweep_write(self):
        self.assertEqual(self.run_check(failure='lost-reply'), [0, 1, 2, 5])

    def test_other_channel_change_stops_sweep_and_restores(self):
        self.assertEqual(self.run_check(failure='other-channel'), [0, 1, 2, 5])

    def test_unavailable_192_input_and_unknown_rate_do_not_write(self):
        for rate, channel in ((3, '5'), (127, '1')):
            with patch.object(sys, 'argv', ['check-preamps.py', '--write', '--channel', channel]), \
                    patch.object(check, 'state', return_value=fixture(rate)), \
                    patch.object(check, 'set_value') as write:
                with self.assertRaises(RuntimeError):
                    check.main()
                write.assert_not_called()

    def test_all_values_requires_explicit_write_before_device_read(self):
        with patch.object(sys, 'argv', ['check-preamps.py', '--all-values']), \
                patch.object(check, 'state') as read, redirect_stdout(io.StringIO()), \
                patch.object(sys, 'stderr', io.StringIO()):
            with self.assertRaises(SystemExit) as result:
                check.main()
            self.assertEqual(result.exception.code, 2)
            read.assert_not_called()


if __name__ == '__main__':
    unittest.main()
