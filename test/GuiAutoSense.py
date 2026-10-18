# SPDX-License-Identifier: GPL-2.0-only
"""Exercise interactive process readiness, finish/cancel and recovery reporting."""

from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from gui.application import Application
from gui.auto_sense import AutoSenseJob
from gui.backend import DeviceError, InvalidControl


def wait_for(predicate):
    deadline = time.monotonic() + 4
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError('Timed out waiting for interactive Auto-Sens.')


class Interactive(unittest.TestCase):
    def check_job(self, mode, action, expected, early=False):
        with tempfile.TemporaryDirectory() as directory:
            job = AutoSenseJob([sys.executable, Path(__file__).with_name('AutoSensePeer.py'), mode],
                               Path(directory), 1)
            if early:
                job.request(action)
            thread = threading.Thread(target=job.run)
            thread.start()
            try:
                with self.assertRaises(DeviceError):
                    job.require_available()
                if not early and mode not in ('malformed', 'launch-error'):
                    wait_for(lambda: job.document()['state'] == 'running')
                    job.request(action)
                thread.join(timeout=5)
                self.assertFalse(thread.is_alive())
                self.assertEqual(job.document()['state'], expected)
                self.assertFalse(job.document()['active'])
                job.require_available()
            finally:
                job.request('cancel')
                thread.join(timeout=5)

    def test_finish(self):
        self.check_job('normal', 'finish', 'completed')

    def test_cancel(self):
        self.check_job('normal', 'cancel', 'cancelled')

    def test_early_cancel_waits_for_installed_handlers(self):
        self.check_job('normal', 'cancel', 'cancelled', early=True)

    def test_early_finish_waits_for_installed_handlers(self):
        self.check_job('normal', 'finish', 'completed', early=True)

    def test_cancel_does_not_claim_failed_recovery_succeeded(self):
        self.check_job('recovery-error', 'cancel', 'failed')

    def test_malformed_event_cancels_and_reports_failure(self):
        self.check_job('malformed', 'cancel', 'failed')

    def test_launch_error(self):
        self.check_job('launch-error', 'cancel', 'failed')

    def test_application_keeps_ctrl_exclusive_and_polling_responsive(self):
        self.check_application('cancel')

    def test_client_disappearance_cancels_auto_sense(self):
        self.check_application('idle')

    def test_server_shutdown_cancels_auto_sense(self):
        self.check_application('close')

    def test_immediate_shutdown_waits_for_cancelled_child(self):
        self.check_application('close-early')

    def check_application(self, stop):
        with tempfile.TemporaryDirectory() as directory:
            application = Application('/missing/bin', directory)
            application.bridge = Mock()
            application.bridge.meters.return_value = None
            application.bridge.request.return_value = {'state': {'payload_hex': '000001'}}
            application._clock_status = Mock(return_value=None)
            original_factory = AutoSenseJob

            def factory(_command, operation_dir, channels):
                return original_factory([sys.executable, Path(__file__).with_name('AutoSensePeer.py'), 'normal'],
                                        operation_dir, channels)

            with patch('gui.application.AutoSenseJob', side_effect=factory):
                result = application.start_auto_sense({'channels': 1})
            try:
                self.assertTrue(result['busy'])
                self.assertTrue(result['capabilities']['auto_sense'])
                if stop != 'close-early':
                    wait_for(lambda: application._auto_job.document()['state'] == 'running')
                self.assertTrue(application.snapshot()['busy'])
                with self.assertRaises(DeviceError):
                    application.change({'kind': 'preamp', 'channel': 0, 'parameter': 'attack', 'value': 0})
                with self.assertRaises(DeviceError):
                    application.start_auto_sense({'channels': 1})
                with self.assertRaises(DeviceError):
                    application.export_preset()
                if stop == 'idle':
                    application._last_client -= 16
                    application.expire_idle()
                elif stop in ('close', 'close-early'):
                    application.close()
                else:
                    application.stop_auto_sense({'action': 'cancel'})
                application._auto_thread.join(timeout=5)
                self.assertEqual(application._auto_job.document()['state'], 'cancelled')
                if stop not in ('close', 'close-early'):
                    self.assertFalse(application.snapshot()['busy'])
            finally:
                application.close()

    def test_invalid_requests_never_open_device(self):
        application = Application('/missing/bin', '/unused')
        application.bridge = Mock()
        for body in ({}, {'channels': True}, {'channels': 0}, {'channels': 256},
                     {'channels': 1, 'command': 'id'}, {'channels': '1'}):
            with self.subTest(body=body), self.assertRaises(InvalidControl):
                application.start_auto_sense(body)
        for body in ({}, {'action': 'kill'}, {'action': 'finish', 'extra': 0}, {'action': 'cancel'}):
            with self.subTest(body=body), self.assertRaises(InvalidControl):
                application.stop_auto_sense(body)
        application.bridge.request.assert_not_called()

    def test_calibration_started_while_request_waits_for_operation_lock(self):
        operations = (
            ('change', {'kind': 'preamp', 'channel': 0, 'parameter': 'attack', 'value': 0}),
            ('export_preset', None),
            ('import_preset', bytes(1639)),
            ('action', {'action': 'initialize', 'target': 'reverb'}),
            ('select_device', {'id': 'serial:UNIT'}),
            ('start_auto_sense', {'channels': 1}),
        )
        for method, body in operations:
            with self.subTest(method=method):
                application = Application('/missing/bin', '/unused')
                application.bridge = Mock()
                checked = threading.Event()
                errors = []
                require_available = application._require_no_auto_sense

                def preflight():
                    require_available()
                    checked.set()

                def request():
                    try:
                        operation = getattr(application, method)
                        operation() if body is None else operation(body)
                    except Exception as error:
                        errors.append(error)

                with patch.object(application, '_require_no_auto_sense', side_effect=preflight), \
                        patch.object(application, '_sync_selected_device',
                                     side_effect=AssertionError('Device I/O during calibration')) as sync:
                    thread = threading.Thread(target=request, daemon=True)
                    try:
                        with application._operation:
                            thread.start()
                            self.assertTrue(checked.wait(timeout=2), 'Request did not finish its initial guard')
                            # Auto-Sens starts after the early guard, before this request acquires the lock.
                            application._auto_job = Mock()
                            application._auto_job.require_available.side_effect = DeviceError('Auto-Sens is active.')
                        thread.join(timeout=2)
                        self.assertFalse(thread.is_alive())
                        self.assertEqual(len(errors), 1)
                        self.assertIsInstance(errors[0], DeviceError)
                        sync.assert_not_called()
                        application.bridge.close.assert_not_called()
                        application.bridge.request.assert_not_called()
                    finally:
                        thread.join(timeout=2)


if __name__ == '__main__':
    unittest.main()
