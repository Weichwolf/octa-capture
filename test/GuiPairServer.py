# SPDX-License-Identifier: GPL-2.0-only
"""Private real HTTP/application fixture with modeled USB and PulseAudio I/O."""

import argparse
from contextlib import ExitStack
import copy
import json
from pathlib import Path
import signal
import sys
import threading
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from PulsePair import Peer, BASE, EXP
from gui import aggregate, application, pair_clock, pair_mode, pulse_audio, pulse_pair
from gui.backend import DeviceError
from gui.devices import Device
from gui.server import LocalServer

OTHER = Device('serial:OTHER', 'OTHER', 2, 'OTHER')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    args = parser.parse_args()
    root = args.directory.resolve()
    template = json.loads((root / 'model.json').read_text())['state']
    peer = Peer()
    baseline = peer.state()
    present = [BASE, EXP, OTHER]
    activity = {BASE.identity: False, EXP.identity: False}
    release = threading.Event()
    pause = False
    current_cancel = None
    native_calls = []

    class Bridge:
        def __init__(self, _executable, port):
            self.device = next(device for device in (BASE, EXP) if device.port == port)
            self.observed = False

        def request(self, operation, _arguments=None):
            if operation != 'state' or self.device not in present:
                raise DeviceError('The synthetic unit is unavailable.')
            self.observed = True
            state = copy.deepcopy(template)
            payload = bytearray.fromhex(state['payload_hex'])
            payload[2] = (44100, 48000, 96000, 192000).index(peer.rate)
            state['payload_hex'] = payload.hex()
            return {'state': state}

        def auto_sense_activity(self):
            if not self.observed:
                return None
            active = activity[self.device.identity]
            return {'active': active, 'channels': int(active), 'observed_age_ms': 0}

        def meters(self):
            return None

        def close(self):
            self.observed = False

    def inspect(*_arguments, **_keywords):
        base, expansion = aggregate.selected(present, BASE.identity, EXP.identity)
        return aggregate.Plan(base, expansion, peer.rate)

    def clock(binary, plan, rate, *, cancelled=None):
        nonlocal current_cancel
        current_cancel = cancelled
        native_calls.append({'previous': plan.rate, 'rate': rate})
        return peer.clock(binary, plan, rate, cancelled=cancelled)

    def event(name):
        nonlocal pause
        if name != 'clock' or not pause:
            return
        (root / 'started').write_text('waiting')
        deadline = time.monotonic() + 15
        while not release.wait(.02):
            if current_cancel is not None and current_cancel.is_set():
                pause = False
                (root / 'cancelled').write_text('cancellation observed')
                raise pair_clock.ClockCancelled('Synthetic native clock cancelled')
            if time.monotonic() >= deadline:
                pause = False
                raise RuntimeError('Synthetic native clock release timed out')
        pause = False

    peer.on_event = event
    pair = pair_mode.PairMode(BASE.identity, EXP.identity)
    app = application.Application(root / 'no-device-binaries', root / 'state', EXP.port, pair=pair)
    app._preferences.update({'show_port_warning': False})

    def status(_app, *, refresh=False):
        external = _app._selected_identity == BASE.identity
        raw = bytes((1, 0, 0)) + peer.rate.to_bytes(3, 'little') + bytes((int(external), 1))
        _app._status = {'sample_rate_hz': peer.rate, 'raw_status_hex': raw.hex(),
                        'clock_source': 'external' if external else 'internal', 'clock_active': True}
        return _app._status

    def save_stats():
        value = {'native_calls': native_calls, 'baseline': baseline, 'current': peer.state(),
                 'modules': [name for name, _arguments in peer.loaded.values()]}
        temporary = root / 'stats.tmp'
        temporary.write_text(json.dumps(value))
        temporary.replace(root / 'stats.json')

    def commands():
        nonlocal pause
        for line in sys.stdin:
            value = json.loads(line)
            command = value['action']
            if command == 'release':
                release.set()
            else:
                with app._operation:
                    if command == 'missing-base':
                        present[:] = [EXP, OTHER] if value['enabled'] else [BASE, EXP, OTHER]
                    elif command == 'auto-base':
                        activity[BASE.identity] = value['enabled']
                    elif command == 'failure':
                        peer.fail_at, peer.failed = value['stage'], False
                    elif command == 'pause':
                        release.clear()
                        (root / 'started').unlink(missing_ok=True)
                        pause = True
                    elif command == 'stats':
                        save_stats()
                    else:
                        raise ValueError('Unknown synthetic control command')
                    pair.reset()
                    app._updated = 0
            print(f'FIXTURE_ACK {value["id"]}', flush=True)

    with ExitStack() as stack:
        stack.enter_context(patch.object(application, 'Bridge', Bridge))
        stack.enter_context(patch.object(application, 'discover', lambda: tuple(present)))
        stack.enter_context(patch.object(application.Application, '_clock_status', status))
        stack.enter_context(patch.object(aggregate, 'inspect', inspect))
        stack.enter_context(patch.object(pulse_pair, 'discover', lambda: tuple(present)))
        stack.enter_context(patch.object(pulse_pair, 'server_info', lambda: copy.deepcopy(peer.info)))
        stack.enter_context(patch.object(pulse_pair, 'verify_card'))
        stack.enter_context(patch.object(pulse_pair.pair_clock, 'change', clock))
        stack.enter_context(patch.object(pulse_audio, 'pulse', peer.pulse))
        stack.enter_context(patch.object(pulse_audio, 'document', peer.document))
        stack.enter_context(patch.object(pulse_audio, 'modules', lambda: copy.deepcopy(peer.loaded)))
        stack.enter_context(patch.object(pulse_audio, 'load_module', peer.load))
        stack.enter_context(patch('pathlib.Path.home', return_value=root / 'home'))
        stack.enter_context(patch.object(pulse_audio.os, 'environ', {**pulse_audio.os.environ, 'XDG_RUNTIME_DIR': str(root)}))
        (root / 'no-device-binaries').mkdir()
        executable = root / 'no-device-binaries/octa-pair-clock'
        executable.touch(mode=0o700)
        app.bridge = Bridge(None, EXP.port)
        http = LocalServer(app, port=0)
        # Keep the fixture alive until request cleanup has completed; do not let
        # daemon-thread exit truncate a real exception during shutdown.
        http.daemon_threads = False
        for number in (signal.SIGTERM, signal.SIGINT):
            signal.signal(number, lambda *_arguments: threading.Thread(target=http.shutdown, daemon=True).start())
        threading.Thread(target=commands, daemon=True).start()
        print(f'PRIVATE_FIXTURE {http.origin}/#token={http.token}', flush=True)
        try:
            http.serve_forever(poll_interval=.05)
        finally:
            app.close()
            save_stats()
            http.server_close()


if __name__ == '__main__':
    main()
