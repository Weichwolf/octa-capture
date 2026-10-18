# SPDX-License-Identifier: GPL-2.0-only
"""Read actual ALSA stream parameters for an independently verified PCM owner."""

import re


def geometry_valid(period, buffer):
    return (64 <= period <= 32768 and 128 <= buffer <= 65536 and
            buffer >= period * 2 and buffer % period == 0)


def owned_parameters(root, item, rate, period, buffer):
    direction = 'c' if item['name'] == 'arecord' else 'p'
    directory = root / f'pcm0{direction}/sub0'

    def owned():
        owner = re.search(r'^owner_pid\s*:\s*(\d+)\s*$', (directory / 'status').read_text(), re.M)
        return owner is not None and int(owner[1]) == item['process'].pid

    if not owned():
        return None
    fields = dict(line.split(':', 1) for line in (directory / 'hw_params').read_text().splitlines() if ':' in line)
    if not fields or not owned():
        return None  # Closing or replacing the owner cannot verify this stream.
    try:
        actual = {'access': fields['access'].strip(), 'sample_format': fields['format'].strip(),
                  'channels': int(fields['channels']), 'rate_hz': int(fields['rate'].split()[0]),
                  'period_frames': int(fields['period_size']), 'buffer_frames': int(fields['buffer_size'])}
    except (KeyError, ValueError, IndexError) as error:
        raise RuntimeError(f'Invalid ALSA hardware parameters for {item["name"]}: {fields}') from error
    expected = {'access': 'RW_INTERLEAVED', 'sample_format': 'S24_3LE', 'channels': item['channels'],
                'rate_hz': rate, 'period_frames': period, 'buffer_frames': buffer}
    if actual != expected:
        raise RuntimeError(f'{item["name"]} negotiated unexpected ALSA parameters: {actual}; requested {expected}')
    return actual
