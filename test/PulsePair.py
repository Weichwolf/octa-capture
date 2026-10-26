# SPDX-License-Identifier: GPL-2.0-only
"""Joint clock transactions against a private two-card PulseAudio graph model."""

import copy
from contextlib import ExitStack, nullcontext
from dataclasses import replace
import json
from pathlib import Path
import sys
import tempfile
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


class Peer:
    def __init__(self):
        self.rate = OLD
        self.sequence = 10
        self.loaded = {}
        self.cards = {}
        self.nodes = {'sink': [], 'source': []}
        self.streams = {'sink': [], 'source': []}
        self.info = {'server_name': 'pulseaudio'}
        self.calls = []
        self.holds = 0
        self.fail_at = None
        self.failed = False
        self.persistent = set()
        self.new_clients = False
        self.on_event = None
        for device in DEVICES:
            owner = self.load('module-alsa-card', {'device_id': device.card_id, 'rate': OLD, 'profile': 'off'})
            self.activate(owner, PROFILE)
        for kind, pairs in (('sink', (3, 7)), ('source', (9, 11))):
            for index, (device, first) in enumerate(zip(DEVICES, pairs, strict=True)):
                node = next(node for node in self.nodes[kind] if node['name'] == self.name(device, kind, first))
                self.streams[kind].append({'index': index + 1, kind: node['index'], 'driver': 'protocol-native.c',
                                           'channel_map': 'front-left,front-right'})
            self.info[f'default_{kind}_name'] = self.name(DEVICES[kind == 'source'], kind, pairs[kind == 'source'])
        self.calls.clear()

    @staticmethod
    def name(device, kind, first):
        return f'alsa_{kind}.{device.card_id}.{"playback" if kind == "sink" else "recording"}-{first}-{first + 1}'

    def event(self, name):
        self.calls.append(name)
        if self.on_event:
            self.on_event(name)
        if name in self.persistent or (name == self.fail_at and not self.failed):
            self.failed = True
            raise ValueError(f'Injected {name}')

    def node(self, owner, kind, name):
        self.sequence += 1
        value = {'index': self.sequence, 'owner_module': owner, 'name': name,
                 'channel_map': 'front-left,front-right', 'mute': name.endswith('3-4'),
                 'volume': {'front-left': {'value': 24000}, 'front-right': {'value': 40000}}}
        self.nodes[kind].append(value)
        return value

    def load(self, name, arguments):
        if name == 'module-null-sink':
            self.event(f'hold:{self.holds}')
            self.holds += 1
        elif name == 'module-alsa-card':
            self.event(f'load:{arguments["device_id"]}')
        else:
            self.event('remap')
        self.sequence += 1
        owner = str(self.sequence)
        self.loaded[owner] = name, copy.deepcopy(arguments)
        if name == 'module-null-sink':
            self.node(owner, 'sink', arguments['sink_name'])
            self.node(owner, 'source', arguments['sink_name'] + '.monitor')
        elif name == 'module-alsa-card':
            device = next(device for device in DEVICES if device.card_id == arguments['device_id'])
            self.cards[owner] = {'owner_module': owner, 'name': 'alsa_card.' + device.card_id,
                                 'active_profile': 'off', 'properties': {'alsa.card': str(device.card_index),
                                 'device.serial': f'Roland_OCTA_{device.serial}', 'device.vendor.id': '0582',
                                 'device.product.id': device.product_id}}
        else:
            kind = 'sink' if name == 'module-remap-sink' else 'source'
            master = next(node for node in self.nodes[kind] if node['name'] == arguments['master'])
            if any(channel not in master['channel_map'].split(',') for channel in
                   arguments.get('master_channel_map', master['channel_map']).split(',')):
                raise AssertionError('Remap selects channels absent from its master')
            node = self.node(owner, kind, arguments[f'{kind}_name'])
            if kind == 'sink':
                self.node(owner, 'source', node['name'] + '.monitor')
        return owner

    def activate(self, owner, profile):
        card = self.cards[owner]
        self.event(f'activate:{card["name"].split(".")[-1]}')
        card['active_profile'] = profile
        if profile != 'off':
            device = DEVICES[int(card['properties']['alsa.card'])]
            for kind, count in (('sink', 10), ('source', 12)):
                for first in range(1, count, 2):
                    node = self.node(owner, kind, self.name(device, kind, first))
                    if kind == 'sink':
                        self.node(owner, 'source', node['name'] + '.monitor')

    def document(self, category):
        if category == 'cards':
            return copy.deepcopy(list(self.cards.values()))
        if category in ('sinks', 'sources'):
            return copy.deepcopy(self.nodes[category[:-1]])
        return copy.deepcopy(self.streams['sink' if category == 'sink-inputs' else 'source'])

    def pulse(self, *arguments):
        operation = arguments[0]
        if operation == '-f':
            return json.dumps(self.info)
        if operation == 'unload-module':
            owner = str(arguments[1])
            card = self.cards.get(owner)
            self.event('unload:' + (card['name'].split('.')[-1] if card else 'holding'))
            for kind in ('sink', 'source'):
                removed = {node['index'] for node in self.nodes[kind] if str(node['owner_module']) == owner}
                if any(stream[kind] in removed for stream in self.streams[kind]):
                    raise AssertionError('A module with attached clients was unloaded')
                self.nodes[kind] = [node for node in self.nodes[kind] if str(node['owner_module']) != owner]
            self.cards.pop(owner, None)
            del self.loaded[owner]
        elif operation == 'set-card-profile':
            owner = next(owner for owner, card in self.cards.items() if card['name'] == arguments[1])
            self.activate(owner, arguments[2])
        elif operation.startswith('set-default-'):
            self.info[f'default_{operation.removeprefix("set-default-")}_name'] = arguments[1]
        elif operation.startswith('move-'):
            kind = 'sink' if operation == 'move-sink-input' else 'source'
            target = next(node for node in self.nodes[kind] if node['name'] == arguments[2])
            stream = next(stream for stream in self.streams[kind] if stream['index'] == arguments[1])
            if stream[kind] == target['index']:
                raise AssertionError('A held stream was needlessly moved to the same endpoint')
            self.event('park' if target['name'].startswith('octa_clock_') else 'resume')
            stream[kind] = target['index']
        elif operation.startswith('set-'):
            _, kind, field = operation.split('-')
            node = next(node for node in self.nodes[kind] if node['name'] == arguments[1])
            self.event('levels')
            if field == 'mute':
                node['mute'] = bool(arguments[2])
            else:
                for value, level in zip(node['volume'].values(), arguments[2:], strict=True):
                    value['value'] = level
        else:
            raise AssertionError(arguments)
        return ''

    def clock(self, _binary, plan, rate):
        if self.cards:
            raise AssertionError('Clock prepared before BOTH PulseAudio cards were released')
        if plan.rate != self.rate:
            raise AssertionError('Clock request does not match the inspected previous rate')
        self.rate = rate
        self.event('clock')
        if self.new_clients:
            for kind in ('sink', 'source'):
                for index, node in enumerate(node for node in self.nodes[kind] if
                        node['name'].startswith('octa_clock_')):
                    if not any(stream['index'] == index + 10 for stream in self.streams[kind]):
                        self.streams[kind].append({'index': index + 10, kind: node['index'],
                                                  'driver': 'protocol-native.c', 'channel_map': 'front-left,front-right'})

    def inspect(self, *_arguments, **_keywords):
        self.event('verify')
        return aggregate.Plan(BASE, EXP, self.rate)

    def state(self):
        result = {'rate': self.rate, 'defaults': copy.deepcopy(self.info), 'streams': {}, 'levels': {}}
        for kind in ('sink', 'source'):
            nodes = {node['index']: node for node in self.nodes[kind]}
            result['streams'][kind] = {stream['index']: nodes[stream[kind]]['name'] for stream in self.streams[kind]}
            result['levels'][kind] = {node['name']: (node['volume'], node['mute']) for node in self.nodes[kind]}
        return copy.deepcopy(result)

    def remap(self, device, kind, first, name, master=None):
        owner = self.load(f'module-remap-{kind}', {'master': master or self.name(device, kind, first),
                          f'{kind}_name': name, 'master_channel_map': 'front-left,front-right'})
        node = next(node for node in self.nodes[kind] if node['owner_module'] == owner)
        node['volume']['front-left']['value'] = 16000
        node['mute'] = True
        stream = self.streams[kind][device.card_index]
        stream[kind] = node['index']
        self.info[f'default_{kind}_name'] = name
        return node


class Coordination(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.stack.enter_context(patch('pathlib.Path.home', return_value=self.root))
        self.stack.enter_context(patch.object(pulse_pair.os, 'geteuid', return_value=1000))
        self.peer = Peer()
        self.stack.enter_context(patch.object(audio, 'pulse', self.peer.pulse))
        self.stack.enter_context(patch.object(audio, 'document', self.peer.document))
        self.stack.enter_context(patch.object(audio, 'modules', lambda: copy.deepcopy(self.peer.loaded)))
        self.stack.enter_context(patch.object(audio, 'load_module', self.peer.load))
        self.stack.enter_context(patch.object(pulse_pair, 'server_info', lambda: copy.deepcopy(self.peer.info)))
        self.stack.enter_context(patch.object(pulse_pair, 'verify_card'))
        self.stack.enter_context(patch.object(pulse_pair, 'discover', return_value=DEVICES))
        self.stack.enter_context(patch.object(aggregate, 'inspect', self.peer.inspect))
        self.stack.enter_context(patch.object(pulse_pair.pair_clock, 'change', self.peer.clock))
        (self.root / '.asoundrc').write_text('# Existing unrelated configuration\n')

    def transaction(self):
        return pulse_pair.Transaction('/unused', aggregate.Plan(BASE, EXP, OLD))

    def test_both_cards_are_held_before_either_unloads_and_verified_before_clients_resume(self):
        before = self.peer.state()
        self.assertEqual(self.transaction().run(NEW).rate, NEW)
        self.assertEqual(self.peer.state(), {**before, 'rate': NEW})
        calls = self.peer.calls
        self.assertLess(calls.index('hold:1'), calls.index('unload:BASE'))
        self.assertLess(calls.index('unload:EXP'), calls.index('clock'))
        self.assertLess(calls.index('activate:EXP'), calls.index('activate:BASE'))
        self.assertLess(max(index for index, value in enumerate(calls) if value == 'verify'), calls.index('resume'))
        self.assertLess(max(index for index, value in enumerate(calls) if value == 'levels'), calls.index('resume'))
        self.assertEqual(len(self.peer.loaded), 2)
        self.assertIn('/pulse/BASE/alsa.conf>', (self.root / '.asoundrc').read_text())
        self.assertIn('/pulse/EXP/alsa.conf>', (self.root / '.asoundrc').read_text())

    def test_each_forward_stage_recovers_original_audio_and_shared_files(self):
        for stage in ('hold:0', 'hold:1', 'unload:BASE', 'unload:EXP', 'clock', 'load:EXP',
                      'activate:EXP', 'load:BASE', 'activate:BASE', 'levels', 'resume'):
            with self.subTest(stage=stage):
                self.peer.__init__()
                self.peer.fail_at = stage
                before = self.peer.state()
                configuration = (self.root / '.asoundrc').read_bytes()
                with self.assertRaisesRegex(ValueError, 'Injected'):
                    self.transaction().run(NEW)
                self.assertEqual(self.peer.state(), before)
                self.assertEqual((self.root / '.asoundrc').read_bytes(), configuration)
                self.assertEqual(len(self.peer.loaded), 2)

    def test_failed_recovery_retains_both_holding_nodes_and_does_not_rescue_to_another_output(self):
        self.peer.persistent = {'clock'}
        with self.assertRaisesRegex(RuntimeError, 'recovery is incomplete'):
            self.transaction().run(NEW)
        self.assertEqual([name for name, _args in self.peer.loaded.values()], ['module-null-sink'] * 2)
        for streams in self.peer.state()['streams'].values():
            self.assertTrue(all(name.startswith('octa_clock_') for name in streams.values()))

    def test_clients_created_on_either_holding_node_return_to_that_unit(self):
        self.peer.new_clients = True
        self.transaction().run(NEW)
        for kind, names in self.peer.state()['streams'].items():
            self.assertIn('.BASE.', names[10])
            self.assertIn('.EXP.', names[11])

    def test_remap_chains_keep_their_names_levels_defaults_and_clients(self):
        first = self.peer.remap(BASE, 'sink', 3, 'private-base-output')
        self.peer.remap(BASE, 'sink', 3, 'private-base-child', first['name'])
        self.peer.remap(EXP, 'source', 11, 'private-exp-input')
        before = self.peer.state()
        self.transaction().run(NEW)
        self.assertEqual(self.peer.state(), {**before, 'rate': NEW})
        self.assertEqual(len(self.peer.loaded), 5)

    def test_remap_load_failure_restores_original_chains_before_resuming(self):
        first = self.peer.remap(BASE, 'sink', 3, 'private-base-output')
        self.peer.remap(BASE, 'sink', 3, 'private-base-child', first['name'])
        before = self.peer.state()
        self.peer.fail_at = 'remap'
        with self.assertRaisesRegex(ValueError, 'Injected remap'):
            self.transaction().run(NEW)
        self.assertEqual(self.peer.state(), before)
        self.assertEqual(len(self.peer.loaded), 4)

    def test_failure_after_one_resumed_client_parks_it_again_before_unloading(self):
        before = self.peer.state()
        resumed = 0
        def fail_second(name):
            nonlocal resumed
            if name == 'resume':
                resumed += 1
                if resumed == 2:
                    raise ValueError('Injected partial resume')
        self.peer.on_event = fail_second
        with self.assertRaisesRegex(ValueError, 'partial resume'):
            self.transaction().run(NEW)
        self.assertEqual(self.peer.state(), before)

    def test_second_configuration_partial_write_is_rolled_back_before_the_first(self):
        transaction = self.transaction()
        before = self.peer.state()
        original = (self.root / '.asoundrc').read_bytes()
        order = []
        for unit in transaction.units:
            restore = unit.controller.files.restore
            unit.controller.files.restore = lambda restore=restore, name=unit.controller.device.card_id: (order.append(name), restore())
        install = transaction.units[1].controller.files.install
        def fail(*arguments):
            install(*arguments)
            raise OSError('Injected disk failure')
        transaction.units[1].controller.files.install = fail
        with self.assertRaisesRegex(OSError, 'disk failure'):
            transaction.run(NEW)
        self.assertEqual(order, ['EXP', 'BASE'])
        self.assertEqual((self.root / '.asoundrc').read_bytes(), original)
        self.assertEqual(self.peer.state(), before)

    def test_monitor_recordings_and_a_source_remap_keep_the_playback_monitor(self):
        monitor = self.peer.name(BASE, 'sink', 3) + '.monitor'
        self.peer.remap(BASE, 'source', 3, 'private-monitor-recording', master=monitor)
        before = self.peer.state()
        self.transaction().run(NEW)
        self.assertEqual(self.peer.state(), {**before, 'rate': NEW})

    def test_headless_transaction_does_not_create_a_desktop_audio_graph(self):
        self.peer.cards.clear()
        self.peer.loaded.clear()
        self.peer.nodes = {'sink': [], 'source': []}
        self.peer.streams = {'sink': [], 'source': []}
        with patch.object(pulse_pair, 'server_info', return_value=None):
            self.assertEqual(self.transaction().run(NEW).rate, NEW)
        self.assertEqual(self.peer.loaded, {})
        self.assertNotIn('hold:0', self.peer.calls)

    def test_identity_loss_before_holds_has_no_audio_or_configuration_side_effect(self):
        before = self.peer.state()
        with patch.object(pulse_pair, 'verify_card', side_effect=ValueError('Wrong serial')):
            with self.assertRaisesRegex(ValueError, 'Wrong serial'):
                self.transaction().run(NEW)
        self.assertEqual(self.peer.state(), before)
        self.assertEqual(self.peer.calls, [])

    def test_module_owned_audio_is_rejected_before_unloading_any_card(self):
        self.peer.streams['sink'][0]['driver'] = 'module-loopback.c'
        before = self.peer.state()
        with self.assertRaisesRegex(ValueError, 'module-owned'):
            self.transaction().run(NEW)
        self.assertEqual(self.peer.state(), before)
        self.assertEqual(self.peer.calls, [])

    def test_legacy_multichannel_monitor_gains_and_mute_are_split_without_touching_capture(self):
        transaction = self.transaction()
        transaction.snapshot(NEW)
        unit = transaction.units[0]
        unit.nodes = {'sink': [], 'source': [{'name': 'legacy-output.monitor', 'owner_module': unit.owner,
            'channel_map': ','.join(f'aux{i}' for i in range(10)), 'mute': True,
            'volume': {f'aux{i}': {'value': (i + 1) * 1000} for i in range(10)}}]}
        before = self.peer.state()['levels']['source']
        transaction.levels(unit, original=False)
        after = self.peer.state()['levels']['source']
        for name, (volume, mute) in after.items():
            if name.startswith('alsa_sink.BASE.') and name.endswith('.monitor'):
                first = audio.pair_key({'name': name}, 'source')[1]
                self.assertEqual([value['value'] for value in volume.values()], [first * 1000, (first + 1) * 1000])
                self.assertTrue(mute)
            else:
                self.assertEqual((volume, mute), before[name])

    def test_public_entry_rechecks_identity_after_acquiring_both_card_locks(self):
        executable = self.root / 'octa-pair-clock'
        executable.touch(mode=0o700)
        with patch.object(audio, 'configuration_lock', return_value=nullcontext()) as lock:
            result = pulse_pair.change_rate(self.root, BASE.identity, EXP.identity, NEW,
                                            expected_devices=DEVICES)
        self.assertEqual(result.rate, NEW)
        lock.assert_called_once_with(('BASE', 'EXP'))

    def test_renumbering_while_waiting_for_locks_fails_before_holding_audio(self):
        executable = self.root / 'octa-pair-clock'
        executable.touch(mode=0o700)
        before = self.peer.state()
        with patch.object(audio, 'configuration_lock', return_value=nullcontext()), \
                patch.object(aggregate, 'inspect', return_value=aggregate.Plan(replace(BASE, card_index=3), EXP, OLD)):
            with self.assertRaisesRegex(ValueError, 'waiting'):
                pulse_pair.change_rate(self.root, BASE.identity, EXP.identity, NEW)
        self.assertEqual(self.peer.state(), before)
        self.assertEqual(self.peer.calls, [])


class Identities(unittest.TestCase):
    def test_pulse_properties_must_match_current_udev_vid_pid_and_both_serial_forms(self):
        properties = {'alsa.card': '0', 'device.serial': 'Roland_OCTA_BASE',
                      'device.vendor.id': '0582', 'device.product.id': '0120'}
        udev = 'ID_VENDOR_ID=0582\nID_MODEL_ID=0120\nID_SERIAL_SHORT=BASE\nID_SERIAL=Roland_OCTA_BASE\n'
        with patch.object(pulse_pair.subprocess, 'check_output', return_value=udev) as command:
            pulse_pair.verify_card(BASE, {'properties': properties})
            self.assertEqual(command.call_args.args[0],
                             ['udevadm', 'info', '--query=property', '--path', '/sys/class/sound/card0'])
            for changes in ({'alsa.card': '1'}, {'device.serial': 'Roland_OCTA_REPLACEMENT'},
                            {'device.product.id': '0121'}, {'device.vendor.id': '1234'}):
                with self.subTest(changes=changes), self.assertRaises(ValueError):
                    pulse_pair.verify_card(BASE, {'properties': {**properties, **changes}})
        for text in ('', udev.replace('SERIAL_SHORT=BASE', 'SERIAL_SHORT=REPLACEMENT'),
                     udev.replace('MODEL_ID=0120', 'MODEL_ID=0121'), 'x' * 65537):
            with patch.object(pulse_pair.subprocess, 'check_output', return_value=text), self.assertRaises(ValueError):
                pulse_pair.verify_card(BASE, {'properties': properties})

    def test_remap_master_channel_translation_preserves_offsets_and_reversal(self):
        parent = {'name': 'old-output', 'owner_module': '1', 'channel_map': ','.join(f'aux{i}' for i in range(10))}
        child = {'name': 'private-output', 'owner_module': '2', 'channel_map': 'front-left,front-right'}
        unit = pulse_pair.Unit(audio.Controller('/unused', BASE), nodes={'sink': [parent, child], 'source': []})
        for old_map, expected in (('aux2,aux3', 'front-left,front-right'), ('aux3,aux2', 'front-right,front-left')):
            unit.modules = [('1', 'module-alsa-card', {}), ('2', 'module-remap-sink',
                            {'master': 'old-output', 'master_channel_map': old_map})]
            remap = pulse_pair.remap_plan(unit)[0]
            self.assertEqual(remap.master_pair, ('playback', 3, 4, False))
            self.assertEqual(remap.arguments['master_channel_map'], expected)
            self.assertEqual(remap.arguments['sink_name'], 'private-output')
        unit.modules[1][2]['master_channel_map'] = 'aux2,aux5'
        with self.assertRaisesRegex(ValueError, 'spanning stereo pairs'):
            pulse_pair.remap_plan(unit)

    def test_non_pulseaudio_server_is_rejected_before_holding_clients(self):
        with patch.object(pulse_pair.shutil, 'which', return_value='/usr/bin/pactl'), \
                patch.object(audio, 'pulse', return_value=json.dumps({'server_name': 'PulseAudio (on PipeWire)'})):
            with self.assertRaisesRegex(ValueError, 'native PulseAudio'):
                pulse_pair.server_info()


if __name__ == '__main__':
    unittest.main()
