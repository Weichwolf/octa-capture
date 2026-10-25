# SPDX-License-Identifier: GPL-2.0-only
"""Discover OCTA-CAPTURE ALSA cards through Linux sysfs, without opening USB."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re


@dataclass(frozen=True)
class Device:
    identity: str
    card_id: str
    card_index: int
    serial: str
    product_id: str = '0120'

    @property
    def port(self):
        return f'hw:CARD={self.card_id},DEV=0,SUBDEV=1'

    @property
    def model(self):
        return 'OCTA-CAPTURE EXP' if self.product_id == '0121' else 'OCTA-CAPTURE'

    @property
    def name(self):
        return f'{self.model} ({self.serial or self.card_id})'

    def document(self, port):
        selectors = (f'hw:CARD={self.card_id}', f'hw:CARD={self.card_index}', f'hw:{self.card_index}')
        return {'id': self.identity, 'name': self.name, 'model': self.model, 'product_id': self.product_id,
                'selected': port.split(',')[0] in selectors}


def _text(path):
    # sysfs files can report size zero, so bound the read rather than stat().
    with path.open('r', encoding='ascii') as source:
        value = source.read(129).strip()
    if len(value) > 128:
        raise ValueError('Oversized device identity.')
    return value


def _device(card):
    card_id = _text(card / 'id')
    if not re.fullmatch(r'[A-Za-z0-9_]{1,15}', card_id):
        return None
    interface = (card / 'device').resolve(strict=True)
    for usb in (interface, *interface.parents):
        if not (usb / 'idVendor').is_file() or not (usb / 'idProduct').is_file():
            continue
        product_id = _text(usb / 'idProduct').lower()
        if _text(usb / 'idVendor').lower() != '0582' or product_id not in ('0120', '0121'):
            return None
        if not re.fullmatch(r'[0-9]+-[0-9]+(?:\.[0-9]+)*', usb.name):
            return None
        try:
            serial = _text(usb / 'serial')
        except OSError:
            serial = ''
        if serial and not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', serial):
            return None
        identity = f'serial:{serial}' if serial else f'usb:{usb.name}'
        return Device(identity, card_id, int(card.name[4:]), serial, product_id)
    return None


def discover(root=Path('/sys/class/sound')):
    """Return present OCTA-CAPTURE/base or EXP cards, skipping inaccessible entries."""
    try:
        cards = tuple(root.iterdir())
    except OSError:
        return ()
    result = []
    for card in cards:
        if not re.fullmatch(r'card[0-9]{1,3}', card.name) or int(card.name[4:]) > 255:
            continue
        try:
            device = _device(card)
        except (OSError, ValueError, UnicodeError, RuntimeError):
            # Hot-unplug can remove any sysfs link or attribute during discovery.
            continue
        if device is not None:
            result.append(device)
    return tuple(sorted(result, key=lambda device: device.card_index))
