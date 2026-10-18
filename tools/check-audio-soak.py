#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Quiet simultaneous capture/playback soak; detects XRUNs and restores the rate."""

import argparse
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import tempfile
import time

RATES = (44100, 48000, 96000, 192000)
PCM_DIRECTORY = Path('/proc/asound/OCTACAPTURE')


class Check:
    def __init__(self, binary_directory, duration, period, buffer):
        self.binary_directory = binary_directory.resolve()
        self.duration = duration
        self.period = period
        self.buffer = buffer

    def run(self, name, *arguments):
        result = subprocess.run([str(self.binary_directory / name), *map(str, arguments)],
                                check=True, capture_output=True, timeout=15)
        return json.loads(result.stdout)

    def parameters(self, item, rate):
        direction = 'c' if item['name'] == 'arecord' else 'p'
        directory = PCM_DIRECTORY / f'pcm0{direction}/sub0'
        owner = re.search(r'^owner_pid\s*:\s*(\d+)\s*$', (directory / 'status').read_text(), re.M)
        if owner is None or int(owner[1]) != item['process'].pid:
            return None
        fields = dict(line.split(':', 1) for line in (directory / 'hw_params').read_text().splitlines() if ':' in line)
        if not fields:
            return None  # The stream may have closed between reading the two files.
        try:
            actual = {'access': fields['access'].strip(), 'sample_format': fields['format'].strip(),
                      'channels': int(fields['channels']), 'rate_hz': int(fields['rate'].split()[0]),
                      'period_frames': int(fields['period_size']), 'buffer_frames': int(fields['buffer_size'])}
        except (KeyError, ValueError, IndexError) as error:
            raise RuntimeError(f'Invalid ALSA hardware parameters for {item["name"]}: {fields}') from error
        expected = {'access': 'RW_INTERLEAVED', 'sample_format': 'S24_3LE', 'channels': item['channels'],
                    'rate_hz': rate, 'period_frames': self.period, 'buffer_frames': self.buffer}
        if actual != expected:
            raise RuntimeError(f'{item["name"]} negotiated unexpected ALSA parameters: {actual}; requested {expected}')
        return actual

    def raw_restore(self, recovery):
        subprocess.run([str(self.binary_directory / 'octa-preset'), 'restore', str(recovery)],
                       check=True, capture_output=True, timeout=15)

    def restore(self, before, original_rate, recovery):
        try:
            self.run('octa-audio-check', original_rate, 'capture')
        finally:
            # Clock/PCM acquisition failure must not skip raw state recovery.
            try:
                current = self.run('octa-state')
            except (OSError, ValueError, subprocess.SubprocessError):
                current = None
            if current != before:
                self.raw_restore(recovery)
            if self.run('octa-state') != before:
                raise RuntimeError(f'Complete raw restoration failed. Recovery: {recovery}')
        if self.run('octa-status')['sample_rate_hz'] != original_rate:
            raise RuntimeError(f'Original clock rate was not restored. Recovery: {recovery}')
        print('Original rate and complete raw state restored and independently verified.', flush=True)

    def duplex(self, rate):
        processes = []
        try:
            for executable, channels, target in (('arecord', 4 if rate == 192000 else 12, '/dev/null'),
                                                  ('aplay', 4 if rate == 192000 else 10, '/dev/zero')):
                error = tempfile.TemporaryFile()
                processes.append({'name': executable, 'error': error, 'process': None})
                process = subprocess.Popen([executable, '-q', '-D', 'hw:CARD=OCTACAPTURE,DEV=0',
                                            '-r', str(rate), '-f', 'S24_3LE', '-c', str(channels),
                                            f'--period-size={self.period}', f'--buffer-size={self.buffer}',
                                            '-d', str(self.duration), '-t', 'raw', target],
                                           stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=error)
                processes[-1].update(process=process, started=time.monotonic(), channels=channels)
            while not all('elapsed' in item for item in processes):
                for item in processes:
                    if 'elapsed' in item:
                        continue
                    elapsed = time.monotonic() - item['started']
                    process = item['process']
                    if 'parameters' not in item:
                        actual = self.parameters(item, rate)
                        if actual is not None:
                            item['parameters'] = actual
                    if process.poll() is None and os.fstat(item['error'].fileno()).st_size:
                        item['error'].seek(0)
                        diagnostic = item['error'].read(4096).decode(errors='replace')
                        raise RuntimeError(f'{rate} Hz {item["name"]}: {diagnostic}')
                    if process.poll() is not None:
                        item['error'].seek(0)
                        diagnostic = item['error'].read(4096).decode(errors='replace')
                        if process.returncode != 0 or diagnostic:
                            raise RuntimeError(f'{rate} Hz {item["name"]}: exit {process.returncode}; {diagnostic}')
                        if 'parameters' not in item:
                            raise RuntimeError(f'{rate} Hz {item["name"]}: actual ALSA parameters were not observed')
                        if not self.duration * 0.97 - 1 <= elapsed <= self.duration * 1.10 + 3:
                            raise RuntimeError(f'{rate} Hz {item["name"]}: unexpected duration {elapsed:.3f} s')
                        item['elapsed'] = elapsed
                    elif elapsed > self.duration * 1.10 + 3:
                        raise TimeoutError(f'{rate} Hz {item["name"]} stalled')
                time.sleep(0.1)
            status = self.run('octa-status')
            if status['sample_rate_hz'] != rate or not status['clock_active']:
                raise RuntimeError(f'Clock mismatch after full-duplex transfer: {status}')
            return {'rate_hz': rate, 'duration_seconds': self.duration, 'clock_active': True,
                    'requested_period_frames': self.period, 'requested_buffer_frames': self.buffer,
                    'streams': [{'direction': item['name'], 'transport_channels': item['channels'],
                                 'elapsed_seconds': round(item['elapsed'], 3),
                                 'actual_parameters': item['parameters']} for item in processes],
                    'xruns': 0, 'generated_audio': 'digital silence'}
        finally:
            for item in processes:
                process = item['process']
                if process is not None and process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=3)
                item['error'].close()


def seconds(value):
    number = int(value)
    if not 10 <= number <= 3600:
        raise argparse.ArgumentTypeError('Use 10..3600 seconds per rate.')
    return number


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bin-dir', type=Path, default=Path('build/strict'))
    parser.add_argument('--duration', type=seconds, default=600, help='seconds per rate, default 600')
    parser.add_argument('--period-size', type=int, default=256, help='requested PCM period frames, default 256')
    parser.add_argument('--buffer-size', type=int, default=8192, help='requested PCM buffer frames, default 8192')
    rates = parser.add_mutually_exclusive_group()
    rates.add_argument('--rates', action='store_true', help='all four rates; otherwise use the current rate')
    rates.add_argument('--rate', type=int, choices=RATES, help='one explicit rate, then restore the original rate')
    parser.add_argument('--recovery-dir', type=Path, default=Path('build/hardware'),
                        help='parent directory for a private raw recovery snapshot')
    arguments = parser.parse_args()
    if (not 64 <= arguments.period_size <= 32768 or not 128 <= arguments.buffer_size <= 65536 or
            arguments.buffer_size < arguments.period_size * 2 or arguments.buffer_size % arguments.period_size):
        parser.error('Use a 64..32768-frame period and a 128..65536-frame buffer holding at least two whole periods.')
    def interrupted(_signum, _frame):
        raise KeyboardInterrupt('Interrupted; stopping streams and restoring the original rate.')
    signal.signal(signal.SIGTERM, interrupted)
    check = Check(arguments.bin_dir, arguments.duration, arguments.period_size, arguments.buffer_size)
    before = check.run('octa-state')
    original_rate = check.run('octa-status')['sample_rate_hz']
    arguments.recovery_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix='audio-soak-', dir=arguments.recovery_dir))
    recovery = directory / 'original.syx'
    body = bytes([1, 0, 0, 0]) + bytes.fromhex(before['payload_hex'])
    with os.fdopen(os.open(recovery, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'wb') as output:
        output.write(bytes.fromhex('f0411000004d12') + body + bytes([-sum(body) % 128, 247]))
        output.flush()
        os.fsync(output.fileno())
    print(json.dumps({'original_rate_hz': original_rate, 'raw_recovery_file': str(recovery)}), flush=True)
    try:
        for rate in RATES if arguments.rates else (arguments.rate or original_rate,):
            check.run('octa-audio-check', rate, 'capture')
            print(json.dumps(check.duplex(rate)), flush=True)
    finally:
        check.restore(before, original_rate, recovery)
    print('PASS simultaneous silent playback/capture, timing, no XRUNs and complete raw restoration.', flush=True)


if __name__ == '__main__':
    main()
