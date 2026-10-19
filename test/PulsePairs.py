# SPDX-License-Identifier: GPL-2.0-only
"""Native channel bindings and faulted PulseAudio clock transactions, offline."""

import copy
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gui import pulse_audio as audio
from gui.devices import Device
from gui.pulse_config import configuration, Files, PROFILE


class Peer:
    def __init__(self):
        self.rate = 44100
        self.next_id = 100
        self.loaded = {}
        self.nodes = {'sink': [], 'source': []}
        self.info = {'server_name': 'pulseaudio'}
        self.streams = {'sink': [], 'source': []}
        self.failure = None
        self.calls = []
        self.load('module-alsa-card', {'rate': '44100', 'profile_set': '/own/profile', 'ignore_dB': 'no'})
        for kind, direction in (('sink', 'playback'), ('source', 'recording')):
            first = next(node for node in self.nodes[kind] if node['name'].endswith(f'{direction}-1-2'))
            self.info[f'default_{kind}_name'] = first['name']
            self.streams[kind] = [{'index': 1, kind: first['index'], 'driver': 'protocol-native.c',
                                   'channel_map': 'front-left,front-right'}]
            first['volume']['front-left']['value'] = 32768
            first['mute'] = kind == 'source'

    def node(self, kind, owner, name):
        self.next_id += 1
        value = {'index': self.next_id, 'owner_module': owner, 'name': name,
                 'channel_map': 'front-left,front-right', 'mute': False,
                 'volume': {channel: {'value': 65536} for channel in ('front-left', 'front-right')}}
        self.nodes[kind].append(value)
        return value

    def load(self, name, arguments):
        if name == 'module-alsa-card' and self.failure in ('load', 'rollback'):
            if self.failure == 'load':
                self.failure = None
            raise ValueError('Injected card load failure')
        self.next_id += 1
        owner = str(self.next_id)
        self.loaded[owner] = (name, copy.deepcopy(arguments))
        if name == 'module-null-sink':
            self.node('sink', owner, arguments['sink_name'])
            self.node('source', owner, arguments['sink_name'] + '.monitor')
        else:
            self.owner = owner
            for kind, direction, count in (('sink', 'playback', 4 if self.rate == 192000 else 10),
                                            ('source', 'recording', 4 if self.rate == 192000 else 12)):
                for channel in range(0, count, 2):
                    node = self.node(kind, owner, f'alsa_{kind}.unit.{direction}-{channel+1}-{channel+2}')
                    if kind == 'sink':
                        self.node('source', owner, node['name'] + '.monitor')
        return owner

    def document(self, category):
        if category == 'cards':
            return [{'owner_module': self.owner, 'active_profile': PROFILE, 'properties': {'alsa.card': '0'}}]
        if category in ('sinks', 'sources'):
            return copy.deepcopy(self.nodes[category[:-1]])
        return copy.deepcopy(self.streams['sink' if category == 'sink-inputs' else 'source'])

    def pulse(self, *arguments):
        self.calls.append(arguments)
        operation = arguments[0]
        if operation == '-f':
            import json
            return json.dumps(self.info)
        if operation == 'unload-module':
            owner = str(arguments[1])
            for kind in ('sink', 'source'):
                removed = {node['index'] for node in self.nodes[kind] if str(node['owner_module']) == owner}
                self.nodes[kind] = [node for node in self.nodes[kind] if str(node['owner_module']) != owner]
                fallback = next((node for node in self.nodes[kind] if node['name'].startswith('octa_clock_')), None)
                for stream in self.streams[kind]:
                    if stream[kind] in removed and fallback:
                        stream[kind] = fallback['index']
            del self.loaded[owner]
        elif operation.startswith('set-default-'):
            self.info[f'default_{operation.removeprefix("set-default-")}_name'] = arguments[1]
        elif operation.startswith('move-'):
            kind = 'sink' if operation == 'move-sink-input' else 'source'
            target = next(node for node in self.nodes[kind] if node['name'] == arguments[2])
            stream = next(stream for stream in self.streams[kind] if stream['index'] == arguments[1])
            stream[kind] = target['index']
        elif operation.startswith('set-'):
            _, kind, field = operation.split('-')
            node = next(node for node in self.nodes[kind] if node['name'] == arguments[1])
            if field == 'mute':
                node['mute'] = bool(arguments[2])
            else:
                for value, volume in zip(node['volume'].values(), arguments[2:], strict=True):
                    value['value'] = volume
        else:
            raise AssertionError(arguments)
        return ''

    def prepare(self, rate):
        if self.failure == 'prepare':
            self.failure = None
            raise ValueError('Injected clock failure')
        self.rate = rate

    def state(self):
        result = {'rate': self.rate, 'defaults': copy.deepcopy(self.info), 'streams': {}, 'levels': {}}
        for kind in ('sink', 'source'):
            index = {node['index']: node for node in self.nodes[kind]}
            result['streams'][kind] = [index[stream[kind]]['name'] for stream in self.streams[kind]]
            result['levels'][kind] = {node['name']: (node['volume'], node['mute']) for node in self.nodes[kind]}
        return copy.deepcopy(result)


class Pairing(unittest.TestCase):
    def test_native_profiles_bind_each_pair_once_at_all_rates(self):
        for rate in (44100, 48000, 96000, 192000):
            alsa, profile = configuration('OCTACAPTURE', rate)
            playback, recording = (4, 4) if rate == 192000 else (10, 12)
            self.assertEqual(alsa.count(b'type dshare'), playback // 2)
            self.assertEqual(alsa.count(b'type dsnoop'), recording // 2)
            self.assertNotIn(b'multichannel', profile)
            for count in (playback, recording):
                for channel in range(0, count, 2):
                    self.assertIn(f'bindings {{ 0 {channel} 1 {channel+1} }}'.encode(), alsa)
            self.assertIn(f'rate {rate}'.encode(), alsa)
            self.assertEqual(profile.count(b'channel-map = front-left,front-right'), (playback + recording) // 2)
        for card, rate in (('../bad', 44100), ('good', True), ('good', 50000)):
            with self.assertRaises(ValueError):
                configuration(card, rate)

    def test_files_preserve_existing_alsa_configuration_and_roll_back(self):
        with tempfile.TemporaryDirectory() as directory, patch('pathlib.Path.home', return_value=Path(directory)):
            home = Path(directory)
            user = home / '.asoundrc'
            user.write_text('# Existing user configuration\npcm.test { type null }\n')
            original = user.read_bytes()
            files = Files('OCTACAPTURE')
            files.install('OCTACAPTURE', 48000)
            self.assertTrue(user.read_bytes().startswith(original.rstrip(b'\n')))
            self.assertIn(b'rate 48000', files.alsa.read_bytes())
            files.restore()
            self.assertEqual(user.read_bytes(), original)
            self.assertFalse(files.profile.exists())

    def test_existing_pulse_argument_case_and_quoting_survive(self):
        with patch.object(audio, 'pulse', return_value='44') as pulse:
            audio.load_module('module-alsa-card', {'ignore_dB': 'no', 'profile_set': '/prefix with spaces/profile'})
        self.assertIn('ignore_dB="no"', pulse.call_args.args)
        self.assertIn('profile_set="/prefix with spaces/profile"', pulse.call_args.args)

    def test_migration_preserves_parent_gain_and_mute_for_every_pair(self):
        parent = {'name': 'hardware.multichannel-output', 'channel_map': ','.join(f'aux{i}' for i in range(10)),
                  'mute': True, 'volume': {f'aux{i}': {'value': 32768 if i % 2 == 0 else 65536} for i in range(10)}}
        child = {'name': 'octa_capture_playback_1_2', 'channel_map': 'front-left,front-right', 'mute': False,
                 'volume': {name: {'value': 32768} for name in ('front-left', 'front-right')}}
        original = {'sink': [parent, child], 'source': []}
        before = copy.deepcopy(original)
        pairs = audio.by_pair(audio.inherited_controls(original)['sink'], 'sink')
        self.assertEqual([item['value'] for item in pairs[('playback', 1, 2, False)]['volume'].values()], [16384, 32768])
        for first in (3, 5, 7, 9):
            pair = pairs[('playback', first, first + 1, False)]
            self.assertEqual([item['value'] for item in pair['volume'].values()], [32768, 65536])
            self.assertTrue(pair['mute'])
        self.assertEqual(original, before)

    def test_success_and_clock_or_card_failure_restore_streams_and_levels(self):
        for failure in (None, 'prepare', 'load'):
            with self.subTest(failure=failure):
                peer = Peer()
                before = peer.state()
                controller = audio.Controller(Path('/unused'), Device('serial:unit', 'OCTACAPTURE', 0, 'unit'))
                controller.files = Mock(profile=Path('/own/profile'))
                controller.status = lambda: peer.rate
                controller.prepare = peer.prepare
                peer.failure = failure
                with patch.object(audio, 'pulse', peer.pulse), patch.object(audio, 'document', peer.document), \
                        patch.object(audio, 'modules', lambda: copy.deepcopy(peer.loaded)), \
                        patch.object(audio, 'load_module', peer.load):
                    card = peer.document('cards')[0]
                    if failure:
                        with self.assertRaisesRegex(ValueError, 'Injected'):
                            controller.switch(card, 48000, 44100, copy.deepcopy(peer.loaded), copy.deepcopy(peer.info))
                        controller.files.restore.assert_called_once()
                        self.assertEqual(peer.state(), before)
                    else:
                        controller.switch(card, 48000, 44100, copy.deepcopy(peer.loaded), copy.deepcopy(peer.info))
                        expected = {**before, 'rate': 48000}
                        self.assertEqual(peer.state(), expected)
                self.assertEqual(len(peer.loaded), 1, 'Temporary holding device is removed')

    def test_192k_unavailable_recording_is_rejected_before_mutation(self):
        peer = Peer()
        node = next(node for node in peer.nodes['source'] if node['name'].endswith('recording-11-12'))
        peer.streams['source'][0]['source'] = node['index']
        controller = audio.Controller(Path('/unused'), Device('serial:unit', 'OCTACAPTURE', 0, 'unit'))
        with patch.object(audio, 'document', peer.document):
            _, streams, _ = controller.snapshot(peer.owner, peer.loaded)
        with self.assertRaisesRegex(ValueError, 'unavailable'):
            controller.validate_streams(streams, 192000)
        self.assertEqual(peer.calls, [])
        peer.streams['source'][0]['resample_method'] = 'peaks'
        with patch.object(audio, 'document', peer.document):
            _, meters, _ = controller.snapshot(peer.owner, peer.loaded)
        controller.validate_streams(meters, 192000)
        self.assertEqual(meters['source'], [], 'Desktop level monitors are not recordings')

    def test_failed_recovery_keeps_streams_paused_instead_of_rescuing_elsewhere(self):
        peer = Peer()
        controller = audio.Controller(Path('/unused'), Device('serial:unit', 'OCTACAPTURE', 0, 'unit'))
        controller.files = Mock(profile=Path('/own/profile'))
        controller.status = lambda: peer.rate
        controller.prepare = peer.prepare
        peer.failure = 'rollback'
        with patch.object(audio, 'pulse', peer.pulse), patch.object(audio, 'document', peer.document), \
                patch.object(audio, 'modules', lambda: copy.deepcopy(peer.loaded)), \
                patch.object(audio, 'load_module', peer.load):
            with self.assertRaisesRegex(RuntimeError, 'Streams remain paused'):
                controller.switch(peer.document('cards')[0], 48000, 44100, copy.deepcopy(peer.loaded), copy.deepcopy(peer.info))
        for values in peer.state()['streams'].values():
            self.assertTrue(all(name.startswith('octa_clock_') for name in values))
        self.assertEqual([name for name, _arguments in peer.loaded.values()], ['module-null-sink'])


if __name__ == '__main__':
    unittest.main()
