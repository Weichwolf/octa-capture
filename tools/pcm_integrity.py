# SPDX-License-Identifier: GPL-2.0-only
"""Quiet deterministic PCM sequences with uniquely verified sample alignment."""

from array import array
import hashlib
import sys

MAX_FRAMES = 60 * 96000
AMPLITUDE = 1024
ALIGNMENT_FRAMES = 32


class IntegrityError(ValueError):
    """Captured samples do not match one unique segment of the source."""


def make_sequence(frames):
    if not isinstance(frames, int) or isinstance(frames, bool) or not 1 <= frames <= MAX_FRAMES:
        raise ValueError('Invalid integrity sequence length.')
    words = array('H', hashlib.shake_256(b'OCTA-CAPTURE PCM integrity v1').digest(frames * 2))
    if sys.byteorder != 'little':
        words.byteswap()
    samples = array('h', ((value & (2 * AMPLITUDE - 1)) - AMPLITUDE for value in words))
    if sys.byteorder != 'little':
        samples.byteswap()
    return samples.tobytes()


def interleave_sequence(reference, channel):
    if (not isinstance(channel, int) or isinstance(channel, bool) or not 0 <= channel < 10 or
            not reference or len(reference) % 2 or len(reference) > MAX_FRAMES * 2):
        raise ValueError('Invalid integrity PCM geometry.')
    high = reference[1::2]
    if any(value not in (0, 1, 2, 3, 252, 253, 254, 255) for value in high):
        raise ValueError('Integrity sequence exceeds the quiet amplitude limit.')
    payload = bytearray(len(reference) // 2 * 30)
    offset = channel * 3
    payload[offset::30] = reference[0::2]
    payload[offset + 1::30] = high
    payload[offset + 2::30] = bytes(255 if value & 128 else 0 for value in high)
    return payload


def verify_sequence(reference, values):
    if (not reference or len(reference) % 2 or len(reference) > MAX_FRAMES * 2 or
            not ALIGNMENT_FRAMES <= len(values) <= len(reference) // 2):
        raise IntegrityError('Invalid integrity comparison length.')
    if any(not -AMPLITUDE <= value < AMPLITUDE for value in values):
        raise IntegrityError('Captured sample exceeds the integrity amplitude limit.')
    captured = array('h', values)
    if sys.byteorder != 'little':
        captured.byteswap()
    observed = captured.tobytes()
    prefix = observed[:ALIGNMENT_FRAMES * 2]
    start = reference.find(prefix)
    while start >= 0 and start % 2:
        start = reference.find(prefix, start + 1)
    if start < 0:
        raise IntegrityError('Captured prefix does not align with the reference sequence.')
    other = reference.find(prefix, start + 1)
    while other >= 0 and other % 2:
        other = reference.find(prefix, other + 1)
    if other >= 0:
        raise IntegrityError('Captured prefix has ambiguous reference alignment.')
    expected = reference[start:start + len(observed)]
    if len(expected) != len(observed):
        raise IntegrityError('Captured sequence extends beyond the reference.')
    if observed != expected:
        index = next(index for index in range(0, len(observed), 2)
                     if observed[index:index + 2] != expected[index:index + 2])
        value = int.from_bytes(expected[index:index + 2], 'little', signed=True)
        raise IntegrityError(f'Integrity sample/order mismatch at sample {index // 2}: '
                             f'observed {values[index // 2]}, expected {value}.')
    return start // 2
