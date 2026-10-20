# SPDX-License-Identifier: GPL-2.0-only
"""Native stereo endpoints and coordinated PulseAudio/device clock changes."""

import argparse
import copy
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import re
import secrets
import shlex
import shutil
import signal
import subprocess
import sys

from .devices import discover
from .package_migration import migrate_legacy_service
from .pulse_config import Files, PROFILE, channel_counts


def pulse(*arguments):
    return subprocess.check_output(['pactl', *map(str, arguments)], text=True, timeout=15,
                                   env={**os.environ, 'LC_ALL': 'C'}).strip()


def document(category):
    return json.loads(pulse('-f', 'json', 'list', category))


def modules():
    return {row[0]: (row[1], dict(argument.split('=', 1) for argument in shlex.split(row[2])))
            for row in (line.split('\t') for line in pulse('list', 'short', 'modules').splitlines())
            if len(row) >= 3}


def module_arguments(arguments):
    encoded = []
    for key, value in arguments.items():
        if not re.fullmatch(r'[A-Za-z_][A-Za-z_0-9]*', key):
            raise ValueError('Invalid PulseAudio module argument.')
        value = str(value).replace('\\', '\\\\').replace('"', '\\"')
        encoded.append(f'{key}="{value}"')
    return encoded


def load_module(name, arguments):
    return pulse('load-module', name, *module_arguments(arguments))


def activate_profile(owner, profile):
    card = next((card for card in document('cards') if str(card['owner_module']) == str(owner)), None)
    if card is None:
        raise ValueError('The PulseAudio card disappeared during setup.')
    if card['active_profile'] != profile:
        pulse('set-card-profile', card['name'], profile)


def pair_key(node, kind):
    name = node['name']
    found = re.search(r'(playback|recording)[-_](\d+)[-_](\d+)(?:\.monitor)?$', name)
    monitor = name.endswith('.monitor')
    if found:
        return found[1], int(found[2]), int(found[3]), monitor
    return ('playback' if kind == 'sink' or monitor else 'recording'), 1, 2, monitor


def by_pair(nodes, kind):
    return {pair_key(node, kind): node for node in nodes}


def move_stream(kind, index, destination):
    category = 'sink-inputs' if kind == 'sink' else 'source-outputs'
    try:
        pulse('move-sink-input' if kind == 'sink' else 'move-source-output', index, destination)
    except subprocess.CalledProcessError:
        if any(stream['index'] == index for stream in document(category)):
            raise


def inherited_controls(nodes):
    """Keep effective gains when replacing a multichannel parent and its remaps."""
    result = copy.deepcopy(nodes)
    for kind, values in result.items():
        for parent in list(values):
            count = len(parent['channel_map'].split(','))
            if count <= 2 or parent['name'].endswith('.monitor'):
                continue
            direction = 'playback' if kind == 'sink' else 'recording'
            levels = list(parent['volume'].values())
            for channel in range(0, count, 2):
                key = (direction, channel + 1, channel + 2, False)
                child = by_pair(values, kind).get(key)
                if child is None or child is parent:
                    child = {'name': f'octa_{direction}_{channel + 1}_{channel + 2}',
                             'channel_map': 'front-left,front-right', 'mute': False,
                             'volume': {name: {'value': 65536} for name in ('front-left', 'front-right')}}
                    values.append(child)
                for side, value in enumerate(child['volume'].values()):
                    value['value'] = min(0xffffffff, round(value['value'] * levels[channel + side]['value'] / 65536))
                child['mute'] = child['mute'] or parent['mute']
    return result


@contextmanager
def device_lock(card_id):
    runtime = Path(os.environ.get('XDG_RUNTIME_DIR', f'/run/user/{os.getuid()}'))
    path = runtime / f'octa-pulse-{card_id}.lock'
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, 'w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


class Controller:
    def __init__(self, binary, device):
        self.binary, self.device = Path(binary), device
        self.files = Files(device.card_id)

    def status(self):
        value = subprocess.check_output([str(self.binary / 'octa-status'), f'hw:CARD={self.device.card_id}'],
                                        text=True, timeout=5)
        return json.loads(value)['sample_rate_hz']

    def prepare(self, rate):
        subprocess.run([str(self.binary / 'octa-audio-check'), str(rate), 'prepare-capture',
                        f'hw:CARD={self.device.card_id},DEV=0'], check=True, timeout=20,
                       stdout=subprocess.DEVNULL)

    def card(self):
        matches = [card for card in document('cards')
                   if str(card.get('properties', {}).get('alsa.card')) == str(self.device.card_index)]
        if len(matches) > 1:
            raise ValueError('Remove unused duplicate PulseAudio cards before changing the clock.')
        return matches[0] if matches else None

    def ensure(self, requested=None):
        with device_lock(self.device.card_id):
            card = self.card()
            current = self.status()
            # Reconnection follows the hardware; only an explicit request changes its clock.
            rate = current if requested is None else requested
            channel_counts(rate)
            if card is None:
                if requested is not None:
                    self.prepare(rate)
                return
            info = json.loads(pulse('-f', 'json', 'info'))
            if info.get('server_name') != 'pulseaudio':
                raise ValueError('Coordinated desktop changes currently require native PulseAudio.')
            original_modules = modules()
            owner = str(card['owner_module'])
            arguments = original_modules[owner][1]
            if (arguments.get('profile_set') == str(self.files.profile) and arguments.get('rate') == str(rate)
                    and card['active_profile'] == PROFILE and current == rate):
                return
            self.switch(card, rate, current, original_modules, info)

    def snapshot(self, owner, original_modules):
        all_nodes = {'sink': document('sinks'), 'source': document('sources')}
        owners = {owner}
        while True:
            names = {node['name'] for nodes in all_nodes.values() for node in nodes
                     if str(node.get('owner_module')) in owners}
            children = {index for index, (name, arguments) in original_modules.items()
                        if name in ('module-remap-sink', 'module-remap-source') and arguments.get('master') in names}
            if children <= owners:
                break
            owners |= children
        nodes = {kind: [node for node in values if str(node.get('owner_module')) in owners]
                 for kind, values in all_nodes.items()}
        streams = {}
        for kind, category in (('sink', 'sink-inputs'), ('source', 'source-outputs')):
            index = {node['index']: node for node in nodes[kind]}
            streams[kind] = [(stream, index[stream[kind]]) for stream in document(category)
                             if stream[kind] in index and stream['driver'] == 'protocol-native.c'
                             and stream.get('resample_method') != 'peaks']
        return nodes, streams, [original_modules[index] for index in original_modules if index in owners]

    def validate_streams(self, streams, rate):
        counts = dict(zip(('sink', 'source'), channel_counts(rate), strict=True))
        for kind, values in streams.items():
            for stream, node in values:
                if len(stream['channel_map'].split(',')) > 2:
                    raise ValueError('Stop multichannel PulseAudio streams before selecting stereo pairs.')
                if pair_key(node, kind)[2] > counts[kind]:
                    raise ValueError('Stop streams using channels unavailable at the requested sample rate.')

    def hold(self, nodes, streams, info):
        name = f'octa_clock_{secrets.token_hex(5)}'
        owner = load_module('module-null-sink', {'sink_name': name, 'channels': 2,
                            'sink_properties': "device.description='OCTA-CAPTURE rate change'"})
        try:
            for kind, values in streams.items():
                for stream, _node in values:
                    move_stream(kind, stream['index'], name if kind == 'sink' else name + '.monitor')
            for kind in ('sink', 'source'):
                if info.get(f'default_{kind}_name') in {node['name'] for node in nodes[kind]}:
                    pulse(f'set-default-{kind}', name if kind == 'sink' else name + '.monitor')
            return owner, name
        except BaseException:
            for kind, values in streams.items():
                for stream, node in values:
                    move_stream(kind, stream['index'], node['name'])
            for kind in ('sink', 'source'):
                if info.get(f'default_{kind}_name') in {node['name'] for node in nodes[kind]}:
                    pulse(f'set-default-{kind}', info[f'default_{kind}_name'])
            pulse('unload-module', owner)
            raise

    def restore_clients(self, owner, nodes, streams, info, holding):
        for kind, category in (('sink', 'sink-inputs'), ('source', 'source-outputs')):
            targets = [node for node in document(f'{kind}s') if str(node.get('owner_module')) == str(owner)]
            pairs = by_pair(targets, kind)
            def target(old):
                key = pair_key(old, kind)
                return pairs.get(key) or pairs[('playback' if kind == 'sink' else 'recording', 1, 2, False)]
            for old in nodes[kind]:
                if len(old['channel_map'].split(',')) != 2:
                    continue
                selected = pairs.get(pair_key(old, kind))
                if selected is None:
                    continue
                destination = selected['name']
                pulse(f'set-{kind}-volume', destination, *(value['value'] for value in old['volume'].values()))
                pulse(f'set-{kind}-mute', destination, int(old['mute']))
            default = next((node for node in nodes[kind] if node['name'] == info.get(f'default_{kind}_name')), None)
            if default:
                pulse(f'set-default-{kind}', target(default)['name'])
            previous = {stream['index']: node for stream, node in streams[kind]}
            temporary = next((node['index'] for node in document(f'{kind}s')
                              if node['name'] == (holding if kind == 'sink' else holding + '.monitor')), None)
            for stream in document(category):
                if stream['index'] in previous or (stream[kind] == temporary and default):
                    old = previous.get(stream['index'], default)
                    move_stream(kind, stream['index'], target(old)['name'])

    def switch(self, card, rate, previous_rate, original_modules, info):
        old_owner = str(card['owner_module'])
        nodes, streams, old_modules = self.snapshot(old_owner, original_modules)
        self.validate_streams(streams, rate)
        for _name, arguments in old_modules:
            module_arguments(arguments)
        arguments = {**original_modules[old_owner][1], 'profile_set': str(self.files.profile),
                     'profile': PROFILE, 'use_ucm': 'no', 'rate': rate, 'tsched': 'no',
                     'format': 's24le', 'avoid_resampling': 'no'}
        module_arguments(arguments)
        holding_owner, holding = self.hold(nodes, streams, info)
        new_owner = None
        unloaded = False
        restored = False
        try:
            pulse('unload-module', old_owner)
            unloaded = True
            self.files.install(self.device.card_id, rate)
            self.prepare(rate)
            new_owner = load_module('module-alsa-card', arguments)
            # Device-specific udev defaults keep each new card off until this point.
            activate_profile(new_owner, PROFILE)
            actual = ([node for node in document('sinks') if str(node.get('owner_module')) == new_owner],
                      [node for node in document('sources') if str(node.get('owner_module')) == new_owner
                       and not node['name'].endswith('.monitor')])
            if tuple(map(len, actual)) != tuple(count // 2 for count in channel_counts(rate)) or self.status() != rate:
                raise ValueError('PulseAudio did not restore the expected stereo endpoints and hardware clock.')
            self.restore_clients(new_owner, inherited_controls(nodes), streams, info, holding)
            restored = True
        except BaseException:
            if new_owner is not None:
                pulse('unload-module', new_owner)
            self.files.restore()
            if unloaded:
                self.prepare(previous_rate)
                restored_owner = load_module(*old_modules[0])
                activate_profile(restored_owner, card['active_profile'])
                for name, arguments in old_modules[1:]:
                    load_module(name, arguments)
                # Old remapped clients may belong to restored child modules.
                restored_nodes = {kind: document(f'{kind}s') for kind in ('sink', 'source')}
                for kind, values in nodes.items():
                    for node in values:
                        if node['name'] in {current['name'] for current in restored_nodes[kind]}:
                            pulse(f'set-{kind}-volume', node['name'], *(value['value'] for value in node['volume'].values()))
                            pulse(f'set-{kind}-mute', node['name'], int(node['mute']))
                for kind, values in streams.items():
                    for stream, old in values:
                        if old['name'] in {node['name'] for node in restored_nodes[kind]}:
                            move_stream(kind, stream['index'], old['name'])
                for kind in ('sink', 'source'):
                    if info.get(f'default_{kind}_name') in {node['name'] for node in nodes[kind]}:
                        pulse(f'set-default-{kind}', info[f'default_{kind}_name'])
                restored = True
            else:
                self.restore_clients(old_owner, nodes, streams, info, holding)
                restored = True
            raise
        finally:
            # Keep clients paused if recovery itself fails, rather than routing
            # them to an unrelated physical output through automatic rescue.
            if restored:
                pulse('unload-module', holding_owner)
            else:
                raise RuntimeError('Audio recovery is incomplete. Streams remain paused; check the audio service.')


def change_rate(binary, port, rate):
    channel_counts(rate)
    device = next((device for device in discover() if device.document(port)['selected']), None)
    available = device is not None and shutil.which('pactl') is not None
    if available:
        try:
            pulse('-f', 'json', 'info')
        except subprocess.CalledProcessError:
            available = False
    if not available:
        port = port.split(',')[0]
        pcm = port + (',DEV=0' if 'CARD=' in port else ',0')
        subprocess.run([str(Path(binary) / 'octa-audio-check'), str(rate), 'prepare-capture', pcm],
                       check=True, timeout=20, stdout=subprocess.DEVNULL)
        return
    Controller(binary, device).ensure(rate)


def reconcile(binary, sound=Path('/sys/class/sound')):
    for device in discover():
        try:
            driver = (sound / f'card{device.card_index}' / 'device/driver').resolve(strict=True)
        except OSError:
            continue
        # Hotplug activation replaces a generic card with a new dedicated card.
        if driver.name == 'snd-octa-capture':
            Controller(binary, device).ensure()


def watch(binary):
    with subprocess.Popen(['pactl', 'subscribe'], stdout=subprocess.PIPE, text=True,
                          env={**os.environ, 'LC_ALL': 'C'}) as events:
        try:
            reconcile(binary)
            for line in events.stdout:
                if line.startswith(("Event 'new'", "Event 'remove'")) and ' on card #' in line:
                    reconcile(binary)
            raise RuntimeError('PulseAudio subscription ended.')
        finally:
            events.terminate()
            try:
                events.wait(timeout=5)
            except subprocess.TimeoutExpired:
                events.kill()
                events.wait(timeout=5)


def install_service(binary):
    launcher = Path(binary).resolve() / 'octa-pulse-pairs'
    if not launcher.is_file():
        raise ValueError('Install OCTA-CAPTURE before enabling the desktop service.')
    def quote(value):
        return '"' + str(value).replace('\\', '\\\\').replace('"', '\\"').replace('%', '%%').replace('$', '$$') + '"'
    unit = Path.home() / '.config/systemd/user/octa-pulse-pairs.service'
    unit.parent.mkdir(parents=True, exist_ok=True)
    unit.write_text('[Unit]\nDescription=OCTA-CAPTURE PulseAudio stereo pairs\nAfter=pulseaudio.service\n'
                    'Wants=pulseaudio.service\nStartLimitIntervalSec=0\n\n[Service]\n'
                    f'ExecStart={quote(sys.executable)} {quote(launcher)} --watch\n'
                    'Restart=on-failure\nRestartSec=3\n\n[Install]\nWantedBy=default.target\n')
    for arguments in (('daemon-reload',), ('enable', unit.name), ('restart', unit.name)):
        subprocess.run(['systemctl', '--user', *arguments], check=True, timeout=15)


def main(*, default_bin_dir=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bin-dir', type=Path, default=default_bin_dir or Path(__file__).resolve().parents[1] / 'build/release')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--watch', action='store_true')
    mode.add_argument('--packaged-service', action='store_true', help='run the packaged user service and migrate its legacy unit')
    mode.add_argument('--install', action='store_true')
    mode.add_argument('--rate', type=int, help='coordinate a hardware sample-rate change')
    parser.add_argument('--device', default='hw:CARD=OCTACAPTURE')
    args = parser.parse_args()
    signal.signal(signal.SIGTERM, lambda *_args: (_ for _ in ()).throw(KeyboardInterrupt()))
    try:
        if args.install:
            install_service(args.bin_dir)
        elif args.watch or args.packaged_service:
            if args.packaged_service:
                migrate_legacy_service()
            watch(args.bin_dir)
        elif args.rate is not None:
            change_rate(args.bin_dir, args.device, args.rate)
        else:
            reconcile(args.bin_dir)
        return 0
    except KeyboardInterrupt:
        return 0
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
