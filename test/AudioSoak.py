# SPDX-License-Identifier: GPL-2.0-only
"""Reject silently negotiated PCM settings and independently recover raw state."""

from contextlib import redirect_stdout
import importlib.util
import io
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

spec = importlib.util.spec_from_file_location(
    'audio_soak', Path(__file__).resolve().parent.parent / 'tools/check-audio-soak.py')
soak = importlib.util.module_from_spec(spec)
spec.loader.exec_module(soak)


class Parameters(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.directory = self.root / 'pcm0p/sub0'
        self.directory.mkdir(parents=True)
        self.patch = patch.object(soak, 'PCM_DIRECTORY', self.root)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        (self.directory / 'status').write_text('state: RUNNING\nowner_pid   : 123\n')
        self.fields = {'access': 'RW_INTERLEAVED', 'format': 'S24_3LE', 'channels': '10',
                       'rate': '48000 (48000/1)', 'period_size': '256', 'buffer_size': '1024'}
        self.check = soak.Check(Path('/unused'), 10, 256, 1024)
        self.item = {'name': 'aplay', 'channels': 10, 'process': Mock(pid=123)}

    def write(self):
        (self.directory / 'hw_params').write_text(''.join(f'{key}: {value}\n' for key, value in self.fields.items()))

    def test_actual_parameters_are_taken_from_the_owned_kernel_stream(self):
        self.write()
        actual = self.check.parameters(self.item, 48000)
        self.assertEqual((actual['period_frames'], actual['buffer_frames']), (256, 1024))
        self.assertEqual(actual['rate_hz'], 48000)
        (self.directory / 'status').write_text('state: RUNNING\nowner_pid: 999\n')
        self.assertIsNone(self.check.parameters(self.item, 48000))
        (self.directory / 'status').write_text('closed\n')
        self.assertIsNone(self.check.parameters(self.item, 48000))

    def test_negotiation_changes_never_pass_as_requested_configuration(self):
        for field, value in (('period_size', '128'), ('buffer_size', '8192'), ('format', 'S32_LE'),
                             ('rate', '44100 (44100/1)'), ('channels', '4'), ('access', 'MMAP_INTERLEAVED')):
            with self.subTest(field=field), patch.dict(self.fields, {field: value}):
                self.write()
                with self.assertRaisesRegex(RuntimeError, 'unexpected ALSA parameters'):
                    self.check.parameters(self.item, 48000)

    def test_closed_and_incomplete_parameters_do_not_claim_verification(self):
        (self.directory / 'hw_params').write_text('closed\n')
        self.assertIsNone(self.check.parameters(self.item, 48000))
        for value in ('rate: invalid\n', 'period_size: 256\n'):
            (self.directory / 'hw_params').write_text(value)
            with self.assertRaisesRegex(RuntimeError, 'Invalid ALSA hardware parameters'):
                self.check.parameters(self.item, 48000)
        self.fields['rate'] = ''
        self.write()
        with self.assertRaisesRegex(RuntimeError, 'Invalid ALSA hardware parameters'):
            self.check.parameters(self.item, 48000)


class Recovery(unittest.TestCase):
    def setUp(self):
        self.check = soak.Check(Path('/unused'), 10, 256, 1024)
        self.before = {'payload_hex': '0102'}
        self.current = {'payload_hex': '0304'}
        self.check.run = Mock(side_effect=self.command)
        self.check.raw_restore = Mock(side_effect=self.restored)

    def command(self, name, *_args):
        if name == 'octa-state':
            return dict(self.current)
        if name == 'octa-status':
            return {'sample_rate_hz': 48000}
        return {}

    def restored(self, _recovery):
        self.current = dict(self.before)

    def test_changed_state_is_restored_and_verified_after_normal_clock_recovery(self):
        with redirect_stdout(io.StringIO()):
            self.check.restore(self.before, 48000, '/recovery.syx')
        self.check.raw_restore.assert_called_once()
        self.assertEqual(self.current, self.before)

    def test_clock_failure_cannot_skip_raw_recovery_or_be_reported_as_success(self):
        def command(name, *args):
            if name == 'octa-audio-check':
                raise subprocess.CalledProcessError(1, 'clock-busy')
            return self.command(name, *args)
        self.check.run.side_effect = command
        output = io.StringIO()
        with redirect_stdout(output), self.assertRaises(subprocess.CalledProcessError):
            self.check.restore(self.before, 48000, '/recovery.syx')
        self.check.raw_restore.assert_called_once()
        self.assertEqual(self.current, self.before)
        self.assertNotIn('verified', output.getvalue())

    def test_failure_to_restore_raw_state_is_reported(self):
        self.check.raw_restore.side_effect = OSError('Restore failed')
        with self.assertRaisesRegex(OSError, 'Restore failed'):
            self.check.restore(self.before, 48000, '/recovery.syx')

    def test_unchanged_state_does_not_trigger_unnecessary_device_writes(self):
        self.current = dict(self.before)
        with redirect_stdout(io.StringIO()):
            self.check.restore(self.before, 48000, '/recovery.syx')
        self.check.raw_restore.assert_not_called()


if __name__ == '__main__':
    unittest.main()
