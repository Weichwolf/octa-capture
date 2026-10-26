# SPDX-License-Identifier: GPL-2.0-only
"""Bounded subprocess interface to the native, serial-checked pair transaction."""

import json
from pathlib import Path
import re
import subprocess

from .aggregate import RATES

CHANGE_TIMEOUT = 75
RECOVERY_TIMEOUT = 45


class ClockError(RuntimeError):
    pass


def _unique_object(pairs):
    result = dict(pairs)
    if len(result) != len(pairs):
        raise ValueError('Duplicate native result field.')
    return result


def _decode(output, returncode, previous, requested):
    if len(output) > 4096:
        raise ClockError('Oversized native paired-clock response; hardware state is unverified.')
    try:
        value = json.loads(output, object_pairs_hook=_unique_object)
    except (ValueError, UnicodeError) as error:
        raise ClockError('Invalid native paired-clock response; hardware state is unverified.') from error
    if type(value) is not dict or type(value.get('ok')) is not bool:
        raise ClockError('Missing native paired-clock result; hardware state is unverified.')
    if value['ok']:
        expected = {'ok': True, 'experimental': True, 'previous_rate_hz': previous,
                    'rate_hz': requested, 'changed': previous != requested}
        if (returncode != 0 or value != expected or type(value.get('previous_rate_hz')) is not int or
                type(value.get('rate_hz')) is not int or type(value.get('changed')) is not bool or
                type(value.get('experimental')) is not bool):
            raise ClockError('Native paired-clock result disagrees with the request; hardware state is unverified.')
        return
    fields = {'ok', 'error', 'change_attempted', 'recovered'}
    attempted = value.get('change_attempted')
    recovered = value.get('recovered')
    if (returncode != 1 or set(value) != fields or type(value.get('error')) is not int or value['error'] >= 0 or
            type(attempted) is not bool or (attempted and type(recovered) is not bool) or
            (not attempted and recovered is not None)):
        raise ClockError('Inconsistent native paired-clock failure; hardware state is unverified.')
    state = 'previous rate verified' if recovered is True else 'no change attempted' if not attempted else 'recovery failed'
    raise ClockError(f'Native paired-clock change failed ({value["error"]}; {state}).')


def _stop(process):
    # SIGTERM requests native recovery. A second bounded wait precedes forced
    # termination; neither a forced exit nor unparseable output proves recovery.
    if process.poll() is None:
        try:
            process.terminate()
        except ProcessLookupError:
            pass
    try:
        return process.communicate(timeout=RECOVERY_TIMEOUT)
    except BaseException:
        if process.poll() is None:
            process.kill()
        process.communicate()
        raise


def change(binary, plan, rate):
    if type(rate) is not int or rate not in RATES:
        raise ValueError('The paired clock requires 44.1, 48 or 96 kHz.')
    devices = (plan.base, plan.expansion)
    if (not all(re.fullmatch(r'[A-Za-z0-9_-]{1,64}', device.serial) for device in devices) or
            devices[0].serial == devices[1].serial):
        raise ValueError('Select two distinct serial identities before changing a paired clock.')
    arguments = [str(Path(binary) / 'octa-pair-clock'), '--experimental', str(rate),
                 *(value for device in devices for value in (device.card_id, device.serial))]
    with subprocess.Popen(arguments, stdout=subprocess.PIPE, stderr=subprocess.PIPE) as process:
        try:
            output, _errors = process.communicate(timeout=CHANGE_TIMEOUT)
        except BaseException:
            _stop(process)
            raise
        _decode(output, process.returncode, plan.rate, rate)
