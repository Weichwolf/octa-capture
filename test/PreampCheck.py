# SPDX-License-Identifier: GPL-2.0-only
"""Preamp sweep isolation and recovery regressions, without hardware access."""

from contextlib import redirect_stdout
import copy
import importlib.util
import io
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('preamp_check', Path(__file__).resolve().parent.parent / 'tools/check-preamps.py')
check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)


def fixture(rate=2):
    payload = bytearray(1146)
    payload[2] = rate
    payload[0x48:0x50] = bytes([5] * 8)
    return {'preamps': [{'low_cut': False, 'phantom': False, 'hi_z': False,
                         'compressor': {'ratio_index': 5}} for _ in range(8)],
            'stereo_links': [False] * 4, 'payload_hex': payload.hex(),
            'output_routes': [0] * 5, 'mixers': [
                {source + '_master': {'volume': [0x200000, 0x200000]}
                 for source in ('input', 'playback')} for _ in range(4)]}


class Sweeps(unittest.TestCase):
    def test_full_range_requires_all_outputs_to_available_silent_mixers(self):
        silent = {'output_routes': [1] * 5, 'mixers': [
            {source + '_master': {'volume': [0, 0]} for source in ('input', 'playback')}
            for _ in range(4)]}
        check.require_silent_outputs(silent, 8)
        with self.assertRaises(RuntimeError):
            check.require_silent_outputs(silent, 4)
        silent['output_routes'] = [0] * 5
        check.require_silent_outputs(silent, 4)
        for source in ('input', 'playback'):
            for channel in range(2):
                changed = copy.deepcopy(silent)
                changed['mixers'][0][source + '_master']['volume'][channel] = 1
                with self.assertRaises(RuntimeError):
                    check.require_silent_outputs(changed, 8)
        for routes in ([0] * 4, [0, 0, 0, 0, 4], [0, 0, 0, 0, -1]):
            changed = {**silent, 'output_routes': routes}
            with self.assertRaises(RuntimeError):
                check.require_silent_outputs(changed, 8)

    def run_check(self, all_values=True, failure=None):
        baseline = fixture()
        current = copy.deepcopy(baseline)
        writes = []
        args = SimpleNamespace(bin_dir=Path('/unused'), port='fake', all_values=all_values)

        def write(_directory, _port, channel, parameter, value):
            self.assertEqual((channel, parameter), (3, 'ratio'))
            writes.append(value)
            current['preamps'][channel]['compressor']['ratio_index'] = value
            raw = bytearray.fromhex(current['payload_hex'])
            raw[0x48 + channel] = value
            if failure == 'other-channel' and value == 2:
                current['preamps'][0]['compressor']['ratio_index'] = 4
                raw[0x48] = 4
            if failure == 'unmapped-byte' and value == 2:
                raw[0x10] = 1
            if failure == 'routing' and value == 2:
                current['output_routes'][0] = 1
            if value == 5:
                current['preamps'][0]['compressor']['ratio_index'] = 5
                raw[0x48] = 5
                raw[0x10] = 0
                current['output_routes'][0] = 0
            current['payload_hex'] = raw.hex()
            if failure == 'lost-reply' and value == 2:
                raise RuntimeError('Write succeeded; reply lost.')

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

    def test_unmapped_raw_change_stops_the_sweep(self):
        self.assertEqual(self.run_check(failure='unmapped-byte'), [0, 1, 2, 5])

    def test_non_preamp_change_stops_the_sweep(self):
        self.assertEqual(self.run_check(failure='routing'), [0, 1, 2, 5])

    def test_private_raw_snapshot_and_recovery_of_non_preamp_changes(self):
        baseline = fixture()
        with tempfile.TemporaryDirectory() as name:
            recovery = check.snapshot(Path(name), baseline)
            raw = recovery.read_bytes()
            self.assertEqual(raw[:11], bytes.fromhex('f0411000004d1201000000'))
            self.assertEqual(raw[11:-2], bytes.fromhex(baseline['payload_hex']))
            self.assertEqual(sum(raw[7:-1]) % 128, 0)
            self.assertEqual(recovery.stat().st_mode & 0o777, 0o600)
            args = SimpleNamespace(bin_dir=Path('/unused'), port='fake')
            changed = copy.deepcopy(baseline)
            changed['output_routes'][0] = 1
            with patch.object(check, 'state', side_effect=[changed, baseline]), \
                    patch.object(check.subprocess, 'run') as restore:
                check.restore(args, baseline, recovery)
                self.assertEqual(restore.call_args.args[0][1:], ['restore', str(recovery), 'fake'])
            with patch.object(check, 'state', return_value=baseline), \
                    patch.object(check.subprocess, 'run') as restore:
                check.restore(args, baseline, recovery)
                restore.assert_not_called()

    def test_failed_sweep_recovers_the_full_state_and_keeps_the_failure(self):
        baseline = fixture()
        current = copy.deepcopy(baseline)

        def failed(*_args):
            current['output_routes'][0] = 1
            raise RuntimeError('Injected unexpected routing change')

        def restored(*_args, **_kwargs):
            current.update(copy.deepcopy(baseline))

        with tempfile.TemporaryDirectory() as name, \
                patch.object(sys, 'argv', ['check-preamps.py', '--write', '--channel', '1', '--recovery-dir', name]), \
                patch.object(check, 'state', side_effect=lambda *_args: copy.deepcopy(current)), \
                patch.object(check, 'check_parameter', side_effect=failed), \
                patch.object(check.subprocess, 'run', side_effect=restored) as restore, \
                redirect_stdout(io.StringIO()) as output:
            with self.assertRaisesRegex(RuntimeError, 'Injected unexpected routing change'):
                check.main()
            restore.assert_called_once()
            self.assertEqual(current, baseline)
            self.assertNotIn('PASS:', output.getvalue())
            self.assertEqual(len(list(Path(name).glob('*/original.syx'))), 1)

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

    def test_live_outputs_block_full_range_before_any_write(self):
        with patch.object(sys, 'argv', ['check-preamps.py', '--write', '--all-values']), \
                patch.object(check, 'state', return_value=fixture()), \
                patch.object(check, 'set_value') as write:
            with self.assertRaises(RuntimeError):
                check.main()
            write.assert_not_called()


if __name__ == '__main__':
    unittest.main()
