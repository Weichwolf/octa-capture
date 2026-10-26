# SPDX-License-Identifier: GPL-2.0-only
"""Native helper result validation and cooperative process cancellation, offline."""

from dataclasses import replace
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gui import pair_clock
from gui.aggregate import Plan
from gui.devices import Device

BASE = Device('serial:BASE', 'BASE', 0, 'BASE')
EXP = Device('serial:EXP', 'EXP', 1, 'EXP', '0121')
PLAN = Plan(BASE, EXP, 48000)


def success(rate=96000):
    return {'ok': True, 'experimental': True, 'previous_rate_hz': 48000,
            'rate_hz': rate, 'changed': rate != 48000}


class Replies(unittest.TestCase):
    def decode(self, value, code=0):
        return pair_clock._decode(json.dumps(value).encode(), code, 48000, 96000)

    def test_only_exact_typed_success_can_commit(self):
        self.decode(success())
        for changes in ({'rate_hz': 48000}, {'previous_rate_hz': 44100}, {'experimental': 1},
                        {'changed': 1}, {'changed': False}, {'extra': 'field'}, {'ok': 1}):
            with self.subTest(changes=changes), self.assertRaises(pair_clock.ClockError):
                self.decode({**success(), **changes})
        with self.assertRaises(pair_clock.ClockError):
            self.decode(success(), 1)

    def test_native_failures_do_not_turn_into_success_even_after_recovery(self):
        for attempted, recovered, message in ((False, None, 'no change attempted'),
                                               (True, True, 'previous rate verified'),
                                               (True, False, 'recovery failed')):
            with self.subTest(attempted=attempted, recovered=recovered), self.assertRaisesRegex(pair_clock.ClockError, message):
                self.decode({'ok': False, 'error': -5, 'change_attempted': attempted, 'recovered': recovered}, 1)
        original = {'ok': False, 'error': -5, 'change_attempted': True, 'recovered': True}
        for changes in ({'error': False}, {'error': 0}, {'change_attempted': 1}, {'recovered': 1},
                        {'recovered': None}, {'extra': 1}, {'change_attempted': False}):
            with self.subTest(changes=changes), self.assertRaises(pair_clock.ClockError):
                self.decode({**original, **changes}, 1)
        with self.assertRaises(pair_clock.ClockError):
            self.decode(original, 0)

    def test_missing_oversized_non_utf8_and_duplicate_results_are_unverified(self):
        for value in (b'', b'\xff', b'x' * 4097, b'null', b'[]', b'{}',
                      b'{"ok":true,"ok":false}'):
            with self.subTest(value=value[:20]), self.assertRaises(pair_clock.ClockError):
                pair_clock._decode(value, 0, 48000, 96000)


class Processes(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='octa pair worker ')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.binary = self.root / 'octa-pair-clock'

    def worker(self, body):
        self.binary.write_text(f'#!{sys.executable}\nimport json, os, signal, sys, time\nfrom pathlib import Path\n'
                               'root = Path(__file__).parent\n' + body)
        self.binary.chmod(0o700)

    def test_arguments_keep_card_serial_pairs_and_paths_with_spaces(self):
        self.worker('(root / "arguments").write_text(json.dumps(sys.argv[1:]))\n'
                    'print(json.dumps({"ok":True,"experimental":True,"previous_rate_hz":48000,'
                    '"rate_hz":int(sys.argv[2]),"changed":int(sys.argv[2])!=48000}))\n')
        for rate in (44100, 48000, 96000):
            pair_clock.change(self.root, PLAN, rate)
            self.assertEqual(json.loads((self.root / 'arguments').read_text()),
                             ['--experimental', str(rate), 'BASE', 'BASE', 'EXP', 'EXP'])

    def test_invalid_requests_never_spawn_a_helper(self):
        with patch.object(pair_clock.subprocess, 'Popen') as spawn:
            for rate in (True, 192000, 0, '48000'):
                with self.assertRaises(ValueError):
                    pair_clock.change(self.root, PLAN, rate)
            for serial in ('', '../OTHER', 'A' * 65, 'A\0B'):
                with self.assertRaises(ValueError):
                    pair_clock.change(self.root, replace(PLAN, base=replace(BASE, serial=serial)), 96000)
            with self.assertRaises(ValueError):
                pair_clock.change(self.root, replace(PLAN, expansion=replace(EXP, serial=BASE.serial)), 96000)
            spawn.assert_not_called()

    def recovery_worker(self):
        self.worker('def recover(_number, _frame):\n'
                    ' time.sleep(.05)\n'
                    ' (root / "recovered").write_text("old clock verified")\n'
                    ' print(json.dumps({"ok":False,"error":-4,"change_attempted":True,"recovered":True}),flush=True)\n'
                    ' raise SystemExit(1)\n'
                    'signal.signal(signal.SIGTERM,recover)\n'
                    '(root / "pid").write_text(str(os.getpid()))\n'
                    'while True: time.sleep(.01)\n')

    def assert_reaped(self):
        pid = int((self.root / 'pid').read_text())
        with self.assertRaises(ProcessLookupError):
            os.kill(pid, 0)

    def test_timeout_waits_for_native_sigterm_recovery_before_returning(self):
        self.recovery_worker()
        with patch.object(pair_clock, 'CHANGE_TIMEOUT', .3), patch.object(pair_clock, 'RECOVERY_TIMEOUT', 2):
            with self.assertRaises(subprocess.TimeoutExpired):
                pair_clock.change(self.root, PLAN, 96000)
        self.assertEqual((self.root / 'recovered').read_text(), 'old clock verified')
        self.assert_reaped()

    def test_interrupt_also_waits_for_cooperative_recovery(self):
        self.recovery_worker()
        original = subprocess.Popen
        def process(*arguments, **keywords):
            result = original(*arguments, **keywords)
            communicate = result.communicate
            first = True
            def interrupted(*arguments, **keywords):
                nonlocal first
                if first:
                    first = False
                    deadline = time.monotonic() + 3
                    while not (self.root / 'pid').exists():
                        if time.monotonic() >= deadline:
                            self.fail('Worker did not install its cancellation handler')
                        time.sleep(.005)
                    raise KeyboardInterrupt
                return communicate(*arguments, **keywords)
            result.communicate = interrupted
            return result
        with patch.object(pair_clock.subprocess, 'Popen', process):
            with self.assertRaises(KeyboardInterrupt):
                pair_clock.change(self.root, PLAN, 96000)
        self.assertTrue((self.root / 'recovered').is_file())
        self.assert_reaped()

    def test_unresponsive_helper_is_killed_and_never_claims_recovery(self):
        self.worker('signal.signal(signal.SIGTERM,signal.SIG_IGN)\n'
                    '(root / "pid").write_text(str(os.getpid()))\n'
                    'while True: time.sleep(.01)\n')
        with patch.object(pair_clock, 'CHANGE_TIMEOUT', .3), patch.object(pair_clock, 'RECOVERY_TIMEOUT', .1):
            with self.assertRaises(subprocess.TimeoutExpired):
                pair_clock.change(self.root, PLAN, 96000)
        self.assertFalse((self.root / 'recovered').exists())
        self.assert_reaped()

    def test_shared_shutdown_flag_waits_for_cooperative_native_recovery(self):
        self.recovery_worker()
        cancelled = threading.Event()
        errors = []
        def close_panel():
            deadline = time.monotonic() + 3
            while not (self.root / 'pid').exists():
                if time.monotonic() >= deadline:
                    errors.append('Worker did not start')
                    break
                time.sleep(.005)
            cancelled.set()
        thread = threading.Thread(target=close_panel)
        thread.start()
        try:
            with self.assertRaises(pair_clock.ClockCancelled):
                pair_clock.change(self.root, PLAN, 96000, cancelled=cancelled)
        finally:
            cancelled.set()
            thread.join(timeout=3)
        self.assertEqual(errors, [])
        self.assertTrue((self.root / 'recovered').is_file())
        self.assert_reaped()

    def test_shutdown_before_launch_never_spawns_the_helper(self):
        cancelled = threading.Event()
        cancelled.set()
        with patch.object(pair_clock.subprocess, 'Popen') as spawn:
            with self.assertRaises(pair_clock.ClockCancelled):
                pair_clock.change(self.root, PLAN, 96000, cancelled=cancelled)
            spawn.assert_not_called()

    def test_cancellable_wait_preserves_one_complete_success_after_multiple_polls(self):
        self.worker('time.sleep(.25)\nprint(' + repr(json.dumps(success())) + ')\n')
        pair_clock.change(self.root, PLAN, 96000, cancelled=threading.Event())


if __name__ == '__main__':
    unittest.main()
