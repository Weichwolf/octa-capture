# SPDX-License-Identifier: GPL-2.0-only
"""Lifecycle for the recovery-aware interactive C++ Auto-Sens process."""

from __future__ import annotations

import json
import signal
import subprocess
import threading

from .backend import DeviceError, InvalidControl


class ObservedAutoSense:
    """Track observed device activity without claiming a pre-calibration backup."""

    def __init__(self):
        self._lock = threading.RLock()
        self._document = None

    def reset(self):
        with self._lock:
            self._document = None

    def observe(self, activity):
        with self._lock:
            current = self._document
            if current and current['state'] == 'failed':
                return dict(current)
            if activity and activity['active']:
                if not current or current['state'] not in ('finishing', 'cancelling'):
                    self._document = {'state': 'running', 'channels': activity['channels'],
                                      'active': True, 'origin': 'device', 'error': None}
            elif current and current['state'] == 'running':
                if activity is None:
                    self.fail('Device activity became unknown.')
                else:
                    self.complete('finish', False)
            return dict(self._document) if self._document else None

    def require_available(self):
        with self._lock:
            if self._document and self._document['state'] == 'failed':
                raise DeviceError(self._document['error'])
            if self._document and self._document['active']:
                raise DeviceError('Auto-Sens is running on the device.')

    def begin(self, action):
        with self._lock:
            if not self._document or self._document['state'] not in ('running', 'completed', 'cancelled'):
                raise InvalidControl('No active device Auto-Sens operation was observed.')
            self._document = {**self._document, 'active': True,
                              'state': 'cancelling' if action == 'cancel' else 'finishing'}

    def complete(self, action, stop_sent):
        with self._lock:
            self._document = {**self._document,
                              'state': 'cancelled' if action == 'cancel' and stop_sent else 'completed',
                              'active': False, 'error': None}

    def fail(self, error):
        with self._lock:
            self._document = {**self._document, 'state': 'failed', 'active': False,
                              'error': str(error) + ' Completion is unverified. Finish or cancel '
                                       'on the device, then reopen this panel.'}


class AutoSenseJob:
    def __init__(self, command, directory, channels):
        self.channels = channels
        self._lock = threading.RLock()
        self._state = 'starting'
        self._prepared = False
        self._requested = None
        self._sent = None
        self._final_action = None
        self._protocol_error = False
        self._error = None
        self._log_path = directory / 'auto-sense.log'
        self._log = self._log_path.open('xb')
        self._log_path.chmod(0o600)
        try:
            self._process = subprocess.Popen(list(map(str, command)), stdout=subprocess.PIPE, stderr=self._log)
        except BaseException:
            self._log.close()
            raise

    def document(self):
        with self._lock:
            return {'state': self._state, 'channels': self.channels,
                    'active': self._state not in ('completed', 'cancelled', 'failed'),
                    'error': self._error}

    def request(self, action):
        with self._lock:
            if not self.document()['active']:
                return self.document()
            if self._requested != 'cancel':
                self._requested = action
            self._state = 'cancelling' if self._requested == 'cancel' else 'finishing'
            self._signal()
            return self.document()

    def _signal(self):
        # The prepared event is emitted after handlers are installed, before
        # opening MIDI. Never race a signal against the child's default handler.
        if not self._prepared or not self._requested or self._sent == self._requested:
            return
        try:
            self._process.send_signal(signal.SIGTERM if self._requested == 'cancel' else signal.SIGUSR1)
            self._sent = self._requested
        except ProcessLookupError:
            pass

    def _read(self):
        try:
            while line := self._process.stdout.readline(4097):
                if len(line) > 4096 or not line.endswith(b'\n'):
                    raise ValueError('Invalid Auto-Sens event size.')
                message = json.loads(line)
                with self._lock:
                    if message == {'event': 'auto-sense-prepared'}:
                        self._prepared = True
                        self._signal()
                    elif message == {'event': 'auto-sense-started'}:
                        if not self._requested:
                            self._state = 'running'
                    elif message.get('action') in ('finish', 'cancel'):
                        self._final_action = message['action']
                    else:
                        raise ValueError('Unexpected Auto-Sens event.')
        except (ValueError, TypeError, AttributeError, OSError):
            with self._lock:
                self._protocol_error = True
            self.request('cancel')

    def run(self):
        reader = threading.Thread(target=self._read, name='auto-sens-events', daemon=True)
        reader.start()
        try:
            try:
                self._process.wait(timeout=75)
            except subprocess.TimeoutExpired:
                self.request('cancel')
                # The child needs time to close CTRL, reopen and verify rollback.
                try:
                    self._process.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    self._process.kill()
                    self._process.wait(timeout=5)
            reader.join(timeout=5)
            self._log.close()
            with self._log_path.open('rb') as log:
                errors = log.read(16384)
            with self._lock:
                if self._process.returncode == 0 and self._final_action == 'finish' and not self._protocol_error:
                    self._state = 'completed'
                elif (self._process.returncode == 130 and self._requested == 'cancel' and
                      (b'Original raw state restored and verified.' in errors or
                       b'Original raw state unchanged; no device write attempted.' in errors) and
                      not self._protocol_error):
                    self._state = 'cancelled'
                else:
                    self._state = 'failed'
                    self._error = 'Auto-Sens did not complete. Check the recovery files before continuing.'
        except (OSError, subprocess.SubprocessError):
            self.request('cancel')
            try:
                self._process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait(timeout=5)
            with self._lock:
                self._state = 'failed'
                self._error = 'Auto-Sens failed. Check the recovery files before continuing.'
        finally:
            self._process.stdout.close()
            self._log.close()

    def require_available(self):
        if self.document()['active']:
            raise DeviceError('Finish or cancel Auto-Sens before changing other settings.')
