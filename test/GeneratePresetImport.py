#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Generate the synthetic, offline-only preset import reference input."""

from pathlib import Path
import struct
import sys


def generate():
    data = bytearray(b'OCTA-CAPTURE Control Panel Data\0Ver100\0')

    def flag(value):
        data.append(int(bool(value)))

    def word(value):
        data.extend(struct.pack('<I', value))

    for route in (8, 6, 4, 2, 0):
        word(route)
    word(3)
    for bank in range(5):
        word(127 if bank == 0 else 13 - bank)
        word(127 if bank == 0 else 50 - bank)
    for pair in range(4):
        flag(pair % 2)
        for side in range(2):
            channel = pair * 2 + side
            flag(channel % 2)
            for index in (50 - channel, 25 - channel, 45 - channel, 50 - channel,
                          8 - channel, 74 - channel):
                word(index)
            for enabled in (channel % 3 == 0, channel % 2, channel == 1, channel % 4 == 0):
                flag(enabled)
            word(100 - channel)
    for bus in range(4):
        flag(bus % 2)
        for source in range(2):
            for pair in range(5):
                flag((pair + bus) % 2)
                for side in range(2):
                    channel = pair * 2 + side
                    flag(channel % 2)
                    flag((channel + bus) % 3 == 0)
                    word(10000 * channel if source == 0 and bus == 0 else 0xdeadbeef)
                    word(0x1000 * (channel % 9))
                    word(0x7fffff if (bus, source, channel) == (3, 1, 9)
                         else 0x200000 + (bus * 100 + source * 10 + channel) * 1000)
        for source in range(2):
            flag((bus + source) % 2)
            word(0x123456 if (bus, source) == (0, 0) else 0xfedcba98)
            word(0x300000 + bus)
            word(0x7fffff - source - bus)
    if len(data) != 1639:
        raise RuntimeError('Unexpected generated preset size')
    return data


if __name__ == '__main__':
    Path(sys.argv[1]).write_bytes(generate())
