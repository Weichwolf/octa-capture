#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Quiet, exclusive-device PCM meter/ALSA handover checks. Restores the rate."""

import argparse
import json
from pathlib import Path
import subprocess
import tempfile
import time


class Check:
    def __init__(self, directory):
        self.directory = Path(directory).resolve()
        self.monitor = None
        self.output = tempfile.TemporaryFile()

    def run(self, name, *arguments):
        result = subprocess.run([str(self.directory / name), *map(str, arguments)],
                                capture_output=True, timeout=12, check=True)
        return result.stdout.decode()

    def frame(self):
        return json.loads(self.run('octa-pcm-meters', '--read'))

    def wait(self, predicate, description, timeout=5):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            frame = self.frame()
            if predicate(frame):
                return frame
            time.sleep(0.05)
        raise AssertionError(f'Timeout: {description}; last frame: {frame}')

    def start(self):
        if self.monitor is not None:
            raise AssertionError('A monitor is already running.')
        self.output.seek(0)
        self.output.truncate()
        self.monitor = subprocess.Popen([str(self.directory / 'octa-pcm-meters'), '60'],
                                        stdout=self.output)
        self.wait(lambda frame: frame['enabled'] and frame['idle_stream'] and frame['error'] == 0
                  and frame['capture']['fresh'] and frame['playback']['fresh'], 'idle PCM engine')

    def stop(self, kill=False):
        process, self.monitor = self.monitor, None
        if process is None:
            return
        if kill:
            process.kill()
        else:
            process.terminate()
        try:
            result = process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
            raise
        if not kill and result != 0:
            raise AssertionError(f'Meter process exited with {result}.')

    def capture(self, close_meter=False):
        # A real PCM file, different period/buffer sizes from the idle engine.
        rate = self.frame()['capture']['rate']
        channels = 4 if rate == 192000 else 12
        process = subprocess.Popen(['arecord', '-q', '-D', 'hw:CARD=OCTACAPTURE',
                                    '-r', str(rate), '-f', 'S24_3LE', '-c', str(channels),
                                    '--period-size=256', '--buffer-size=2048', '-d2',
                                    '-t', 'raw', '/dev/null'], stderr=subprocess.PIPE)
        try:
            self.wait(lambda frame: frame['enabled'] and not frame['idle_stream'] and
                      frame['capture']['fresh'], 'real PCM takes priority')
            if close_meter:
                self.stop()
            _, error = process.communicate(timeout=8)
            assert process.returncode == 0, error.decode()
            assert not error, error.decode()
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
        if close_meter:
            self.wait(lambda frame: not frame['enabled'] and not frame['idle_stream'],
                      'meter close leaves normal capture running')
        else:
            self.wait(lambda frame: frame['idle_stream'] and frame['capture']['fresh'] and
                      frame['playback']['fresh'], 'idle engine resumes after PCM close')

    def rates(self, rates):
        for rate in rates:
            for direction in ('capture', 'playback'):
                result = json.loads(self.run('octa-audio-check', rate, direction))
                assert result['error'] == 0 and result['timing_ok'], result
            frame = self.wait(lambda frame: frame['idle_stream'] and frame['error'] == 0 and
                              all(frame[name]['rate'] == rate and frame[name]['fresh']
                                  for name in ('capture', 'playback')), f'fresh {rate} Hz PCM')
            assert frame['capture']['channels'] == (4 if rate == 192000 else 12)
            assert frame['playback']['channels'] == (4 if rate == 192000 else 10)
            first = frame['capture']['sequence']
            time.sleep(0.08)
            assert self.frame()['capture']['sequence'] != first, 'capture sequence stalled'
            print(f'PASS {rate} Hz: quiet capture/playback, fresh peaks and idle handover', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bin-dir', default='build/strict')
    parser.add_argument('--rates', action='store_true', help='change through all four rates and restore')
    arguments = parser.parse_args()
    check = Check(arguments.bin_dir)
    before = json.loads(check.run('octa-state'))
    original_rate = json.loads(check.run('octa-status'))['sample_rate_hz']
    try:
        check.start()
        assert json.loads(check.run('octa-state')) == before, 'starting meters changed state'
        check.capture()
        print('PASS different PCM period sizes take priority; idle engine resumes', flush=True)
        check.capture(close_meter=True)
        print('PASS closing meters preserves an already running PCM capture', flush=True)
        check.start()
        check.rates((44100, 48000, 96000, 192000) if arguments.rates else (original_rate,))
        check.stop(kill=True)
        # No read renews the dead client's lease during this interval.
        time.sleep(2.3)
        check.wait(lambda frame: not frame['enabled'] and not frame['idle_stream'],
                   'dead client lease expires')
        print('PASS killed meter client releases idle streams through kernel lease', flush=True)
    finally:
        check.stop()
        check.run('octa-audio-check', original_rate, 'capture')
        check.output.close()
    assert json.loads(check.run('octa-state')) == before, 'complete raw device state changed'
    print('PASS original rate and complete raw state restored; no generated audio', flush=True)


if __name__ == '__main__':
    main()
