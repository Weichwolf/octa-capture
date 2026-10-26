#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Check paired client/remap recovery on a private real PulseAudio daemon.

Run with python3 tools/check-pulse-pair.py. USB identity,
clock I/O and ALSA card creation are modeled. All pactl operations, remaps,
holding nodes and playback/recording clients use the real private server.
No hardware devices or existing audio servers are opened.
"""

from contextlib import ExitStack
import os
from pathlib import Path
import select
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gui import aggregate, pulse_audio as audio, pulse_pair
from gui.devices import Device
from gui.pulse_config import PROFILE

BASE = Device('serial:BASE', 'BASE', 0, 'BASE')
EXP = Device('serial:EXP', 'EXP', 1, 'EXP', '0121')
DEVICES = (BASE, EXP)
OLD, NEW = 48000, 96000


def wait_for(predicate, description):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.02)
    raise RuntimeError(f'Timed out waiting for {description}.')


def stop(process):
    if process.poll() is None:
        process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


class Server:
    def __init__(self, root, stack):
        runtime = root / 'runtime'
        runtime.mkdir(mode=0o700)
        # A private session bus also prevents claiming the desktop daemon's name.
        bus = subprocess.Popen(['dbus-daemon', '--session', '--nofork', '--nopidfile', '--print-address=1'],
                               stdout=subprocess.PIPE, text=True)
        stack.callback(bus.stdout.close)
        stack.callback(stop, bus)
        if not select.select([bus.stdout], [], [], 5)[0]:
            raise RuntimeError('Private session bus did not start.')
        address = bus.stdout.readline().strip()
        if not address or bus.poll() is not None:
            raise RuntimeError('Private session bus exited during startup.')
        self.env = {**self.environment(root, runtime), 'DBUS_SESSION_BUS_ADDRESS': address}
        self.process = subprocess.Popen(
            ['pulseaudio', '-n', '--daemonize=no',
             '--use-pid-file=no', '--exit-idle-time=-1', '--realtime=no',
             '--high-priority=no', f'--log-target=file:{root}/pulse.log',
             '--file', str(root / 'daemon.pa')],
            env=self.env)
        stack.callback(stop, self.process)
        stack.enter_context(patch.dict(os.environ, self.env))
        wait_for(self.ready, 'private PulseAudio startup')
        info = pulse_pair.server_info()
        if info['server_string'] != f'unix:{runtime}/native':
            raise RuntimeError('Refusing to use a non-private audio server.')

    @staticmethod
    def environment(root, runtime):
        return {**os.environ, 'HOME': str(root), 'XDG_CONFIG_HOME': str(root / 'config'),
                'XDG_RUNTIME_DIR': str(runtime), 'PULSE_RUNTIME_PATH': str(runtime),
                'PULSE_STATE_PATH': str(root / 'state'), 'PULSE_COOKIE': str(root / 'cookie'),
                'PULSE_SERVER': f'unix:{runtime}/native'}

    def ready(self):
        if self.process.poll() is not None:
            raise RuntimeError('Private PulseAudio exited during startup.')
        return subprocess.run(['pactl', 'info'], stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL, timeout=2).returncode == 0


class HardwareBoundary:
    """Replace only card discovery/creation and clock/USB edges with null sinks.

    Each modeled unit has one stereo output and its monitor as input. This
    deliberately does not claim the physical 10/12-channel ALSA profile works.
    Real module IDs, nodes, streams, dependency graphs and module arguments
    remain visible to the production coordinator.
    """

    def __init__(self):
        self.rate = OLD
        self.original_modules = audio.modules
        self.clock_calls = []
        self.fail_base = False
        for device in DEVICES:
            self.create(device, OLD)

    @staticmethod
    def root_name(device):
        return f'octa_test_{device.card_id}.playback-1-2'

    def create(self, device, rate):
        return audio.load_module('module-null-sink', {'sink_name': self.root_name(device),
                                 'rate': rate, 'channels': 2, 'format': 's24le'})

    def modules(self):
        result = self.original_modules()
        for index, (name, arguments) in list(result.items()):
            for device in DEVICES:
                if name == 'module-null-sink' and arguments.get('sink_name') == self.root_name(device):
                    result[index] = 'module-alsa-card', {'device_id': device.card_id, 'rate': arguments['rate']}
        return result

    def card(self, device):
        return next(({'owner_module': index, 'active_profile': PROFILE}
                     for index, (name, arguments) in self.modules().items()
                     if name == 'module-alsa-card' and arguments['device_id'] == device.card_id), None)

    def inspect(self, *_arguments, **_keywords):
        return aggregate.Plan(BASE, EXP, self.rate)

    def clock(self, _binary, _plan, rate, **_keywords):
        self.clock_calls.append(rate)
        self.rate = rate

    def controller(self, device):
        controller = audio.Controller('/missing-native-backends', device)
        controller.card = lambda: self.card(device)
        controller.validate_endpoints = self.validate_endpoints
        return controller

    @staticmethod
    def validate_endpoints(owner, rate):
        for kind in ('sink', 'source'):
            nodes = [node for node in audio.document(f'{kind}s') if str(node['owner_module']) == owner]
            if (len(nodes) != 1 or nodes[0]['channel_map'] != 'front-left,front-right' or
                    nodes[0]['sample_specification'] != f's24le 2ch {rate}Hz'):
                raise RuntimeError('The modeled stereo card did not reopen.')

    def transaction(self):
        boundary = self
        class Check(pulse_pair.Transaction):
            def load(self, unit, arguments, _profile):
                device = unit.controller.device
                if boundary.fail_base and device == BASE:
                    boundary.fail_base = False
                    raise ValueError('Injected BASE reopen failure')
                unit.owner = boundary.create(device, int(arguments['rate']))
                return unit.owner
        transaction = Check('/missing-native-backends', self.inspect())
        transaction.units = [pulse_pair.Unit(self.controller(device)) for device in DEVICES]
        return transaction


class RealPulsePair(unittest.TestCase):
    def setUp(self):
        if os.geteuid() == 0:
            self.fail('Run the private PulseAudio check as a regular user.')
        stack = ExitStack()
        self.addCleanup(stack.close)
        self.root = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix='octa-pulse-pair-')))
        (self.root / 'config/pulse').mkdir(parents=True)
        (self.root / 'config/pulse/daemon.conf').write_text('flat-volumes = no\n')
        (self.root / 'daemon.pa').write_text(
            f'load-module module-native-protocol-unix socket={self.root}/runtime/native auth-anonymous=1\n')
        self.server = Server(self.root, stack)
        audio.load_module('module-rescue-streams', {})
        audio.load_module('module-null-sink', {'sink_name': 'unrelated_output', 'channels': 2})
        stack.enter_context(patch.object(Path, 'home', return_value=self.root))
        self.boundary = HardwareBoundary()
        stack.enter_context(patch.object(audio, 'modules', self.boundary.modules))
        stack.enter_context(patch.object(pulse_pair, 'discover', return_value=DEVICES))
        stack.enter_context(patch.object(pulse_pair, 'verify_card'))
        stack.enter_context(patch.object(aggregate, 'inspect', self.boundary.inspect))
        stack.enter_context(patch.object(pulse_pair.pair_clock, 'change', self.boundary.clock))
        self.stack = stack
        for device in DEVICES:
            self.remaps_and_clients(device)
        audio.pulse('set-default-sink', 'BASE_speaker_chain')
        audio.pulse('set-default-source', 'EXP_mic_chain')
        for kind, category in (('sink', 'sink-inputs'), ('source', 'source-outputs')):
            target = 'sink-input' if kind == 'sink' else 'source-output'
            for index, stream in enumerate(audio.document(category)):
                if stream['driver'] == 'protocol-native.c':
                    audio.pulse(f'set-{target}-volume', stream['index'], 30000 + index * 500, 40000 + index * 500)
                    audio.pulse(f'set-{target}-mute', stream['index'], int(index % 2 == 0))
        (self.root / '.asoundrc').write_text('# Unrelated ALSA configuration\n')

    def client(self, device, kind, node):
        name = f'octa_test_{device.card_id}_{kind}'
        command = ['pacat', '--playback' if kind == 'sink' else '--record', '--raw',
                   '--device', node, '--channels=2', '--rate=48000', '--format=s16le', f'--stream-name={name}']
        with open('/dev/zero', 'rb') as zeros:
            process = subprocess.Popen(command, stdin=zeros, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        self.stack.callback(stop, process)
        self.stack.callback(process.stderr.close)
        category = 'sink-inputs' if kind == 'sink' else 'source-outputs'
        def ready():
            if process.poll() is not None:
                raise RuntimeError(process.stderr.read().decode())
            return any(stream['properties'].get('media.name') == name for stream in audio.document(category))
        wait_for(ready, f'{name} client startup')

    def remaps_and_clients(self, device):
        for kind, label in (('sink', 'speaker'), ('source', 'mic')):
            master = self.boundary.root_name(device) + ('.monitor' if kind == 'source' else '')
            for suffix in ('', '_chain'):
                node = f'{device.card_id}_{label}{suffix}'
                audio.load_module(f'module-remap-{kind}', {'master': master, f'{kind}_name': node,
                                  'channels': 2, 'master_channel_map': 'front-right,front-left', 'remix': 'no'})
                master = node
            self.client(device, kind, master)
        for kind in ('sink', 'source'):
            for index, node in enumerate(audio.document(f'{kind}s')):
                audio.pulse(f'set-{kind}-volume', node['name'], 20000 + index * 1000, 30000 + index * 1000)
                audio.pulse(f'set-{kind}-mute', node['name'], int(index % 2 == 0))

    def state(self):
        info = pulse_pair.server_info()
        result = {'defaults': {kind: info[f'default_{kind}_name'] for kind in ('sink', 'source')},
                  'levels': {}, 'clients': {}, 'stream_settings': {},
                  'remaps': {(name, arguments.get('sink_name', arguments.get('source_name'))): arguments
                             for name, arguments in audio.modules().values() if name.startswith('module-remap-')}}
        for kind, category in (('sink', 'sink-inputs'), ('source', 'source-outputs')):
            nodes = {node['index']: node for node in audio.document(f'{kind}s')}
            result['levels'][kind] = {node['name']: (node['volume'], node['mute']) for node in nodes.values()}
            result['clients'][kind] = {stream['index']: nodes[stream[kind]]['name']
                                      for stream in audio.document(category) if stream['driver'] == 'protocol-native.c'}
            result['stream_settings'][kind] = {stream['index']: (stream['volume'], stream['mute'], stream['channel_map'],
                                               stream['sample_specification']) for stream in audio.document(category)
                                               if stream['driver'] == 'protocol-native.c'}
        return result

    def monitor_peak(self, sink):
        """Send nonzero PCM and measure a real native monitor recording."""
        with ExitStack() as stack:
            pattern = self.root / 'probe.raw'
            pattern.write_bytes(struct.pack('<hh', 8192, -12288) * 48000 * 4)
            common = ['pacat', '--raw', '--channels=2', '--rate=48000', '--format=s16le', '--latency-msec=20']
            recording = subprocess.Popen([*common, '--record', f'--device={sink}.monitor',
                                          '--stream-name=octa_probe_capture'], stdout=subprocess.PIPE,
                                         stderr=subprocess.PIPE)
            stack.callback(recording.stderr.close)
            stack.callback(recording.stdout.close)
            stack.callback(stop, recording)
            wait_for(lambda: any(stream['properties'].get('media.name') == 'octa_probe_capture'
                                 for stream in audio.document('source-outputs')), 'probe capture startup')
            with pattern.open('rb') as payload:
                playback = subprocess.Popen([*common, '--playback', f'--device={sink}',
                                             '--stream-name=octa_probe_playback'], stdin=payload,
                                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            stack.callback(playback.stderr.close)
            stack.callback(stop, playback)
            wait_for(lambda: any(stream['properties'].get('media.name') == 'octa_probe_playback'
                                 for stream in audio.document('sink-inputs')), 'probe playback startup')
            data = bytearray()
            deadline = time.monotonic() + 5
            expected = 48000 * 4
            while len(data) < expected and time.monotonic() < deadline:
                if select.select([recording.stdout], [], [], .1)[0]:
                    block = os.read(recording.stdout.fileno(), min(16384, expected - len(data)))
                    if not block:
                        raise RuntimeError('The native probe recording ended early.')
                    data.extend(block)
            if len(data) != expected:
                raise RuntimeError('The native probe did not deliver one second of stereo PCM.')
            return max(abs(sample[0]) for sample in struct.iter_unpack('<h', data))

    def test_holding_capture_is_silent_while_playback_clients_continue(self):
        owner = audio.load_module('module-null-sink', {'sink_name': 'positive_control', 'channels': 2, 'rate': OLD})
        try:
            self.assertGreater(self.monitor_peak('positive_control'), 1000,
                               'Positive control must detect the actual nonzero playback samples.')
        finally:
            audio.pulse('unload-module', owner)
        controller = audio.Controller('/missing-native-backends', BASE)
        owner, name = controller.hold({'sink': [], 'source': []}, {'sink': [], 'source': []}, {})
        try:
            self.assertEqual(self.monitor_peak(name), 0,
                             'Held capture clients must not receive the held playback audio.')
        finally:
            audio.pulse('unload-module', owner)

    def test_chained_remaps_clients_levels_and_defaults_survive_the_joint_transaction(self):
        before = self.state()
        result = self.boundary.transaction().run(NEW)
        self.assertEqual(result.rate, NEW)
        self.assertEqual(self.state(), before)
        self.assertEqual(self.boundary.clock_calls, [NEW])
        self.assertEqual(len(before['clients']['sink']), 2)
        self.assertEqual(len(before['clients']['source']), 2)
        self.assertFalse(any(name.startswith('octa_clock_') for name in self.state()['levels']['sink']))

    def test_reopen_failure_recovers_both_real_graphs_and_shared_configuration(self):
        before = self.state()
        original = (self.root / '.asoundrc').read_bytes()
        self.boundary.fail_base = True
        with self.assertRaisesRegex(ValueError, 'BASE reopen failure'):
            self.boundary.transaction().run(NEW)
        self.assertEqual(self.state(), before)
        self.assertEqual(self.boundary.clock_calls, [NEW, OLD])
        self.assertEqual((self.root / '.asoundrc').read_bytes(), original)

    def test_failure_after_partial_resume_parks_and_restores_the_live_clients(self):
        before = self.state()
        transaction = self.boundary.transaction()
        resume_clients = transaction.resume
        failed = False
        def resume(unit, *, original):
            nonlocal failed
            result = resume_clients(unit, original=original)
            if not original and not failed:
                failed = True
                raise ValueError('Injected failure after client resume')
            return result
        transaction.resume = resume
        with self.assertRaisesRegex(ValueError, 'after client resume'):
            transaction.run(NEW)
        self.assertEqual(self.state(), before)
        self.assertEqual(self.boundary.clock_calls, [NEW, OLD])

    def test_cancellation_after_clock_preparation_recovers_real_clients(self):
        before = self.state()
        cancelled = threading.Event()
        transaction = self.boundary.transaction()
        transaction.cancelled = cancelled
        clock = self.boundary.clock
        def cancel(*arguments, **keywords):
            clock(*arguments, **keywords)
            cancelled.set()
        with patch.object(pulse_pair.pair_clock, 'change', cancel):
            with self.assertRaises(pulse_pair.pair_clock.ClockCancelled):
                transaction.run(NEW)
        self.assertEqual(self.state(), before)
        self.assertTrue(cancelled.is_set())
        self.assertEqual(self.boundary.clock_calls, [NEW, OLD])

    def test_failed_capture_gain_restore_rolls_back_the_hold_with_original_client_controls(self):
        before = self.state()
        original = audio.pulse
        failed = False
        def pulse(*arguments):
            nonlocal failed
            if arguments[0] == 'set-source-output-volume' and not failed:
                failed = True
                raise subprocess.CalledProcessError(1, 'pactl')
            return original(*arguments)
        with patch.object(audio, 'pulse', pulse):
            with self.assertRaises(subprocess.CalledProcessError):
                self.boundary.transaction().run(NEW)
        self.assertTrue(failed)
        self.assertEqual(self.state(), before)
        self.assertEqual(self.boundary.clock_calls, [])

    def test_failed_clock_recovery_keeps_clients_held_despite_automatic_rescue(self):
        before = self.state()
        transaction = self.boundary.transaction()
        with patch.object(pulse_pair.pair_clock, 'change', side_effect=ValueError('Clock unavailable')):
            with self.assertRaisesRegex(RuntimeError, 'recovery is incomplete'):
                transaction.run(NEW)
        after = self.state()
        for kind in ('sink', 'source'):
            self.assertEqual(set(before['clients'][kind]), set(after['clients'][kind]))
            self.assertTrue(all(name.startswith('octa_clock_') for name in after['clients'][kind].values()))
        self.assertEqual(after['levels']['sink']['unrelated_output'], before['levels']['sink']['unrelated_output'])
        with self.assertRaisesRegex(RuntimeError, 'still have clients'):
            transaction.cleanup()
        self.assertEqual(self.state(), after)

    def test_untracked_module_stream_is_rejected_before_mutation(self):
        audio.load_module('module-loopback', {'source': self.boundary.root_name(BASE) + '.monitor',
                          'sink': self.boundary.root_name(EXP), 'latency_msec': 20})
        before = self.state()
        modules = audio.modules()
        original = (self.root / '.asoundrc').read_bytes()
        with self.assertRaisesRegex(ValueError, 'module-owned'):
            self.boundary.transaction().run(NEW)
        self.assertEqual(self.state(), before)
        self.assertEqual(audio.modules(), modules)
        self.assertEqual(self.boundary.clock_calls, [])
        self.assertEqual((self.root / '.asoundrc').read_bytes(), original)


if __name__ == '__main__':
    unittest.main(verbosity=2)
