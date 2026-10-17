# SPDX-License-Identifier: GPL-2.0-only
"""Serialize real device controls through the thread-confined C++ MIDI owner."""

from __future__ import annotations

from collections import deque
import json
import math
from pathlib import Path
import queue
import subprocess
import threading
import time


class DeviceError(RuntimeError):
    """The device process failed or could not verify a request."""


class InvalidControl(ValueError):
    """A browser request does not describe a supported typed control."""


PREAMP_PARAMETERS = {
    'phantom': (0, 1), 'low-cut': (1, 1), 'phase': (2, 1), 'hi-z': (3, 1),
    'sensitivity': (4, 100), 'stereo-link': (5, 1), 'compressor': (6, 1),
    'gate': (7, 50), 'attack': (8, 25), 'release': (9, 45),
    'threshold': (10, 50), 'ratio': (11, 8), 'gain': (12, 74),
}
MIXER_PARAMETERS = {'link': 0, 'mono': 1, 'solo': 2, 'mute': 3, 'pan': 4, 'volume': 8, 'send': 14}
MASTER_PARAMETERS = {'link': 0, 'volume': 1, 'return': 7, 'io-link': 13}


def integer(value, minimum, maximum):
    if type(value) is not int or not minimum <= value <= maximum:
        raise InvalidControl(f'Expected an integer from {minimum} through {maximum}.')
    return value


def choice(value, values):
    if type(value) is not str or value not in values:
        raise InvalidControl('Unknown control selection.')
    return values[value]


def fields(body, names):
    if set(body) != {'kind', *names}:
        raise InvalidControl('Missing or unexpected control fields.')


def channel_control(body):
    kind = body['kind']
    fields(body, ('source', 'bus', 'channel', 'parameter', 'value'))
    source = choice(body['source'], {'input': 0, 'playback': 1})
    bus = integer(body['bus'], 0, 3)
    channel = integer(body['channel'], 0, 9 if kind == 'mixer' else 1)
    names = MIXER_PARAMETERS if kind == 'mixer' else MASTER_PARAMETERS
    parameter = choice(body['parameter'], names)
    maximum = 0x7fffff if body['parameter'] in ('volume', 'send', 'return') else 1
    if kind == 'mixer' and parameter == 4:
        maximum = 0x8000
    value = integer(body['value'], 0, maximum)
    if kind == 'mixer':
        if parameter in (0, 1) and channel % 2:
            raise InvalidControl('Pair controls require their first channel.')
        if parameter == 14 and (source != 0 or bus != 0):
            raise InvalidControl('Reverb sends belong to input mixer A.')
    else:
        if parameter != 1 and channel != 0:
            raise InvalidControl('Master switches require channel zero.')
        if (parameter == 7 and (source != 0 or bus != 0)) or (parameter == 13 and source != 0):
            raise InvalidControl('Unsupported master control.')
    return kind, [source, bus, channel, parameter, value]


def validate_control(body):
    if type(body) is not dict:
        raise InvalidControl('Expected a control object.')
    kind = body.get('kind')
    if kind == 'preamp':
        fields(body, ('channel', 'parameter', 'value'))
        channel = integer(body['channel'], 0, 7)
        parameter, maximum = choice(body['parameter'], PREAMP_PARAMETERS)
        value = integer(body['value'], 0, maximum)
        if (parameter == 3 and channel >= 2) or (parameter == 5 and channel % 2):
            raise InvalidControl('Unsupported input or stereo-pair selector.')
        return kind, [channel, parameter, value]
    if kind in ('mixer', 'master'):
        return channel_control(body)
    if kind == 'reverb':
        fields(body, ('parameter', 'bank', 'value'))
        parameter = choice(body['parameter'], {'type': 0, 'pre-delay': 1, 'time': 2})
        bank = integer(body['bank'], 0 if parameter == 0 else 1, 0 if parameter == 0 else 4)
        value = integer(body['value'], 0, (4, 12, 49)[parameter])
        return kind, [parameter, bank, value]
    if kind == 'route':
        fields(body, ('pair', 'selection'))
        return kind, [integer(body['pair'], 0, 4), integer(body['selection'], 0, 8)]
    raise InvalidControl('Unknown control kind.')


class Bridge:
    """One subprocess, one serialized command, bounded replies and latest meter frame."""

    def __init__(self, binary: Path, port='hw:CARD=OCTACAPTURE,DEV=0,SUBDEV=1'):
        self.binary = Path(binary).resolve()
        self.port = port
        self.lock = threading.RLock()
        self._data_lock = threading.Lock()
        self._process = None
        self._reader = None
        self._ready = threading.Event()
        self._replies = queue.Queue(maxsize=1)
        self._failure = None
        self._serial = 0
        self._meter = None
        self._meter_time = None
        self._pcm = None
        self._pcm_time = None
        self._meter_history = deque(maxlen=64)
        self._meter_serial = 0

    def _read(self, process):
        try:
            while True:
                line = process.stdout.readline(65537)
                if not line:
                    break
                if len(line) > 65536 or not line.endswith(b'\n'):
                    raise DeviceError('Oversized or incomplete device response.')
                response = json.loads(line)
                if type(response) is not dict:
                    raise DeviceError('Invalid device response.')
                if response.get('event') == 'ready':
                    self._ready.set()
                elif response.get('event') == 'meters':
                    self._update_meter(response)
                elif response.get('event') == 'pcm-meters':
                    self._update_pcm(response.get('pcm'))
                else:
                    self._replies.put_nowait(response)
        except (OSError, ValueError, queue.Full, DeviceError) as error:
            self._failure = str(error)
        finally:
            self._failure = self._failure or 'Device connection closed.'
            self._ready.set()
            try:
                self._replies.put_nowait({'id': 0, 'ok': False})
            except queue.Full:
                pass

    def _update_meter(self, response):
        levels, decibels = response.get('input_levels'), response.get('input_db')
        if (type(levels) is not list or type(decibels) is not list or
                len(levels) != 8 or len(decibels) != 8 or
                any(type(value) is not int or not 0 <= value <= 16383 for value in levels) or
                any(type(value) not in (int, float) or not math.isfinite(value) or
                    not -100 <= value <= 0 for value in decibels)):
            raise DeviceError('Invalid meter response.')
        with self._data_lock:
            self._meter = {'input_levels': levels, 'input_db': decibels}
            self._meter_time = time.monotonic()
            self._record_meter('input', levels, 0, self._meter_time)

    def _record_meter(self, source, levels, rate, received):
        # Caller holds _data_lock. IDs survive backend restarts so an open page
        # can discard repeated history without losing a short peak notification.
        self._meter_serial += 1
        self._meter_history.append({'id': self._meter_serial, 'source': source,
                                    'levels': levels, 'rate': rate, 'received': received})

    def _start(self):
        if self._process is not None and self._process.poll() is None and self._failure is None:
            return
        self._stop()
        self._ready.clear()
        self._failure = None
        self._replies = queue.Queue(maxsize=1)
        try:
            self._process = subprocess.Popen([str(self.binary), self.port], stdin=subprocess.PIPE,
                                             stdout=subprocess.PIPE)
        except OSError as error:
            raise DeviceError('Could not start the device backend.') from error
        self._reader = threading.Thread(target=self._read, args=(self._process,), daemon=True)
        self._reader.start()
        if not self._ready.wait(5) or self._failure:
            self._stop()
            raise DeviceError('The OCTA-CAPTURE device is not available.')
        self._exchange('meters', [1])

    def _update_pcm(self, pcm):
        if (type(pcm) is not dict or set(pcm) != {'enabled', 'idle_stream', 'error', 'capture', 'playback'} or
                type(pcm['enabled']) is not bool or type(pcm['idle_stream']) is not bool or
                type(pcm['error']) is not int or not 0 <= pcm['error'] <= 4095):
            raise DeviceError('Invalid PCM meter response.')
        for name, maximum in (('capture', 12), ('playback', 10)):
            direction = pcm[name]
            if (type(direction) is not dict or
                    set(direction) != {'rate', 'channels', 'age_ms', 'sequence', 'fresh', 'peaks'} or
                    any(type(direction[key]) is not int or not 0 <= direction[key] <= 0xffffffff
                        for key in ('rate', 'channels', 'age_ms', 'sequence')) or
                    type(direction['fresh']) is not bool or type(direction['peaks']) is not list or
                    len(direction['peaks']) != maximum):
                raise DeviceError('Invalid PCM stream response.')
            rate, channels = direction['rate'], direction['channels']
            unseen = rate == channels == 0 and direction['age_ms'] == 0xffffffff and not direction['fresh']
            if not unseen and (rate not in (44100, 48000, 96000, 192000) or
                               channels != (4 if rate == 192000 else maximum) or
                               direction['fresh'] != (direction['age_ms'] <= 500)):
                raise DeviceError('Inconsistent PCM stream response.')
            for index, peak in enumerate(direction['peaks']):
                if (type(peak) is not int or not 0 <= peak <= 0x7fffff00 or peak % 256 or
                        (index >= channels and peak != 0)):
                    raise DeviceError('Invalid PCM peak response.')
        with self._data_lock:
            self._pcm = pcm
            self._pcm_time = time.monotonic()
            if pcm['enabled']:
                for source in ('capture', 'playback'):
                    direction = pcm[source]
                    if direction['fresh']:
                        self._record_meter(source, [peak >> 17 for peak in direction['peaks']],
                                           direction['rate'], self._pcm_time)

    def _stop(self):
        process = self._process
        if process is None:
            self._clear_meters()
            return
        try:
            if process.stdin:
                process.stdin.close()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.terminate()
                process.wait(timeout=3)
        except (OSError, subprocess.TimeoutExpired):
            process.kill()
            process.wait(timeout=2)
        finally:
            if self._reader:
                self._reader.join(timeout=2)
            if process.stdout:
                process.stdout.close()
            self._process = None
            self._reader = None
            self._clear_meters()

    def _clear_meters(self):
        with self._data_lock:
            self._meter = None
            self._meter_time = None
            self._pcm = None
            self._pcm_time = None
            self._meter_history.clear()

    def _exchange(self, operation, arguments):
        self._serial = self._serial % 0xffffffff + 1
        command = ' '.join(map(str, [self._serial, operation, *arguments])) + '\n'
        try:
            self._process.stdin.write(command.encode('ascii'))
            self._process.stdin.flush()
            response = self._replies.get(timeout=6)
        except (OSError, queue.Empty) as error:
            self._stop()
            raise DeviceError('The device did not answer.') from error
        if response.get('id') != self._serial or response.get('ok') is not True:
            self._stop()
            raise DeviceError('The device could not verify this change.')
        return response

    def request(self, operation, arguments=()):
        if operation not in ('state', 'meters', 'preamp', 'mixer', 'master', 'reverb', 'route'):
            raise InvalidControl('Unsupported backend operation.')
        if any(type(value) is not int or not 0 <= value <= 0xffffffff for value in arguments):
            raise InvalidControl('Invalid backend argument.')
        with self.lock:
            self._start()
            return self._exchange(operation, arguments)

    def change(self, control):
        operation, arguments = validate_control(control)
        return self.request(operation, arguments)['state']

    def meters(self):
        with self._data_lock:
            if self._meter is None and self._pcm is None:
                return None
            now = time.monotonic()
            result = ({**self._meter, 'age_ms': int((now - self._meter_time) * 1000)}
                      if self._meter else {'input_levels': [0] * 8, 'input_db': [-96] * 8,
                                          'age_ms': 0xffffffff})
            if self._pcm is not None:
                result['pcm'] = {**self._pcm,
                                 'received_age_ms': int((now - self._pcm_time) * 1000)}
            result['history'] = [{'id': frame['id'], 'source': frame['source'],
                                  'levels': frame['levels'], 'rate': frame['rate'],
                                  'age_ms': int((now - frame['received']) * 1000)}
                                 for frame in self._meter_history]
            return result

    def close(self):
        with self.lock:
            self._stop()
