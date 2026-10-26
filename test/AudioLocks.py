# SPDX-License-Identifier: GPL-2.0-only
"""Serialize shared ALSA configuration across unrelated cards and pair operations."""

from contextlib import contextmanager
from pathlib import Path
import os
import select
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from gui import pulse_audio as audio
from gui.pulse_config import Files


class Locks(unittest.TestCase):
    def test_global_lock_precedes_distinct_sorted_card_locks(self):
        calls = []
        @contextmanager
        def lock(name):
            calls.append(('enter', name))
            yield
            calls.append(('exit', name))
        with patch.object(audio, '_file_lock', lock):
            with audio.configuration_lock(('EXP', 'BASE', 'EXP')):
                calls.append(('body', None))
        names = ['octa-pulse.configuration.lock', 'octa-pulse-BASE.lock', 'octa-pulse-EXP.lock']
        self.assertEqual(calls, [('enter', name) for name in names] + [('body', None)] +
                         [('exit', name) for name in reversed(names)])

    def test_unrelated_card_writer_waits_and_preserves_both_includes(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            environment = {**os.environ, 'HOME': name, 'XDG_RUNTIME_DIR': name,
                           'PYTHONPATH': str(ROOT)}
            code = ('from gui.pulse_audio import configuration_lock\n'
                    'from gui.pulse_config import Files\n'
                    'print("waiting", flush=True)\n'
                    'with configuration_lock(("EXP",)):\n'
                    ' print("entered", flush=True)\n'
                    ' Files("EXP").install("EXP", 96000)\n')
            with patch.dict(os.environ, environment), patch('pathlib.Path.home', return_value=root):
                with audio.configuration_lock(('BASE',)):
                    Files('BASE').install('BASE', 48000)
                    process = subprocess.Popen([sys.executable, '-u', '-c', code], env=environment,
                                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                    try:
                        self.assertTrue(select.select([process.stdout], [], [], 5)[0])
                        self.assertEqual(process.stdout.readline(), 'waiting\n')
                        self.assertIsNone(process.poll())
                        self.assertFalse(select.select([process.stdout], [], [], .1)[0],
                                         'An unrelated card must wait for the shared configuration lock')
                    except BaseException:
                        process.kill()
                        process.communicate(timeout=5)
                        raise
                output, errors = process.communicate(timeout=5)
                self.assertEqual(process.returncode, 0, errors)
                self.assertEqual(output, 'entered\n')
                content = (root / '.asoundrc').read_text()
                for card in ('BASE', 'EXP'):
                    self.assertIn(f'/pulse/{card}/alsa.conf>', content)

    def test_lock_cannot_follow_a_symlink_or_escape_the_runtime_directory(self):
        with tempfile.TemporaryDirectory() as name, patch.dict(os.environ, {'XDG_RUNTIME_DIR': name}):
            root = Path(name)
            target = root / 'untouched'
            target.write_text('original')
            (root / 'octa-pulse-BASE.lock').symlink_to(target)
            with self.assertRaises(OSError), audio.device_lock('BASE'):
                self.fail('Symlink lock accepted')
            with self.assertRaises(ValueError), audio.device_lock('../outside'):
                self.fail('Invalid card lock accepted')
            self.assertEqual(target.read_text(), 'original')

    def test_cancelled_waiter_exits_while_another_controller_still_owns_the_shared_lock(self):
        with tempfile.TemporaryDirectory() as name, patch.dict(os.environ, {'XDG_RUNTIME_DIR': name}):
            cancelled, started, done = threading.Event(), threading.Event(), threading.Event()
            errors = []
            def waiter():
                started.set()
                try:
                    with audio.configuration_lock(('EXP',), cancelled=cancelled):
                        errors.append('A waiting controller entered the held lock')
                except RuntimeError as error:
                    errors.append(str(error))
                finally:
                    done.set()
            with audio.configuration_lock(('BASE',)):
                thread = threading.Thread(target=waiter)
                thread.start()
                try:
                    self.assertTrue(started.wait(2))
                    self.assertFalse(done.wait(.1))
                    cancelled.set()
                    self.assertTrue(done.wait(2), 'Cancellation must not wait for the lock owner to finish')
                finally:
                    cancelled.set()
                    thread.join(timeout=2)
            self.assertFalse(thread.is_alive())
            self.assertEqual(errors, ['Audio configuration lock acquisition cancelled.'])


if __name__ == '__main__':
    unittest.main()
