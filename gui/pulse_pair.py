# SPDX-License-Identifier: GPL-2.0-only
"""Experimental joint base/EXP PulseAudio clock and client restoration."""

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import re
import shutil
import subprocess

from . import aggregate, pair_clock, pulse_audio as audio
from .devices import discover
from .pulse_config import PROFILE


def server_info():
    if shutil.which('pactl') is None:
        return None
    try:
        info = json.loads(audio.pulse('-f', 'json', 'info'))
    except subprocess.CalledProcessError:
        return None
    if info.get('server_name') != 'pulseaudio':
        raise ValueError('Joint desktop clock changes currently require native PulseAudio.')
    return info


def verify_card(device, card):
    """Compare the retained PA card identity with current udev serial properties."""
    output = subprocess.check_output(['udevadm', 'info', '--query=property', '--path',
                                      f'/sys/class/sound/card{device.card_index}'], text=True, timeout=5)
    if len(output) > 65536:
        raise ValueError('Oversized udev identity response.')
    values = dict(line.split('=', 1) for line in output.splitlines() if '=' in line)
    properties = card.get('properties', {})
    expected = {'ID_VENDOR_ID': '0582', 'ID_MODEL_ID': device.product_id, 'ID_SERIAL_SHORT': device.serial}
    if (not device.serial or any(values.get(key) != value for key, value in expected.items()) or
            not values.get('ID_SERIAL') or properties.get('device.serial') != values['ID_SERIAL'] or
            properties.get('device.vendor.id') != '0582' or
            properties.get('device.product.id') != device.product_id or
            str(properties.get('alsa.card')) != str(device.card_index)):
        raise ValueError('PulseAudio card identity no longer matches the selected USB unit.')


@dataclass
class Unit:
    controller: audio.Controller
    card: dict | None = None
    nodes: dict = field(default_factory=dict)
    streams: dict = field(default_factory=dict)
    modules: list = field(default_factory=list)
    remaps: list = field(default_factory=list)
    arguments: dict = field(default_factory=dict)
    holding: tuple | None = None
    owner: str | None = None
    files_attempted: bool = False


@dataclass(frozen=True)
class Remap:
    name: str
    arguments: dict
    kind: str
    master_pair: tuple | None


def remap_plan(unit):
    nodes = {node['name']: node for values in unit.nodes.values() for node in values}
    result = []
    for index, name, original in unit.modules[1:]:
        kind = 'sink' if name == 'module-remap-sink' else 'source'
        children = [node for node in unit.nodes[kind] if str(node.get('owner_module')) == index]
        if len(children) != 1:
            raise ValueError('Cannot preserve the remap endpoint identity.')
        master = nodes[original['master']]
        arguments = {**original, f'{kind}_name': children[0]['name']}
        key = None
        if str(master.get('owner_module')) == unit.modules[0][0]:
            channels = master['channel_map'].split(',')
            mapping = arguments.get('master_channel_map', children[0]['channel_map']).split(',')
            if len(mapping) != 2 or any(channel not in channels for channel in mapping):
                raise ValueError('A remap must explicitly select two known master channels.')
            indices = [channels.index(channel) for channel in mapping]
            if indices[0] // 2 != indices[1] // 2:
                raise ValueError('A remap spanning stereo pairs cannot use the native stereo profile.')
            key = audio.pair_key(master, kind)
            if len(channels) > 2:
                first = (indices[0] // 2) * 2 + 1
                key = (key[0], first, first + 1, key[3])
            arguments['master_channel_map'] = ','.join(('front-left', 'front-right')[index % 2] for index in indices)
        audio.module_arguments(arguments)
        result.append(Remap(name, arguments, kind, key))
    return result


def ordered_modules(owner, nodes, modules):
    owners = {str(node.get('owner_module')) for values in nodes.values() for node in values} | {owner}
    names = {node['name']: str(node.get('owner_module')) for values in nodes.values() for node in values}
    result = [(owner, *modules[owner])]
    pending = {key: value for key, value in modules.items() if key in owners and key != owner}
    while pending:
        ready = [key for key, (_name, arguments) in pending.items()
                 if names.get(arguments.get('master')) in {entry[0] for entry in result}]
        if not ready:
            raise ValueError('Cannot restore the PulseAudio remap dependency graph.')
        result.extend((key, *pending.pop(key)) for key in ready)
    return result


def untracked_module_stream(stream, kind, nodes, modules):
    """Allow only native clients, peak readers and captured remap master edges."""
    if stream.get('driver') == 'protocol-native.c' or stream.get('resample_method') == 'peaks':
        return False
    owner = str(stream.get('owner_module'))
    expected = f'module-remap-{kind}'
    arguments = next((arguments for index, name, arguments in modules
                      if index == owner and name == expected), None)
    if arguments is None or stream.get('driver') != expected + '.c':
        return True
    master = next((node for node in nodes[kind] if node['name'] == arguments.get('master')), None)
    return master is None or stream[kind] != master['index']


class Transaction:
    def __init__(self, binary, plan, *, cancelled=None):
        self.binary, self.plan = Path(binary), plan
        self.units = [Unit(audio.Controller(binary, device)) for device in (plan.base, plan.expansion)]
        self.info = None
        self.clock_attempted = False
        self.cancelled = cancelled

    def check_cancelled(self):
        if self.cancelled is not None and self.cancelled.is_set():
            raise pair_clock.ClockCancelled('Paired clock operation cancelled.')

    def present(self):
        devices = aggregate.selected(discover(), self.plan.base.identity, self.plan.expansion.identity)
        if devices != (self.plan.base, self.plan.expansion):
            raise ValueError('The selected pair changed during the desktop clock transaction.')

    def inspect(self, rate=None):
        self.present()
        plan = aggregate.inspect(self.binary, self.plan.base.identity, self.plan.expansion.identity,
                                 require_master=True)
        if (plan.base, plan.expansion) != (self.plan.base, self.plan.expansion) or \
                (rate is not None and plan.rate != rate):
            raise ValueError('The selected pair did not settle at the expected clock and identities.')
        return plan

    def snapshot(self, rate):
        self.info = server_info()
        if self.info is None:
            return
        original = audio.modules()
        for unit in self.units:
            controller = unit.controller
            unit.card = controller.card()
            if unit.card is None:
                continue
            verify_card(controller.device, unit.card)
            unit.owner = str(unit.card['owner_module'])
            if original.get(unit.owner, (None,))[0] != 'module-alsa-card':
                raise ValueError('The selected PulseAudio card has an unsupported owner.')
            unit.nodes, unit.streams, _old = controller.snapshot(unit.owner, original)
            unit.modules = ordered_modules(unit.owner, unit.nodes, original)
            for kind, category in (('sink', 'sink-inputs'), ('source', 'source-outputs')):
                indices = {node['index'] for node in unit.nodes[kind]}
                if any(stream[kind] in indices and untracked_module_stream(stream, kind, unit.nodes, unit.modules)
                       for stream in audio.document(category)):
                    raise ValueError('Stop module-owned PulseAudio streams before changing the paired clock.')
            controller.validate_streams(unit.streams, rate)
            unit.remaps = remap_plan(unit)
            for _index, _name, arguments in unit.modules:
                audio.module_arguments(arguments)
            unit.arguments = controller.profile_arguments(original[unit.owner][1], rate)
        owners = [unit.owner for unit in self.units if unit.card is not None]
        if len(set(owners)) != len(owners):
            raise ValueError('The selected pair must have distinct PulseAudio owners.')
        self.present()

    def load(self, unit, arguments, profile):
        self.present()
        owner = audio.load_module('module-alsa-card', {**arguments, 'profile': 'off'})
        unit.owner = owner
        card = next((card for card in audio.document('cards') if str(card['owner_module']) == owner), None)
        if card is None:
            raise ValueError('The new PulseAudio card disappeared before profile activation.')
        verify_card(unit.controller.device, card)
        self.present()
        audio.activate_profile(owner, profile)
        return owner

    def park(self, unit, current):
        if unit.holding is None:
            return
        nodes = {'sink': [], 'source': []}
        modules = []
        if unit.owner in current:
            nodes, _streams, _modules = unit.controller.snapshot(unit.owner, current)
            modules = ordered_modules(unit.owner, nodes, current)
        for kind, category in (('sink', 'sink-inputs'), ('source', 'source-outputs')):
            attached = {node['index'] for node in nodes[kind]}
            previous = {stream['index']: stream for stream, _node in unit.streams[kind]}
            destination = unit.holding[1] + ('.monitor' if kind == 'source' else '')
            temporary = next((node['index'] for node in audio.document(f'{kind}s') if node['name'] == destination), None)
            for stream in audio.document(category):
                if stream[kind] == temporary:
                    continue
                if stream[kind] in attached and untracked_module_stream(stream, kind, nodes, modules):
                    raise ValueError('An untracked module stream prevents safe paired audio recovery.')
                if stream['index'] in previous or (stream[kind] in attached and
                        stream.get('driver') == 'protocol-native.c' and stream.get('resample_method') != 'peaks'):
                    audio.move_stream(kind, stream['index'], destination, controls=previous.get(stream['index']))
            if self.info.get(f'default_{kind}_name') in {node['name'] for node in unit.nodes[kind]}:
                audio.pulse(f'set-default-{kind}', destination)

    def load_remaps(self, unit):
        for remap in unit.remaps:
            arguments = dict(remap.arguments)
            if remap.master_pair is not None:
                targets = [node for node in audio.document(f'{remap.kind}s') if str(node.get('owner_module')) == unit.owner]
                master = audio.by_pair(targets, remap.kind).get(remap.master_pair)
                if master is None:
                    raise ValueError('The new remap master pair is unavailable.')
                arguments['master'] = master['name']
            audio.load_module(remap.name, arguments)

    def levels(self, unit, *, original):
        current, _streams, _modules = unit.controller.snapshot(unit.owner, audio.modules())
        old_owner = unit.modules[0][0]
        if not original:
            roots = {kind: [node for node in values if str(node.get('owner_module')) == old_owner]
                     for kind, values in unit.nodes.items()}
            unit.controller.restore_levels(unit.owner, audio.inherited_controls(roots))
            monitors = audio.by_pair([node for node in current['source'] if
                                      str(node.get('owner_module')) == unit.owner], 'source')
            for old in roots['source']:
                if not old['name'].endswith('.monitor') or len(old['channel_map'].split(',')) <= 2:
                    continue
                values = list(old['volume'].values())
                for offset in range(0, len(values), 2):
                    target = monitors.get(('playback', offset + 1, offset + 2, True))
                    if target is None or offset + 1 >= len(values):
                        raise ValueError('The legacy monitor channels cannot be restored.')
                    audio.pulse('set-source-volume', target['name'], values[offset]['value'], values[offset + 1]['value'])
                    audio.pulse('set-source-mute', target['name'], int(old['mute']))
        for kind in ('sink', 'source'):
            names = {node['name']: node for node in current[kind]}
            for old in unit.nodes[kind]:
                if not original and str(old.get('owner_module')) == old_owner:
                    continue
                if old['name'] not in names or names[old['name']]['channel_map'] != old['channel_map']:
                    raise ValueError('An original PulseAudio endpoint could not be restored.')
                audio.pulse(f'set-{kind}-volume', old['name'], *(v['value'] for v in old['volume'].values()))
                audio.pulse(f'set-{kind}-mute', old['name'], int(old['mute']))

    def resume(self, unit, *, original):
        current, _streams, _modules = unit.controller.snapshot(unit.owner, audio.modules())
        for kind, category in (('sink', 'sink-inputs'), ('source', 'source-outputs')):
            old_nodes = unit.nodes[kind]
            names = {node['name']: node for node in current[kind]}
            roots = [node for node in current[kind] if str(node.get('owner_module')) == unit.owner]
            pairs = audio.by_pair(roots, kind)
            def target(old):
                if original or str(old.get('owner_module')) != unit.modules[0][0]:
                    return names[old['name']]['name']
                node = pairs.get(audio.pair_key(old, kind)) or pairs[('playback' if kind == 'sink' else 'recording', 1, 2, False)]
                return node['name']
            default = next((node for node in old_nodes if node['name'] == self.info.get(f'default_{kind}_name')), None)
            fallback = default or next((node for node in old_nodes if audio.pair_key(node, kind) ==
                                       ('playback' if kind == 'sink' else 'recording', 1, 2, False)), None)
            previous = {stream['index']: node for stream, node in unit.streams[kind]}
            controls = {stream['index']: stream for stream, _node in unit.streams[kind]}
            temporary = next((node['index'] for node in audio.document(f'{kind}s') if
                              node['name'] == unit.holding[1] + ('.monitor' if kind == 'source' else '')), None)
            for stream in audio.document(category):
                if stream['index'] in previous or (stream[kind] == temporary and fallback):
                    audio.move_stream(kind, stream['index'], target(previous.get(stream['index'], fallback)),
                                      controls=controls.get(stream['index']))
            if default:
                audio.pulse(f'set-default-{kind}', target(default))

    def recover(self):
        current = audio.modules() if self.info is not None else {}
        for unit in self.units:
            self.park(unit, current)
        for unit in reversed(self.units):
            if unit.card is not None and unit.owner not in {entry[0] for entry in unit.modules}:
                if unit.owner in audio.modules():
                    nodes, _streams, _old = unit.controller.snapshot(unit.owner, audio.modules())
                    for index, _name, _arguments in reversed(ordered_modules(unit.owner, nodes, audio.modules())):
                        audio.pulse('unload-module', index)
                unit.owner = None
        for unit in reversed(self.units):
            if unit.files_attempted:
                unit.controller.files.restore()
        if self.clock_attempted:
            pair_clock.change(self.binary, self.inspect(), self.plan.rate)
        self.inspect(self.plan.rate)
        for unit in reversed(self.units):
            if unit.card is None:
                continue
            current = audio.modules()
            old_owner, _name, arguments = unit.modules[0]
            if old_owner in current:
                unit.owner = old_owner
            else:
                self.load(unit, {**arguments, 'device_id': unit.controller.device.card_id, 'rate': self.plan.rate},
                          unit.card['active_profile'])
            for index, name, arguments in unit.modules[1:]:
                if index not in current:
                    audio.load_module(name, arguments)
        self.inspect(self.plan.rate)
        for unit in self.units:
            if unit.card is not None:
                self.levels(unit, original=True)
        for unit in self.units:
            if unit.holding is not None:
                self.resume(unit, original=True)

    def cleanup(self):
        for unit in self.units:
            if unit.holding is None:
                continue
            for kind, category in (('sink', 'sink-inputs'), ('source', 'source-outputs')):
                temporary = {node['index'] for node in audio.document(f'{kind}s') if
                             node['name'] == unit.holding[1] + ('.monitor' if kind == 'source' else '')}
                if any(stream[kind] in temporary for stream in audio.document(category)):
                    raise RuntimeError('Temporary endpoints still have clients and are retained; audio recovery is incomplete.')
        errors = []
        for unit in self.units:
            if unit.holding is not None:
                try:
                    audio.pulse('unload-module', unit.holding[0])
                except (OSError, subprocess.SubprocessError) as error:
                    errors.append(error)
        if errors:
            raise RuntimeError('Audio endpoints are active, but temporary endpoint cleanup failed.') from errors[0]

    def run(self, rate):
        self.check_cancelled()
        self.snapshot(rate)
        try:
            for unit in self.units:
                self.check_cancelled()
                if unit.card is not None:
                    unit.holding = unit.controller.hold(unit.nodes, unit.streams, self.info)
            self.present()
            for unit in self.units:
                for index, _name, _arguments in reversed(unit.modules):
                    self.check_cancelled()
                    if index in audio.modules():
                        audio.pulse('unload-module', index)
            for unit in self.units:
                self.check_cancelled()
                if unit.card is not None:
                    unit.files_attempted = True
                    unit.controller.files.install(unit.controller.device.card_id, rate)
            self.clock_attempted = True
            if self.cancelled is None:
                pair_clock.change(self.binary, self.plan, rate)
            else:
                pair_clock.change(self.binary, self.plan, rate, cancelled=self.cancelled)
            result = self.inspect(rate)
            for unit in reversed(self.units):
                self.check_cancelled()
                if unit.card is not None:
                    self.load(unit, unit.arguments, PROFILE)
                    unit.controller.validate_endpoints(unit.owner, rate)
                    self.load_remaps(unit)
            result = self.inspect(rate)
            for unit in self.units:
                self.check_cancelled()
                if unit.card is not None:
                    self.levels(unit, original=False)
            for unit in self.units:
                self.check_cancelled()
                if unit.card is not None:
                    self.resume(unit, original=False)
            self.check_cancelled()
        except BaseException as primary:
            try:
                self.recover()
            except BaseException as recovery:
                raise RuntimeError('Paired audio recovery is incomplete. Temporary endpoints are retained; '
                                   f'check the audio service. Change: {primary}; recovery: {recovery}') from recovery
            self.cleanup()
            raise
        self.cleanup()
        return result


def change_rate(binary, base_id, expansion_id, rate, *, expected_devices=None, cancelled=None):
    if os.geteuid() == 0:
        raise ValueError('Run the paired desktop clock transaction as the regular user.')
    if type(rate) is not int or rate not in aggregate.RATES:
        raise ValueError('The two-unit rate must be 44.1, 48 or 96 kHz.')
    devices = aggregate.selected(discover(), base_id, expansion_id)
    if expected_devices is not None and devices != expected_devices:
        raise ValueError('The selected pair changed after preparing its aggregate configuration.')
    if (not all(re.fullmatch(r'[A-Za-z0-9_-]{1,64}', device.serial) for device in devices) or
            devices[0].serial == devices[1].serial):
        raise ValueError('Paired rate changes require two distinct device serials.')
    executable = Path(binary) / 'octa-pair-clock'
    if not executable.is_file() or not os.access(executable, os.X_OK):
        raise ValueError('Install the native octa-pair-clock backend before changing the paired rate.')
    cards = tuple(device.card_id for device in devices)
    lock = audio.configuration_lock(cards) if cancelled is None else audio.configuration_lock(cards, cancelled=cancelled)
    with lock:
        plan = aggregate.inspect(binary, base_id, expansion_id, require_master=True)
        if (plan.base, plan.expansion) != devices:
            raise ValueError('The pair changed while waiting for the desktop audio lock.')
        return Transaction(binary, plan, cancelled=cancelled).run(rate)
