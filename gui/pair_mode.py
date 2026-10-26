# SPDX-License-Identifier: GPL-2.0-only
"""Explicit serial selection and verified clock availability for the paired GUI."""

import re
import subprocess
import threading
import time

from . import aggregate
from .backend import DeviceError


class PairMode:
    def __init__(self, base, expansion):
        if (not all(type(identity) is str and re.fullmatch(r'serial:[A-Za-z0-9_-]{1,64}', identity)
                    for identity in (base, expansion)) or base == expansion):
            raise ValueError('Select distinct base and EXP serial identities, e.g. serial:SERIAL.')
        self.base, self.expansion = base, expansion
        self.cancelled = threading.Event()
        self._lock = threading.Lock()
        self._plan = None
        self._error = 'The paired clock has not been checked.'
        self._updated = 0

    def role(self, identity):
        return 'base' if identity == self.base else 'expansion' if identity == self.expansion else None

    def member(self, device):
        role = self.role(device.identity)
        return (role is not None and device.serial == device.identity.removeprefix('serial:') and
                device.product_id == ('0120' if role == 'base' else '0121'))

    def visible(self, devices):
        members = tuple(device for device in devices if self.member(device))
        return tuple(device for device in members if sum(other.identity == device.identity for other in members) == 1)

    def reset(self):
        with self._lock:
            self._plan = None
            self._updated = 0
            self._error = 'The paired clock has not been checked.'

    def refresh(self, binary, *, force=False):
        with self._lock:
            if not force and time.monotonic() - self._updated < 1:
                return
        try:
            plan = aggregate.inspect(binary, self.base, self.expansion, require_master=True)
            error = None
        except ValueError as failure:
            plan, error = None, str(failure)
        except (OSError, subprocess.SubprocessError):
            plan, error = None, 'Paired clock status is unavailable.'
        with self._lock:
            self._plan, self._error, self._updated = plan, error, time.monotonic()

    def require_plan(self, binary):
        self.refresh(binary, force=True)
        with self._lock:
            if self._plan is None:
                raise DeviceError(self._error)
            return self._plan

    def _allowed(self, plan, status, selected):
        if plan is None:
            return ()
        try:
            clock = aggregate.decode_clock(status)
        except ValueError:
            return ()
        if clock.rate != plan.rate or clock.rate not in aggregate.RATES:
            return ()
        role = self.role(selected)
        if role == 'base' and clock.external and clock.active:
            return (clock.rate,)
        if role == 'expansion' and not clock.external and clock.setting == 0:
            return aggregate.RATES
        return ()

    def document(self, status, selected):
        with self._lock:
            plan, error = self._plan, self._error
        allowed = self._allowed(plan, status, selected)
        if not allowed and not error:
            error = 'The selected unit clock does not match the paired clock.'
        return {'experimental': True, 'base_id': self.base, 'expansion_id': self.expansion,
                'selected_role': self.role(selected), 'available': bool(allowed),
                'allowed_rates': list(allowed), 'sample_rate_hz': plan.rate if plan else None,
                'capture_channels': 24, 'playback_channels': 20, 'error': error}


def add_arguments(parser):
    parser.add_argument('--pair-base', help='experimental paired base identity, e.g. serial:BASE')
    parser.add_argument('--pair-expansion', help='experimental paired EXP identity, e.g. serial:EXP')
    parser.add_argument('--experimental-pair', action='store_true', help='explicitly enable joint base/EXP clock changes')


def from_arguments(parser, args):
    if not any((args.pair_base, args.pair_expansion, args.experimental_pair)):
        return None
    if not all((args.pair_base, args.pair_expansion, args.experimental_pair)):
        parser.error('--pair-base, --pair-expansion and --experimental-pair must be supplied together.')
    try:
        return PairMode(args.pair_base, args.pair_expansion)
    except ValueError as error:
        parser.error(str(error))
