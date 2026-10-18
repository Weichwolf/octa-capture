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
from .auto_sense import AutoSenseJob
from .devices import discover
from .preferences import Preferences


class Application:
    def __init__(self, bin_dir, data_dir, port='hw:CARD=OCTACAPTURE,DEV=0,SUBDEV=1'):
        self.bin_dir = Path(bin_dir).resolve()
        self.data_dir = Path(data_dir).expanduser().resolve()
        self.port = port
        self.control_port = port.split(',')[0]
        self.bridge = Bridge(self.bin_dir / 'octa-bridge', port)
        self._operation = threading.RLock()
        self._cache_lock = threading.Lock()
        self._lifecycle = threading.Lock()
        self._cached = None
        self._updated = 0
        self._status = None
        self._status_updated = 0
        self._retry_after = 0
        self._closed = False
        self._last_client = time.monotonic()
        self._transaction = False
        self._auto_job = None
        self._auto_thread = None
        self._selected_identity = None
        self._preferences = Preferences(self.data_dir)

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
                'preferences': self._preferences.document(),
                'capabilities': {'auto_sense': True},
                'auto_sense': self._auto_job.document() if self._auto_job else None}

    def snapshot(self):
        self._touch()
        with self._cache_lock:
            transaction = self._transaction
        if transaction:
            return self._document()
        if not self._operation.acquire(blocking=False):
            return self._document()
        try:
            self._open()
            if time.monotonic() < self._retry_after:
                raise DeviceError('The OCTA-CAPTURE device is not available.')
            if self._cached is None or time.monotonic() - self._updated >= 0.25:
                try:
                    self._sync_selected_device()
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
        self._require_no_auto_sense()
        self._touch()
        with self._operation:
            self._open()
            self._require_no_auto_sense()
            self._sync_selected_device()
            self._remember(self.bridge.request(operation, arguments)['state'])
            return self._document()

    def meters(self):
        self._touch()
        return {'meters': self.bridge.meters()}

    def devices(self):
        self._open()
        devices = []
        for device in discover():
            document = device.document(self.port)
            if self._selected_identity is not None:
                document['selected'] = device.identity == self._selected_identity
            devices.append(document)
        return {'devices': devices}

    def preferences(self):
        self._open()
        return self._preferences.document()

    def set_preferences(self, body):
        self._open()
        return self._preferences.update(body)

    def _bind_device(self, device):
        self.bridge.close()
        self.port = device.port
        self.control_port = self.port.split(',')[0]
        self.bridge = Bridge(self.bin_dir / 'octa-bridge', self.port)
        self._status = None
        self._invalidate()

    def _sync_selected_device(self):
        if self._selected_identity is None:
            return
        device = next((device for device in discover() if device.identity == self._selected_identity), None)
        if device is None:
            self.bridge.close()
            raise DeviceError('The selected OCTA-CAPTURE is disconnected.')
        if device.port != self.port:
            self._bind_device(device)

    def select_device(self, body):
        if type(body) is not dict or set(body) != {'id'} or type(body['id']) is not str:
            raise InvalidControl('Expected a device identity.')
        if not 1 <= len(body['id']) <= 96:
            raise InvalidControl('Invalid device identity.')
        self._require_no_auto_sense()
        with self._operation:
            self._open()
            self._require_no_auto_sense()
            device = next((device for device in discover() if device.identity == body['id']), None)
            if device is None:
                raise InvalidControl('The selected OCTA-CAPTURE is no longer present.')
            self._selected_identity = device.identity
            self._bind_device(device)
            self._auto_job = None
            return self.snapshot()

    def _touch(self):
        with self._cache_lock:
            self._last_client = time.monotonic()

    def disconnect(self):
        if self._auto_job:
            self._auto_job.request('cancel')
        with self._operation:
            self._open()
            self.bridge.close()
            self._invalidate()
        return {'connected': False}

    def expire_idle(self):
        with self._cache_lock:
            idle = time.monotonic() - self._last_client > 15
        if idle and self._auto_job:
            self._auto_job.request('cancel')
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
        self._require_no_auto_sense()
        with self._operation:
            self._open()
            self._require_no_auto_sense()
            self._sync_selected_device()
            self.bridge.close()
            output = self._run([self.bin_dir / 'octa-preset', 'export', self.port], timeout=15)
            if len(output) != 1639:
                raise DeviceError('The device returned an invalid settings file.')
            return output

    def import_preset(self, data):
        if len(data) != 1639:
            raise InvalidControl('An OCTA-CAPTURE settings file must contain exactly 1639 bytes.')
        self._require_no_auto_sense()
        with self._operation:
            self._open()
            self._require_no_auto_sense()
            self._sync_selected_device()
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
        self._action_arguments(body)
        self._require_no_auto_sense()
        with self._operation:
            self._open()
            self._require_no_auto_sense()
            self._sync_selected_device()
            arguments = self._action_arguments(body)
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

    def _require_no_auto_sense(self):
        if self._auto_job:
            self._auto_job.require_available()

    def start_auto_sense(self, body):
        if type(body) is not dict or set(body) != {'channels'}:
            raise InvalidControl('Expected the selected Auto-Sens channels.')
        channels = integer(body['channels'], 1, 255)
        self._touch()
        self._require_no_auto_sense()
        with self._operation:
            self._open()
            self._require_no_auto_sense()
            self._sync_selected_device()
            state = self.snapshot()['state']
            if bytes.fromhex(state['payload_hex'])[2] == 3 and channels & 0xf0:
                raise InvalidControl('Only inputs 1–4 are available at 192 kHz.')
            directory = self._directory()
            self.bridge.close()
            with self._lifecycle:
                self._open()
                job = AutoSenseJob([self.bin_dir / 'octa-auto-sense', '--interactive', channels,
                                    60000, 'finish', directory / 'recovery.syx', self.port], directory, channels)
                self._auto_job = job
                self._set_transaction(True)
                self._auto_thread = threading.Thread(target=self._run_auto_sense, args=(job,),
                                                     name='auto-sens-operation', daemon=True)
                self._auto_thread.start()
            return self._document()

    def _run_auto_sense(self, job):
        with self._operation:
            try:
                job.run()
            finally:
                self._invalidate(preserve=True)
                self._set_transaction(False)

    def stop_auto_sense(self, body):
        if type(body) is not dict or set(body) != {'action'} or body['action'] not in ('finish', 'cancel'):
            raise InvalidControl('Expected Auto-Sens finish or cancel.')
        self._touch()
        if self._auto_job is None:
            raise InvalidControl('Auto-Sens is not running.')
        self._auto_job.request(body['action'])
        return self._document()

    def close(self):
        with self._lifecycle:
            self._closed = True
            job, thread = self._auto_job, self._auto_thread
        if job:
            job.request('cancel')
        if thread:
            thread.join()
        with self._operation:
            self.bridge.close()
