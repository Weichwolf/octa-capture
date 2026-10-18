#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Exclusive-device Mixer A audio checks via return 11/12, with physical outputs silenced."""

import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from gui.backend import Bridge

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pcm_checks import geometry_valid, owned_parameters

RATES = (44100, 48000, 96000)
UNITY = 0x200000
PAN_RIGHT = 0x8000
TAG = 1024  # -78 dBFS before mixer gain; no test signal reaches physical outputs.
PCM_DIRECTORY = Path('/proc/asound/OCTACAPTURE')
PATTERN_PERIOD = 2047


def next_pattern(state):
    # PRBS11: x^11 + x^9 + 1, with all nonzero eleven-bit states.
    return ((state << 1) & PATTERN_PERIOD) | (((state >> 10) ^ (state >> 8)) & 1)


def pattern_cycle(channel):
    state, frames = 1, []
    for _ in range(PATTERN_PERIOD):
        frames.append(b''.join((state - TAG if index == channel else 0).to_bytes(3, 'little', signed=True)
                               for index in range(10)))
        state = next_pattern(state)
    require(state == 1, 'Invalid digital test-sequence period.')
    return b''.join(frames)


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


class AudioMismatch(RuntimeError):
    """A valid, completed stream contains unexpected digital samples."""


class Check:
    def __init__(self, directory, keep_going=False, period=1024, buffer=8192):
        self.directory = directory.resolve()
        self.bridge = Bridge(self.directory / 'octa-bridge')
        self.rate = None
        self.cases = 0
        self.keep_going = keep_going
        self.failures = []
        self.period = period
        self.buffer = buffer
        self.last_parameters = {}

    def audio_failure(self, label, message):
        if not self.keep_going:
            raise AudioMismatch(f'{label}: {message}')
        self.failures.append({'rate_hz': self.rate, 'case': label, 'error': str(message)})
        print(json.dumps({'failure': self.failures[-1]}), flush=True)

    def run(self, name, *arguments):
        return subprocess.run([str(self.directory / name), *map(str, arguments)],
                              check=True, capture_output=True, timeout=25).stdout

    def json(self, name, *arguments):
        return json.loads(self.run(name, *arguments))

    def state(self):
        return self.bridge.request('state')['state']

    def master(self, source, bus, channel, parameter, value):
        return self.bridge.change({'kind': 'master', 'source': source, 'bus': bus,
                                   'channel': channel, 'parameter': parameter, 'value': value})

    def strip(self, channel, parameter, value):
        return self.bridge.change({'kind': 'mixer', 'source': 'playback', 'bus': 0,
                                   'channel': channel, 'parameter': parameter, 'value': value})

    def silence_outputs(self):
        for source in ('input', 'playback'):
            for channel in range(2):
                self.master(source, 1, channel, 'volume', 0)
        state = self.state()
        require(all(value == 0 for source in ('input', 'playback')
                    for value in state['mixers'][1][source + '_master']['volume']), 'Mixer B is not silent.')
        for pair in range(5):
            self.bridge.change({'kind': 'route', 'pair': pair, 'selection': 1})
        self.verify_silence()

    def verify_silence(self):
        state = self.state()
        require(state['output_routes'] == [1] * 5, 'Physical outputs must use silent Mixer B.')
        require(all(value == 0 for source in ('input', 'playback')
                    for value in state['mixers'][1][source + '_master']['volume']), 'Mixer B must remain silent.')

    def prepare_return(self):
        self.master('input', 0, 0, 'io-link', 0)
        for channel in range(2):
            self.master('input', 0, channel, 'volume', 0)
            self.master('playback', 0, channel, 'volume', UNITY)
        self.master('input', 0, 0, 'return', 0)
        for channel in range(0, 10, 2):
            self.strip(channel, 'link', 0)
            self.strip(channel, 'mono', 0)
        for channel in range(10):
            for parameter, value in (('mute', 0), ('solo', 0), ('pan', 0), ('volume', UNITY)):
                self.strip(channel, parameter, value)
        self.verify_silence()

    def measure(self, channel, amplitude, pattern=False, duration=2):
        self.verify_silence()
        rate = self.rate
        sample = (pattern_cycle(channel) if pattern else
                  b''.join((amplitude if index == channel else 0).to_bytes(3, 'little', signed=True)
                           for index in range(10)))
        processes = []
        with os.fdopen(os.memfd_create('octa-quiet-mixer-test', os.MFD_CLOEXEC), 'w+b') as generated, \
                os.fdopen(os.memfd_create('octa-mixer-return', os.MFD_CLOEXEC), 'w+b') as recorded, \
                tempfile.TemporaryFile() as capture_errors, tempfile.TemporaryFile() as playback_errors:
            # Start/end on digital zero; keep all generated/captured audio in RAM.
            quiet = bytes(30) * (rate // 4)
            size = (rate * duration - 2 * (rate // 4)) * 30
            payload = (sample * ((size + len(sample) - 1) // len(sample)))[:size]
            generated.write(quiet + payload + quiet)
            generated.flush()
            generated.seek(0)
            try:
                common = ['-q', '-D', 'hw:CARD=OCTACAPTURE,DEV=0', '-r', str(rate), '-f', 'S24_3LE',
                          f'--period-size={self.period}', f'--buffer-size={self.buffer}', '-d', str(duration), '-t', 'raw']
                capture = subprocess.Popen(['arecord', *common, '-c12'], stdout=recorded, stderr=capture_errors)
                processes.append(capture)
                playback = subprocess.Popen(['aplay', *common, '-c10'], stdin=generated,
                                            stdout=subprocess.DEVNULL, stderr=playback_errors)
                processes.append(playback)
                self.last_parameters = self.observe_streams([
                    {'name': 'arecord', 'process': capture, 'channels': 12, 'error': capture_errors},
                    {'name': 'aplay', 'process': playback, 'channels': 10, 'error': playback_errors}], duration)
                recorded.seek(0)
                audio = recorded.read(rate * duration * 36 + 1)
                require(len(audio) == rate * duration * 36, 'Incomplete 12-channel capture.')
                result = []
                for offset in (30, 33):
                    full_values = [int.from_bytes(audio[index + offset:index + offset + 3], 'little', signed=True)
                                   for index in range(0, len(audio), 36)]
                    values = full_values[rate // 2:rate * duration - rate // 2]
                    if pattern and offset == 30:
                        state = values[0] + TAG
                        if not 1 <= state <= PATTERN_PERIOD:
                            raise AudioMismatch('Invalid first PRBS11 sample.')
                        for index, value in enumerate(values):
                            if value != state - TAG:
                                frame = rate // 2 + index
                                zero = not any(audio[frame * 36:(frame + 1) * 36])
                                raise AudioMismatch(f'PRBS11 sample/order mismatch: channel {channel + 1}, '
                                                    f'sample {index}, observed {value}, expected {state - TAG}, '
                                                    f'all_capture_channels_zero={zero}')
                            state = next_pattern(state)
                    if pattern and offset == 33:
                        if min(values) != 0 or max(values) != 0:
                            raise AudioMismatch('PRBS11 leaked to the other return channel.')
                    minimum, maximum = min(values), max(values)
                    minimum_frame = rate // 2 + values.index(minimum)
                    maximum_frame = rate // 2 + values.index(maximum)
                    result.append({'mean': sum(values) / len(values), 'minimum': minimum, 'maximum': maximum,
                                   'first_minimum_index': values.index(minimum),
                                   'last_minimum_index': len(values) - 1 - values[::-1].index(minimum),
                                   'minimum_all_channels_zero': not any(audio[minimum_frame * 36:(minimum_frame + 1) * 36]),
                                   'maximum_all_channels_zero': not any(audio[maximum_frame * 36:(maximum_frame + 1) * 36]),
                                   'first_nonzero_frame': next((index for index, value in enumerate(full_values) if value), None),
                                   'last_nonzero_frame': next((len(full_values) - 1 - index for index, value in enumerate(reversed(full_values)) if value), None)})
                return result
            finally:
                for process in processes:
                    if process.poll() is None:
                        process.terminate()
                        try:
                            process.wait(timeout=3)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait(timeout=3)
                    for pipe in (process.stdout, process.stderr):
                        if pipe is not None:
                            pipe.close()

    def observe_streams(self, streams, duration):
        deadline = time.monotonic() + duration + 15
        parameters = {}
        finished = set()
        while True:
            for item in streams:
                process, name = item['process'], item['name']
                if name not in parameters:
                    actual = owned_parameters(PCM_DIRECTORY, item, self.rate, self.period, self.buffer)
                    if actual is not None:
                        parameters[name] = actual
                # Observe completion before reading diagnostics, so a final
                # XRUN written immediately before exit cannot escape the check.
                exit_code = process.poll()
                item['error'].seek(0)
                errors = item['error'].read(4096)
                require(not errors, f'{name} failed/XRUN: {errors!r}')
                if exit_code is not None:
                    require(exit_code == 0, f'{name} failed: exit {exit_code}')
                    require(name in parameters, f'{name}: actual ALSA parameters were not observed.')
                    finished.add(name)
            if len(finished) == len(streams):
                return parameters
            require(time.monotonic() < deadline, 'Duplex PCM transfer stalled.')
            time.sleep(0.01)

    def expect(self, label, expected, channel=0, amplitude=TAG, tolerance=0):
        result = self.measure(channel, amplitude)
        self.cases += 1
        if not all(abs(item[field] - value) <= tolerance for item, value in zip(result, expected)
                   for field in ('minimum', 'maximum')):
            self.audio_failure(label, f'channel {channel + 1}: expected {expected}, observed {result}')
            return
        print(json.dumps({'rate_hz': self.rate, 'case': label, 'source_channel': channel + 1,
                          'return_11_12': result, 'tolerance_lsb': tolerance, 'actual_parameters': self.last_parameters}), flush=True)

    def pattern(self, channel, duration=2):
        self.cases += 1
        try:
            result = self.measure(channel, TAG, pattern=True, duration=duration)
            print(json.dumps({'rate_hz': self.rate, 'case': 'PRBS11 sample integrity',
                              'source_channel': channel + 1, 'checked_samples': self.rate * (duration - 1),
                              'return_11_12': result, 'actual_parameters': self.last_parameters}), flush=True)
        except AudioMismatch as error:
            self.audio_failure(f'PRBS11 channel {channel + 1}', error)

    def channels(self):
        for channel in range(10):
            for pan in (0, PAN_RIGHT // 4, PAN_RIGHT // 2, PAN_RIGHT * 3 // 4, PAN_RIGHT):
                self.strip(channel, 'pan', pan)
                expected = (TAG * (PAN_RIGHT - pan) // PAN_RIGHT, TAG * pan // PAN_RIGHT)
                self.expect(f'channel map/pan {pan}', expected, channel=channel)
            self.strip(channel, 'pan', 0)
            self.pattern(channel)
            self.expect('signed negative sample', (-TAG, 0), channel=channel, amplitude=-TAG)
            self.strip(channel, 'mute', 1)
            self.expect('channel mute', (0, 0), channel=channel)
            self.strip(channel, 'mute', 0)

    def gains(self):
        for coefficient in (0, 1, 0x1000, 0x8000, 0x80000, UNITY // 2, UNITY, 0x3fd930, 0x7fffff):
            self.strip(0, 'volume', coefficient)
            self.expect(f'strip gain {coefficient}', (TAG * coefficient / UNITY, 0), tolerance=1)
        self.strip(0, 'volume', UNITY)
        for channel in range(2):
            pan = 0 if channel == 0 else PAN_RIGHT
            self.strip(0, 'pan', pan)
            for coefficient in (0, UNITY // 2, UNITY):
                self.master('playback', 0, channel, 'volume', coefficient)
                expected = [0, 0]
                expected[channel] = TAG * coefficient // UNITY
                self.expect(f'master {channel + 1} gain {coefficient}', expected)
            self.master('playback', 0, channel, 'volume', UNITY)
        self.strip(0, 'pan', 0)

    def solo(self):
        self.strip(1, 'solo', 1)
        self.expect('other channel solo suppresses signal', (0, 0))
        self.strip(0, 'solo', 1)
        self.expect('multiple solo channels retain selected signal', (TAG, 0))
        self.strip(0, 'mute', 1)
        self.expect('mute also suppresses a soloed channel', (0, 0))
        self.strip(0, 'mute', 0)
        self.strip(0, 'solo', 0)
        self.strip(1, 'solo', 0)
        self.expect('clearing solo restores signal', (TAG, 0))


def snapshot(parent, before):
    parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix='mixer-audio-', dir=parent))
    recovery = directory / 'original.syx'
    body = bytes([1, 0, 0, 0]) + bytes.fromhex(before['payload_hex'])
    with os.fdopen(os.open(recovery, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'wb') as output:
        output.write(bytes.fromhex('f0411000004d12') + body + bytes([-sum(body) % 128, 247]))
        output.flush()
        os.fsync(output.fileno())
    return recovery


def interrupted(_signum, _frame):
    raise KeyboardInterrupt('Stopping streams before restoring the original settings.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bin-dir', type=Path, default=ROOT / 'build/strict')
    parser.add_argument('--write', action='store_true', help='temporarily change mixers/routes and play isolated quiet tags')
    parser.add_argument('--rates', action='store_true', help='44.1/48/96 kHz; requires --write and restores the original rate')
    parser.add_argument('--keep-going', action='store_true', help='collect sample mismatches; I/O/safety failures still stop immediately')
    parser.add_argument('--integrity-seconds', type=int, help='only check ten longer PRBS11 streams per rate (2..60 seconds each)')
    parser.add_argument('--period-size', type=int, default=1024, help='requested PCM period frames')
    parser.add_argument('--buffer-size', type=int, default=8192, help='requested PCM buffer frames')
    parser.add_argument('--recovery-dir', type=Path, default=ROOT / 'build/hardware')
    args = parser.parse_args()
    if args.rates and not args.write:
        parser.error('--rates requires --write.')
    if args.keep_going and not args.write:
        parser.error('--keep-going requires --write.')
    if args.integrity_seconds is not None and (not args.write or not 2 <= args.integrity_seconds <= 60):
        parser.error('--integrity-seconds requires --write and a value from 2 to 60.')
    if not geometry_valid(args.period_size, args.buffer_size):
        parser.error('Use a 64..32768-frame period and a 128..65536-frame buffer holding at least two whole periods.')
    check = Check(args.bin_dir, keep_going=args.keep_going, period=args.period_size, buffer=args.buffer_size)
    before = check.json('octa-state')
    original_rate = check.json('octa-status')['sample_rate_hz']
    if not args.write:
        print(json.dumps({'sample_rate_hz': original_rate, 'digital_return_channels': [11, 12],
                          'return_available': original_rate in RATES, 'write': False}))
        return
    if original_rate not in RATES:
        parser.error('Select 44.1/48/96 kHz before this write test; return 11/12 is unavailable at 192 kHz.')
    recovery = snapshot(args.recovery_dir, before)
    print(json.dumps({'original_rate_hz': original_rate, 'raw_recovery_file': str(recovery)}), flush=True)
    modified = False
    try:
        require(check.state() == before, 'Device state changed before exclusive CTRL access.')
        modified = True
        check.silence_outputs()
        print('Physical outputs verified on silent Mixer B.', flush=True)
        for rate in RATES if args.rates else (original_rate,):
            check.bridge.close()
            check.json('octa-audio-check', rate, 'capture')
            check.rate = rate
            check.prepare_return()
            print(f'Mixer A prepared at {rate} Hz; physical outputs remain silent.', flush=True)
            if args.integrity_seconds is not None:
                for channel in range(10):
                    check.pattern(channel, args.integrity_seconds)
            else:
                check.channels()
                check.gains()
                check.solo()
    finally:
        try:
            check.bridge.close()
        finally:
            if modified:
                try:
                    check.json('octa-audio-check', original_rate, 'capture')
                finally:
                    # A competing PCM owner must not skip restoring mixer/routing
                    # settings. Any clock or raw verification failure still fails.
                    check.run('octa-preset', 'restore', recovery)
                require(check.json('octa-state') == before, f'Full-state restoration failed. Recovery: {recovery}')
                print('Original rate and complete raw state restored and independently verified.', flush=True)
    require(not check.failures, f'{len(check.failures)} of {check.cases} digital audio comparisons failed; see diagnostics.')
    print(f'PASS {check.cases} digital mixer audio cases; physical outputs silenced; no recorded audio files.', flush=True)


if __name__ == '__main__':
    signal.signal(signal.SIGTERM, interrupted)
    main()
