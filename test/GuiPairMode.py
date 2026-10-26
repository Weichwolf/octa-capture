# SPDX-License-Identifier: GPL-2.0-only
"""Paired GUI role/clock guards and lifecycle; all device replies are fixtures."""

from contextlib import ExitStack
from dataclasses import replace
import io
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gui import aggregate, application, desktop, pair_mode, server
from gui.application import Application
from gui.backend import DeviceError, InvalidControl
from gui.devices import Device

BASE = Device('serial:BASE', 'BASE', 0, 'BASE')
EXP = Device('serial:EXP', 'EXP', 1, 'EXP', '0121')
OTHER = Device('serial:OTHER', 'OTHER', 2, 'OTHER')
PLAN = aggregate.Plan(BASE, EXP, 48000)


def status(rate=48000, source=0, active=True):
    raw = bytes((1, 0, 0)) + rate.to_bytes(3, 'little') + bytes((source, int(active)))
    return {'sample_rate_hz': rate, 'clock_source': 'external' if source & 1 else 'internal',
            'clock_active': active, 'raw_status_hex': raw.hex()}


class Selection(unittest.TestCase):
    def test_serial_roles_and_duplicate_guards(self):
        for base, exp in (('usb:1-1', EXP.identity), (BASE.identity, ''), (BASE.identity, BASE.identity),
                          ('serial:../BASE', EXP.identity), ('serial:' + 'A' * 65, EXP.identity)):
            with self.subTest(base=base, exp=exp), self.assertRaises(ValueError):
                pair_mode.PairMode(base, exp)
        pair = pair_mode.PairMode(BASE.identity, EXP.identity)
        self.assertEqual(pair.visible((BASE, EXP, OTHER)), (BASE, EXP))
        self.assertEqual(pair.visible((BASE, BASE, EXP)), (EXP,))
        self.assertFalse(pair.member(replace(BASE, product_id='0121')))
        self.assertFalse(pair.member(replace(EXP, serial='REPLACEMENT')))

    def test_reference_master_restrictions_and_pair_limits(self):
        pair = pair_mode.PairMode(BASE.identity, EXP.identity)
        with patch.object(aggregate, 'inspect', return_value=PLAN):
            pair.require_plan('/unused')
        self.assertEqual(pair.document(status(), EXP.identity)['allowed_rates'], [44100, 48000, 96000])
        self.assertEqual(pair.document(status(source=1), BASE.identity)['allowed_rates'], [48000])
        for document, selected in ((status(source=1), EXP.identity), (status(source=2), EXP.identity),
                                   (status(), BASE.identity), (status(active=False, source=1), BASE.identity),
                                   (status(rate=96000), EXP.identity), (status(), OTHER.identity),
                                   (None, EXP.identity), ({**status(), 'clock_active': 1}, EXP.identity)):
            with self.subTest(document=document, selected=selected):
                result = pair.document(document, selected)
                self.assertFalse(result['available'])
                self.assertEqual(result['allowed_rates'], [])

    def test_failed_refresh_cannot_reuse_previously_verified_pair(self):
        pair = pair_mode.PairMode(BASE.identity, EXP.identity)
        with patch.object(aggregate, 'inspect', return_value=PLAN) as inspect:
            pair.refresh('/unused')
            pair.refresh('/unused')
            inspect.assert_called_once_with('/unused', BASE.identity, EXP.identity, require_master=True)
        with patch.object(aggregate, 'inspect', side_effect=ValueError('EXP disconnected')):
            with self.assertRaisesRegex(DeviceError, 'EXP disconnected'):
                pair.require_plan('/unused')
        self.assertFalse(pair.document(status(), EXP.identity)['available'])
        pair.cancelled.set()
        pair.reset()
        self.assertTrue(pair.cancelled.is_set())


class Actions(unittest.TestCase):
    def setUp(self):
        stack = ExitStack()
        self.addCleanup(stack.close)
        directory = stack.enter_context(tempfile.TemporaryDirectory())
        self.pair = pair_mode.PairMode(BASE.identity, EXP.identity)
        self.app = Application('/missing', Path(directory) / 'state', EXP.port, pair=self.pair)
        self.app._device = EXP
        self.bridge = Mock()
        self.bridge.auto_sense_activity.return_value = {'active': False, 'channels': 0, 'observed_age_ms': 0}
        self.bridge.request.return_value = {'state': {'selected': 'EXP'}}
        self.app.bridge = self.bridge
        self.companion = Mock()
        self.companion.auto_sense_activity.return_value = {'active': False, 'channels': 0, 'observed_age_ms': 0}
        self.companion.request.return_value = {'state': {'selected': 'BASE'}}
        self.discovery = stack.enter_context(patch.object(application, 'discover', return_value=(BASE, EXP, OTHER)))
        stack.enter_context(patch.object(aggregate, 'inspect', return_value=PLAN))
        self.create = stack.enter_context(patch.object(application, 'Bridge', return_value=self.companion))
        self.single = stack.enter_context(patch.object(application, 'change_rate'))
        self.joint = stack.enter_context(patch.object(application, 'change_pair_rate'))
        self.directory = stack.enter_context(patch.object(self.app, '_directory'))
        self.app._clock_status = Mock(return_value=status())
        self.app.snapshot = Mock(return_value={'restored': True})

    def test_exp_rate_action_uses_joint_backend_and_probes_both_auto_sense_states(self):
        self.assertEqual(self.app.action({'action': 'sample-rate', 'rate': 96000}), {'restored': True})
        self.joint.assert_called_once_with(Path('/missing'), BASE.identity, EXP.identity, 96000,
                                           expected_devices=(BASE, EXP), cancelled=self.pair.cancelled)
        self.single.assert_not_called()
        self.directory.assert_not_called()
        self.bridge.request.assert_called_once_with('state')
        self.companion.request.assert_called_once_with('state')
        self.companion.close.assert_called_once_with()
        self.create.assert_called_once_with(Path('/missing/octa-bridge'), BASE.port)
        self.assertFalse(self.app._transaction)

    def test_192k_and_non_pair_selection_are_rejected_before_device_io(self):
        with self.assertRaises(InvalidControl):
            self.app.action({'action': 'sample-rate', 'rate': 192000})
        self.app._clock_status.assert_not_called()
        self.discovery.assert_not_called()
        with self.assertRaises(InvalidControl):
            self.app.select_device({'id': OTHER.identity})
        self.bridge.close.assert_not_called()
        self.joint.assert_not_called()
        self.assertEqual([device['id'] for device in self.app.devices()['devices']], [BASE.identity, EXP.identity])

    def test_base_can_only_keep_its_current_external_rate(self):
        self.app._selected_identity = BASE.identity
        self.app._device, self.app.port = BASE, BASE.port
        self.app._clock_status.return_value = status(source=1)
        with self.assertRaisesRegex(DeviceError, 'only 48000'):
            self.app.action({'action': 'sample-rate', 'rate': 96000})
        self.bridge.request.assert_not_called()
        self.joint.assert_not_called()

    def test_active_unknown_or_stale_companion_auto_sense_blocks_before_any_audio_change(self):
        for activity in (None, {'active': True, 'channels': 1, 'observed_age_ms': 0},
                         {'active': False, 'channels': 0, 'observed_age_ms': 1001}):
            with self.subTest(activity=activity):
                self.companion.reset_mock()
                self.companion.auto_sense_activity.return_value = activity
                with self.assertRaises(DeviceError):
                    self.app.action({'action': 'sample-rate', 'rate': 96000})
                self.companion.close.assert_called_once_with()
        self.joint.assert_not_called()
        self.single.assert_not_called()
        self.directory.assert_not_called()
        self.bridge.close.assert_not_called()

    def test_disappearance_or_renumbering_during_preflight_never_falls_back_to_single_unit(self):
        with patch.object(aggregate, 'inspect', side_effect=ValueError('Base missing')):
            with self.assertRaisesRegex(DeviceError, 'Base missing'):
                self.app.action({'action': 'sample-rate', 'rate': 96000})
        def renumber(*_arguments, **_keywords):
            device = replace(EXP, card_index=4, card_id='NEWEXP')
            self.discovery.return_value = (BASE, device)
            return replace(PLAN, expansion=device)
        with patch.object(aggregate, 'inspect', side_effect=renumber):
            with self.assertRaisesRegex(DeviceError, 'selected unit changed'):
                self.app.action({'action': 'sample-rate', 'rate': 96000})
        self.single.assert_not_called()
        self.joint.assert_not_called()

    def test_joint_backend_failure_invalidates_pair_status_and_retains_actual_error(self):
        self.joint.side_effect = RuntimeError('Paired audio recovery is incomplete')
        with self.assertRaisesRegex(DeviceError, 'recovery is incomplete'):
            self.app.action({'action': 'sample-rate', 'rate': 96000})
        self.assertFalse(self.pair.document(status(), EXP.identity)['available'])
        self.assertFalse(self.app._transaction)

    def test_close_signals_the_pair_before_waiting_for_the_active_operation(self):
        closed = threading.Event()
        with self.app._operation:
            thread = threading.Thread(target=lambda: (self.app.close(), closed.set()))
            thread.start()
            try:
                self.assertTrue(self.pair.cancelled.wait(2))
                self.assertFalse(closed.is_set())
            finally:
                self.pair.cancelled.set()
        thread.join(timeout=2)
        self.assertFalse(thread.is_alive())
        self.assertTrue(closed.is_set())


class Launchers(unittest.TestCase):
    def test_missing_flag_or_invalid_serial_fails_before_either_host_opens(self):
        for host in (server, desktop):
            for arguments in (['--pair-base', BASE.identity], ['--experimental-pair'],
                              ['--pair-base', BASE.identity, '--pair-expansion', EXP.identity],
                              ['--pair-base', BASE.identity, '--pair-expansion', BASE.identity, '--experimental-pair']):
                with self.subTest(host=host.__name__, arguments=arguments), patch('sys.argv', ['panel', *arguments]), \
                        patch.object(host, 'Application') as app, patch('sys.stderr', new_callable=io.StringIO):
                    with self.assertRaises(SystemExit) as error:
                        host.main()
                    self.assertEqual(error.exception.code, 2)
                    app.assert_not_called()

    def test_launchers_parse_the_same_explicit_pair_configuration(self):
        for host in (server, desktop):
            arguments = ['panel', '--pair-base', BASE.identity, '--pair-expansion', EXP.identity, '--experimental-pair']
            with ExitStack() as stack:
                stack.enter_context(patch('sys.argv', arguments))
                stack.enter_context(patch('sys.stdout', new_callable=io.StringIO))
                app = stack.enter_context(patch.object(host, 'Application'))
                stack.enter_context(patch.object(host, 'LocalServer'))
                if host is desktop:
                    stack.enter_context(patch.object(host, 'gtk_modules'))
                    stack.enter_context(patch.object(host, 'Desktop'))
                else:
                    stack.enter_context(patch.object(host.signal, 'signal'))
                host.main()
                pair = app.call_args.kwargs['pair']
                self.assertEqual((pair.base, pair.expansion), (BASE.identity, EXP.identity))
                actual = Application('/missing', '/unused', pair=pair)
                self.assertEqual(actual._selected_identity, EXP.identity)


if __name__ == '__main__':
    unittest.main()
