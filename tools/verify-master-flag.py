#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Verify the unnamed per-mixer bit in isolated Windows/macOS reference execution.

This does not assign a feature name, expose a live setter, or access a device.
"""

import argparse
import json
from pathlib import Path
import runpy
import struct

from unicorn import x86_const as registers

MONITOR = runpy.run_path(str(Path(__file__).with_name('verify-input-monitor.py')))
MAC = runpy.run_path(str(Path(__file__).with_name('emulate-bulk-preset.py')))
HELPERS = MONITOR['HELPERS']
CONTEXT, DATA = (HELPERS[name] for name in ('CONTEXT', 'DATA'))
FLAG_OFFSET, MIXER_STRIDE, MODEL_FLAG = 0x442, 0x198, 0x329


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def frame(address, payload):
    body = bytes(address) + bytes(payload)
    return bytes.fromhex('f0411000004d12') + body + bytes([-sum(body) % 128, 247])


class Windows(MONITOR['Kernel']):
    def __init__(self, path):
        super().__init__(path)
        HELPERS['write'](self.cpu, MONITOR['PROPS'] + 0x38, 48000)

    def sent(self, cpu, _address, _size, _context):
        size = MONITOR['read'](cpu, cpu.reg_read(registers.UC_X86_REG_R9))
        require(size == 14, 'Unexpected original master-flag frame length')
        self.frames.append(bytes(cpu.mem_read(cpu.reg_read(registers.UC_X86_REG_R8), size)))
        MONITOR['returned'](cpu, 1)

    def flag(self, bus, enabled):
        self.frames.clear()
        offset = 0x3f0 + bus * 0x208
        HELPERS['write'](self.cpu, CONTEXT + offset, int(not enabled))
        original = bytes(self.cpu.mem_read(CONTEXT, 0xb00))
        request = bytearray(40)
        struct.pack_into('<III', request, 0x10, 0x29, bus, int(enabled))
        self.cpu.mem_write(DATA, bytes(request))
        HELPERS['execute'](self.cpu, 0x140006550, (CONTEXT, DATA + 0x1000, DATA, 0), windows=True)
        require(self.cpu.reg_read(registers.UC_X86_REG_RAX) == 1, 'Original command was not accepted')
        expected = bytearray(original)
        struct.pack_into('<I', expected, offset, int(enabled))
        require(bytes(self.cpu.mem_read(CONTEXT, len(expected))) == expected, 'Unexpected Windows state change')
        expected_frame = frame((0, 8, bus << 4, 14), [int(enabled)])
        require(self.frames == [expected_frame], 'Windows command 0x29 produces a different frame')
        return expected_frame


class Mac:
    def __init__(self, path):
        self.cpu = HELPERS['cpu_image'](path, MAC['REFERENCE_SHA256'])

    def load(self, bus, raw):
        self.cpu.mem_write(CONTEXT, bytes(0x1000))
        payload = bytearray(1146)
        payload[FLAG_OFFSET:FLAG_OFFSET + 4] = bytes([0x7e] * 4)
        payload[FLAG_OFFSET + bus] = raw
        packet = frame((1, 0, 0, 0), payload)
        self.cpu.mem_write(DATA, packet)
        HELPERS['execute'](self.cpu, 0x100009434, (CONTEXT, DATA, 0, 0))
        require(bytes(self.cpu.mem_read(CONTEXT + 0x815, 1)) == b'\1', 'Original bulk decoder rejected its packet')
        require(bytes(self.cpu.mem_read(CONTEXT + MODEL_FLAG + bus * MIXER_STRIDE, 1)) == bytes([raw & 1]),
                'macOS bulk flag is not bit zero at the expected per-mixer offset')
        return packet

    def export(self):
        HELPERS['execute'](self.cpu, 0x10002aa98, (CONTEXT, DATA, 0, 0))
        return bytes(self.cpu.mem_read(DATA + 11, 1146))

    def notification(self, bus, enabled, notification):
        original = self.load(bus, 0x7e | int(not enabled))
        prior = bytes(self.cpu.mem_read(CONTEXT, 0x816))
        self.cpu.mem_write(DATA + 0x1000, notification)
        HELPERS['execute'](self.cpu, 0x1000093a0, (CONTEXT, DATA + 0x1000, 0, 0))
        require(self.cpu.reg_read(registers.UC_X86_REG_RAX) == 0x80000, 'Wrong original master notification mask')
        expected = bytearray(prior)
        expected[MODEL_FLAG + bus * MIXER_STRIDE] = int(enabled)
        require(bytes(self.cpu.mem_read(CONTEXT, len(expected))) == expected, 'Notification changed unrelated macOS state')
        payload = bytearray(original[11:-2])
        payload[FLAG_OFFSET + bus] = 0x7e | int(enabled)
        require(self.export() == payload, 'macOS bulk export changed unrelated bytes or reserved flag bits')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mac_gui', type=Path)
    parser.add_argument('windows_kernel', type=Path)
    args = parser.parse_args()
    mac, windows = Mac(args.mac_gui), Windows(args.windows_kernel)
    bulk_cases = 0
    for bus in range(4):
        for raw in range(128):
            packet = mac.load(bus, raw)
            require(mac.export() == packet[11:-2], 'Original bulk roundtrip does not preserve reserved bits')
            bulk_cases += 1
        for enabled in (False, True):
            mac.notification(bus, enabled, windows.flag(bus, enabled))
    print('PASS original Windows sender, macOS notification decoder and reserved-bit-preserving bulk roundtrip.')
    print(json.dumps({'bulk_cases': bulk_cases, 'windows_mac_notification_cases': 8,
                      'address': '00 08 (bus<<4) 0E', 'bulk_offset': '0x442 + bus, bit 0',
                      'feature_semantics': 'unknown', 'host_dependencies': 'memory-only final MIDI send',
                      'hardware_validation': False}))


if __name__ == '__main__':
    main()
