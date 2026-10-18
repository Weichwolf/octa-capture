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

    def start(self, idle=True):
        if self.monitor is not None:
            raise AssertionError('A monitor is already running.')
        self.output.seek(0)
        self.output.truncate()
        self.monitor = subprocess.Popen([str(self.directory / 'octa-pcm-meters'), '60'],
                                        stdout=self.output)
        self.wait(lambda frame: frame['enabled'] and frame['idle_stream'] == idle and frame['error'] == 0
                  and (not idle or (frame['capture']['fresh'] and frame['playback']['fresh'])),
                  'idle PCM engine' if idle else 'meters on normal PCM streams')

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

    def streams(self, directions, close_meter=False):
        # Normal PCM files must own their endpoints across meter close/reopen.
        rate = json.loads(self.run('octa-status'))['sample_rate_hz']
        processes = []
        try:
            for direction in directions:
                capture = direction == 'capture'
                channels = 4 if rate == 192000 else (12 if capture else 10)
                error = tempfile.TemporaryFile()
                processes.append({'error': error, 'process': None})
                process = subprocess.Popen(['arecord' if capture else 'aplay', '-q',
                                            '-D', 'hw:CARD=OCTACAPTURE', '-r', str(rate),
                                            '-f', 'S24_3LE', '-c', str(channels),
                                            '--period-size=1024', '--buffer-size=8192', '-d4',
                                            '-t', 'raw', '/dev/null' if capture else '/dev/zero'],
                                           stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=error)
                processes[-1]['process'] = process
            self.normal_progress(directions, rate)
            if close_meter:
                self.stop()
                self.wait(lambda frame: not frame['enabled'] and not frame['idle_stream'],
                          'meter close releases only its own engine')
                time.sleep(0.12)
                assert all(item['process'].poll() is None for item in processes), 'normal PCM stopped with meters'
                self.start(idle=False)
                self.normal_progress(directions, rate)
            for item in processes:
                process = item['process']
                process.wait(timeout=10)
                item['error'].seek(0)
                error = item['error'].read(4096).decode(errors='replace')
                assert process.returncode == 0, error
                assert not error, error
        finally:
            for item in processes:
                process = item['process']
                if process is not None and process.poll() is None:
                    process.kill()
                    process.wait(timeout=5)
                item['error'].close()
        self.wait(lambda frame: frame['idle_stream'] and frame['capture']['fresh'] and
                  frame['playback']['fresh'], 'idle engine resumes after PCM close')
        mode = '/'.join(directions)
        action = 'meter close/reopen and normal PCM preservation' if close_meter else 'idle handover/resumption'
        print(f'PASS {rate} Hz {mode}: {action}, no XRUNs', flush=True)

    def normal_progress(self, directions, rate):
        first = self.wait(lambda frame: frame['enabled'] and not frame['idle_stream'] and
                          all(frame[name]['fresh'] and frame[name]['rate'] == rate for name in directions),
                          'real PCM takes priority')
        self.wait(lambda frame: frame['enabled'] and not frame['idle_stream'] and
                  all(frame[name]['fresh'] and frame[name]['rate'] == rate and
                      frame[name]['sequence'] != first[name]['sequence'] for name in directions),
                  'normal PCM advances each requested meter direction')

    def rates(self, rates, peaks=False):
        for rate in rates:
            for direction in ('capture', 'playback'):
                result = json.loads(self.run('octa-audio-check', rate, direction))
                assert result['error'] == 0 and result['timing_ok'], result
            if peaks:
                self.stop()
                print(self.run('octa-pcm-peak-check'), end='', flush=True)
                self.start()
            frame = self.wait(lambda frame: frame['idle_stream'] and frame['error'] == 0 and
                              all(frame[name]['rate'] == rate and frame[name]['fresh']
                                  for name in ('capture', 'playback')), f'fresh {rate} Hz PCM')
            assert frame['capture']['channels'] == (4 if rate == 192000 else 12)
            assert frame['playback']['channels'] == (4 if rate == 192000 else 10)
            first = frame['capture']['sequence']
            time.sleep(0.08)
            assert self.frame()['capture']['sequence'] != first, 'capture sequence stalled'
            for directions in (('capture',), ('playback',), ('capture', 'playback')):
                self.streams(directions)
                self.streams(directions, close_meter=True)
            print(f'PASS {rate} Hz: quiet capture/playback, fresh peaks and idle handover', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bin-dir', default='build/strict')
    parser.add_argument('--rates', action='store_true', help='change through all four rates and restore')
    parser.add_argument('--peaks', action='store_true',
                        help='verify exact channel peaks with tags no larger than -118 dBFS')
    arguments = parser.parse_args()
    check = Check(arguments.bin_dir)
    before = json.loads(check.run('octa-state'))
    original_rate = json.loads(check.run('octa-status'))['sample_rate_hz']
    try:
        check.start()
        assert json.loads(check.run('octa-state')) == before, 'starting meters changed state'
        check.rates((44100, 48000, 96000, 192000) if arguments.rates else (original_rate,),
                    peaks=arguments.peaks)
        check.stop(kill=True)
        # No read renews the dead client's lease during this interval.
        time.sleep(2.3)
        check.wait(lambda frame: not frame['enabled'] and not frame['idle_stream'],
                   'dead client lease expires')
        print('PASS killed meter client releases idle streams through kernel lease', flush=True)
    finally:
        try:
            check.stop()
        finally:
            try:
                check.run('octa-audio-check', original_rate, 'capture')
                assert json.loads(check.run('octa-state')) == before, 'complete raw device state changed'
                print('Original rate and complete raw state restored and independently verified.', flush=True)
            finally:
                check.output.close()
    signal = 'only very quiet channel tags' if arguments.peaks else 'no generated audio'
    print(f'PASS original rate and complete raw state restored; {signal}', flush=True)


if __name__ == '__main__':
    main()
