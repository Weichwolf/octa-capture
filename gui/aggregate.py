# SPDX-License-Identifier: GPL-2.0-only
"""Experimental base/EXP aggregate preparation and private ALSA configuration."""

import argparse
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import subprocess
import sys

from .devices import Device, discover

RATES = (44100, 48000, 96000)


@dataclass(frozen=True)
class Clock:
    rate: int
    external: bool
    active: bool


@dataclass(frozen=True)
class Plan:
    base: Device
    expansion: Device
    rate: int

    def document(self):
        return {'experimental': True, 'sample_rate_hz': self.rate, 'capture_channels': 24,
                'playback_channels': 20, 'base': self.base.document(self.base.port),
                'expansion': self.expansion.document(self.expansion.port)}


def selected(devices, base_id, expansion_id):
    result = []
    for identity, product in ((base_id, '0120'), (expansion_id, '0121')):
        matches = [device for device in devices if device.identity == identity and device.product_id == product]
        if len(matches) != 1:
            raise ValueError('Select one present base device and one present EXP device by identity.')
        result.append(matches[0])
    if result[0].card_id == result[1].card_id or result[0].identity == result[1].identity:
        raise ValueError('The aggregate requires two distinct devices.')
    return tuple(result)


def read_clock(binary, device):
    output = subprocess.run([str(Path(binary) / 'octa-status'), f'hw:CARD={device.card_id}'],
                            capture_output=True, check=True, timeout=5).stdout
    if len(output) > 4096:
        raise ValueError('Oversized device clock response.')
    status = json.loads(output)
    raw = status.get('raw_status_hex') if type(status) is dict else None
    if type(raw) is not str or len(raw) != 16:
        raise ValueError('Missing device clock packet.')
    packet = bytes.fromhex(raw)
    if len(packet) != 8 or packet[0] != 1:
        raise ValueError('Invalid device clock packet.')
    rate = int.from_bytes(packet[3:6], 'little')
    if rate == 0:
        raise ValueError('Invalid device clock rate.')
    source, active = ('external' if packet[6] & 1 else 'internal'), packet[7] != 0
    if type(status.get('sample_rate_hz')) is not int or status['sample_rate_hz'] != rate or \
            status.get('clock_source') != source or type(status.get('clock_active')) is not bool or \
            status['clock_active'] != active:
        raise ValueError('Inconsistent device clock response.')
    return Clock(rate, source == 'external', active)


def inspect(binary, base_id, expansion_id):
    base, expansion = selected(discover(), base_id, expansion_id)
    before = tuple(read_clock(binary, device) for device in (base, expansion))
    after = tuple(read_clock(binary, device) for device in (base, expansion))
    if before != after or selected(discover(), base_id, expansion_id) != (base, expansion):
        raise ValueError('Device identity or clock status changed during aggregate preparation.')
    first, second = after
    if first.rate != second.rate or first.rate not in RATES:
        raise ValueError('Both devices must already use the same 44.1, 48 or 96 kHz rate.')
    if not first.external or not first.active or (second.external and not second.active):
        raise ValueError('Connect EXP COAXIAL OUT to base COAXIAL IN and check DIGITAL AUTO/clock status.')
    # Status display activation does not establish sample-clock/phase alignment.
    return Plan(base, expansion, first.rate)


def configuration(base: Device, expansion: Device, rate: int):
    """Keep base channels first and use EXP for ALSA multi's master pointer."""
    if type(rate) is not int or rate not in RATES:
        raise ValueError('The two-unit aggregate requires 44.1, 48 or 96 kHz.')
    if base.product_id != '0120' or expansion.product_id != '0121':
        raise ValueError('Select one base device and one EXP device.')
    if base.identity == expansion.identity or base.card_id == expansion.card_id:
        raise ValueError('The aggregate requires two distinct devices.')
    for device in (base, expansion):
        if not re.fullmatch(r'[A-Za-z0-9_]{1,15}', device.card_id):
            raise ValueError('Invalid ALSA card identifier.')
    lines = ['# SPDX-License-Identifier: GPL-2.0-only\n',
             '# Experimental base/EXP aggregate: external sample-clock synchronization is required.\n']
    for direction, channels in (('playback', 10), ('capture', 12)):
        for unit, device in (('base', base), ('exp', expansion)):
            lines.append(f'pcm.octa_expand_{unit}_{direction} {{\n type hw\n card "{device.card_id}"\n'
                         f' device 0\n format S24_3LE\n channels {channels}\n rate {rate}\n}}\n')
        raw = f'octa_expand_{direction}_raw'
        lines.append(f'pcm.{raw} {{\n type multi\n master 1\n slaves {{\n'
                     f'  base {{ pcm "octa_expand_base_{direction}" channels {channels} }}\n'
                     f'  exp {{ pcm "octa_expand_exp_{direction}" channels {channels} }}\n }}\n bindings {{\n')
        for index in range(channels * 2):
            unit = 'base' if index < channels else 'exp'
            lines.append(f'  {index} {{ slave {unit} channel {index % channels} }}\n')
        lines.append(' }\n}\n')
        lines.append(f'pcm.octa_expand_{direction} {{\n type route\n'
                     f' slave {{ pcm "{raw}" format S24_3LE channels {channels * 2} }}\n ttable {{\n')
        lines.extend(f'  {index}.{index} 1\n' for index in range(channels * 2))
        lines.append(' }\n}\n')
    lines.append('pcm.octa_expand {\n type asym\n playback.pcm "octa_expand_playback"\n'
                 ' capture.pcm "octa_expand_capture"\n'
                 ' hint { show on description "OCTA-CAPTURE base + EXP (experimental, 24 in / 20 out)" }\n}\n')
    return ''.join(lines).encode('ascii')


def write_new(path, data):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(descriptor, 'wb') as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
    except BaseException:
        Path(path).unlink(missing_ok=True)
        raise


def main(default_bin_dir=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', required=True, help='discovered base device identity, e.g. serial:...')
    parser.add_argument('--expansion', required=True, help='discovered EXP device identity')
    parser.add_argument('--bin-dir', type=Path,
                        default=default_bin_dir or Path(__file__).resolve().parent.parent / 'build/release')
    parser.add_argument('--write-config', type=Path, help='new private ALSA config file; no automatic desktop installation')
    args = parser.parse_args()
    try:
        if os.geteuid() == 0:
            raise ValueError('Run aggregate preparation as the regular desktop user.')
        plan = inspect(args.bin_dir, args.base, args.expansion)
        if args.write_config:
            write_new(args.write_config, configuration(plan.base, plan.expansion, plan.rate))
        print(json.dumps(plan.document()))
        return 0
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(f'Aggregate preparation failed: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
