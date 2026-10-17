# SPDX-License-Identifier: GPL-2.0-only
"""GUI operations backed by verified C++ commands and private recovery files."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time

from .backend import Bridge, DeviceError, InvalidControl, integer, validate_control


class Application:
    def __init__(self, bin_dir, data_dir, port='hw:CARD=OCTACAPTURE,DEV=0,SUBDEV=1'):
        self.bin_dir = Path(bin_dir).resolve()
        self.data_dir = Path(data_dir).expanduser().resolve()
        self.port = port
        self.control_port = port.split(',')[0]
        self.bridge = Bridge(self.bin_dir / 'octa-bridge', port)
        self._operation = threading.RLock()
        self._cache_lock = threading.Lock()
        self._cached = None
        self._updated = 0
        self._status = None
        self._status_updated = 0
        self._retry_after = 0
        self._closed = False
        self._last_client = time.monotonic()
        self._transaction = False

    def _open(self):
        if self._closed:
            raise DeviceError('The control panel is closing.')

    def _clock_status(self):
        if time.monotonic() - self._status_updated < 1:
            return self._status
        self._status_updated = time.monotonic()
        try:
            # ALSA status is separate from the exclusive CTRL MIDI endpoint.
            output = subprocess.run([str(self.bin_dir / 'octa-status'), self.control_port], capture_output=True,
                                    check=True, timeout=2)
            self._status = json.loads(output.stdout)
        except (OSError, ValueError, subprocess.SubprocessError):
            self._status = None
        return self._status

    def _remember(self, state):
        with self._cache_lock:
            self._cached = state
            self._updated = time.monotonic()
        return state

    def _document(self, busy=None):
        with self._cache_lock:
            state = self._cached
            transaction = self._transaction
        if state is None:
            raise DeviceError('The OCTA-CAPTURE device is not available.')
        return {'connected': True, 'busy': transaction if busy is None else busy, 'state': state,
                'status': self._status, 'meters': self.bridge.meters(),
                'capabilities': {'auto_sense': False}}

    def snapshot(self):
        self._touch()
        if not self._operation.acquire(blocking=False):
            return self._document()
        try:
            self._open()
            if time.monotonic() < self._retry_after:
                raise DeviceError('The OCTA-CAPTURE device is not available.')
            if self._cached is None or time.monotonic() - self._updated >= 0.25:
                try:
                    self._remember(self.bridge.request('state')['state'])
                except DeviceError:
                    with self._cache_lock:
                        self._cached = None
                    self._retry_after = time.monotonic() + 1
                    raise
            self._clock_status()
            return self._document()
        finally:
            self._operation.release()

    def change(self, body):
        operation, arguments = validate_control(body)
        self._touch()
        with self._operation:
            self._open()
            self._remember(self.bridge.request(operation, arguments)['state'])
            return self._document()

    def meters(self):
        self._touch()
        return {'meters': self.bridge.meters()}

    def _touch(self):
        with self._cache_lock:
            self._last_client = time.monotonic()

    def disconnect(self):
        with self._operation:
            self._open()
            self.bridge.close()
            self._invalidate()
        return {'connected': False}

    def expire_idle(self):
        with self._cache_lock:
            idle = time.monotonic() - self._last_client > 15
        if idle and self._operation.acquire(blocking=False):
            try:
                if not self._closed:
                    self.bridge.close()
                    self._invalidate()
            finally:
                self._operation.release()

    def _run(self, arguments, timeout=75):
        with subprocess.Popen(list(map(str, arguments)), stdout=subprocess.PIPE, stderr=subprocess.PIPE) as process:
            try:
                output, errors = process.communicate(timeout=timeout)
            except BaseException:
                process.terminate()
                try:
                    process.communicate(timeout=20)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.communicate(timeout=5)
                raise
        if process.returncode:
            print(errors.decode(errors='replace'), file=sys.stderr)
            raise DeviceError('The device could not complete this operation. Check the recovery file.')
        return output

    def _directory(self):
        self.data_dir.mkdir(parents=True, mode=0o700, exist_ok=True)
        return Path(tempfile.mkdtemp(prefix='operation-', dir=self.data_dir))

    def export_preset(self):
        with self._operation:
            self._open()
            self.bridge.close()
            output = self._run([self.bin_dir / 'octa-preset', 'export', self.port], timeout=15)
            if len(output) != 1639:
                raise DeviceError('The device returned an invalid settings file.')
            return output

    def import_preset(self, data):
        if len(data) != 1639:
            raise InvalidControl('An OCTA-CAPTURE settings file must contain exactly 1639 bytes.')
        with self._operation:
            self._open()
            directory = self._directory()
            uploaded = directory / 'settings.octa'
            with uploaded.open('xb') as output:
                uploaded.chmod(0o600)
                output.write(data)
                output.flush()
                os.fsync(output.fileno())
            # Validate the complete file before interrupting the live meter connection.
            validation = subprocess.run([str(self.bin_dir / 'octa-preset'), 'inspect', str(uploaded)],
                                        capture_output=True, timeout=5, check=False)
            if validation.returncode:
                uploaded.unlink()
                directory.rmdir()
                raise InvalidControl('Invalid OCTA-CAPTURE settings file.')
            self._set_transaction(True)
            try:
                self.bridge.close()
                self._run([self.bin_dir / 'octa-preset', 'apply', uploaded,
                           directory / 'recovery.syx', self.port])
            finally:
                self._set_transaction(False)
                self._invalidate(preserve=True)
            return self.snapshot()

    def _set_transaction(self, value):
        with self._cache_lock:
            self._transaction = value

    def _invalidate(self, preserve=False):
        with self._cache_lock:
            if not preserve:
                self._cached = None
            self._updated = 0
        self._retry_after = 0
        self._status_updated = 0

    def action(self, body):
        arguments = self._action_arguments(body)
        with self._operation:
            self._open()
            directory = self._directory()
            arguments = [str(value).replace('{recovery}', str(directory / 'recovery.syx'))
                         for value in arguments]
            self._set_transaction(True)
            try:
                self.bridge.close()
                self._run(arguments)
            finally:
                self._set_transaction(False)
                self._invalidate(preserve=True)
            return self.snapshot()

    def _action_arguments(self, body):
        if type(body) is not dict or type(body.get('action')) is not str:
            raise InvalidControl('Expected a device operation.')
        action = body['action']
        executable = self.bin_dir / 'octa-preset'
        if action == 'initialize' and set(body) == {'action', 'target'}:
            if body['target'] not in ('all', 'preamps', 'direct-mix', 'reverb', 'patchbay'):
                raise InvalidControl('Unknown initialization group.')
            return [executable, action, body['target'], '{recovery}', self.port]
        if action == 'routing' and set(body) == {'action', 'preset'}:
            if body['preset'] not in ('multi-monitor', 'playback'):
                raise InvalidControl('Unknown routing preset.')
            return [executable, action, body['preset'], '{recovery}', self.port]
        if action in ('direct-out', 'memory-store', 'memory-load') and set(body) == {'action'}:
            return [executable, action, '{recovery}', self.port]
        if action == 'sample-rate' and set(body) == {'action', 'rate'}:
            rate = integer(body['rate'], 44100, 192000)
            if rate not in (44100, 48000, 96000, 192000):
                raise InvalidControl('Unsupported sample rate.')
            pcm = self.control_port + (',DEV=0' if 'CARD=' in self.control_port else ',0')
            return [self.bin_dir / 'octa-audio-check', rate, 'prepare-capture', pcm]
        raise InvalidControl('Unknown device operation or fields.')

    def close(self):
        with self._operation:
            self._closed = True
            self.bridge.close()
