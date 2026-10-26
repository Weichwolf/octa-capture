#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Compare original Windows USB clock negotiation with our actual clock C source offline."""

import argparse
import json
from pathlib import Path
import runpy
import subprocess

STATUS = runpy.run_path(str(Path(__file__).with_name('verify-status-poll.py')))
HELPERS = STATUS['HELPERS']
CONTEXT, PROPS, STACK, STOP, URB = (HELPERS[name] for name in ('CONTEXT', 'PROPS', 'STACK', 'STOP', 'URB'))
MODEL = STATUS['MODEL']
read, write, require = (STATUS[name] for name in ('read', 'write', 'require'))
ROOT = Path(__file__).resolve().parent.parent


class WindowsClock(STATUS['Windows']):
    def __init__(self, path):
        super().__init__(path)
        self.stub(0x140012de0, self.usb)
        for name, callback in (('ExAllocatePool', self.allocated), ('ExFreePoolWithTag', self.freed)):
            address = STOP + 0x100 + len(self.targets) * 16
            self.targets.append(address)
            write(self.cpu, self.imports[name], address, 'Q')
            self.stub(address, callback)

    def allocated(self):
        require((self.argument(0), self.argument(1)) == (0x200, 0x88) and not self.pool_live,
                'Unexpected original URB allocation or overlapping ownership.')
        self.pool_live = True
        self.allocations += 1
        self.cpu.mem_write(URB, b'\xa5' * 0x100)
        return URB

    def freed(self):
        require((self.argument(0), self.argument(1)) == (URB, 0) and self.pool_live,
                'Unbalanced original URB free.')
        self.pool_live = False
        self.frees += 1
        return 0

    def initialized(self):
        if self.argument(0) == MODEL + 0x9d0:
            return super().initialized()
        require(STACK - 0x1000 < self.argument(0) < STACK and
                (self.argument(1), self.argument(2)) == (0, 0), 'Unexpected original clock event.')
        self.clock_event = self.argument(0)
        return 0

    def waited(self):
        if self.argument(0) == MODEL + 0x9d0:
            return super().waited()
        require(self.argument(0) == self.clock_event and
                tuple(self.argument(index) for index in (1, 2, 3)) == (0, 0, 0),
                'Unexpected original clock delay event.')
        require(read(self.cpu, self.argument(4), 'q') == -2000000, 'Clock delay is not 200 ms.')
        self.clock_waits += 1
        return 0

    def usb(self):
        require((self.argument(0), self.argument(1)) == (PROPS + 0x500, URB) and self.pool_live,
                'Unexpected original USB submission context.')
        require(read(self.cpu, URB) == 0x170088 and read(self.cpu, URB + 0x24) == 4 and
                read(self.cpu, URB + 0x30, 'Q') == 0, 'Unexpected original vendor-device URB.')
        flags, buffer = read(self.cpu, URB + 0x20), read(self.cpu, URB + 0x28, 'Q')
        require(flags in (0, 3) and STACK - 0x1000 < buffer < STACK, 'Unexpected clock buffer/flags.')
        direction = 0xc0 if flags == 3 else 0x40
        setup = bytes(self.cpu.mem_read(URB + 0x80, 6))
        value = 1 if direction == 0xc0 else 8
        require(setup == bytes([0, 3, value, 0, 0, 0]), 'Original request is not clock request 03.')
        payload = bytes(self.cpu.mem_read(buffer, 4)).hex() if direction == 0x40 else ''
        self.requests.append((direction, 3, value, 0, 4, payload))
        if direction == 0x40:
            write(self.cpu, URB + 0x24, self.out_length)
            return self.out_status
        require(self.replies, 'Original polling exceeded its fixed reply sequence.')
        status, length, rate, clock_flags = self.replies.pop(0)
        require(0 <= length <= 4, 'Reply exceeds the modeled four-byte USB allocation.')
        if not status and length:
            self.cpu.mem_write(buffer, (rate.to_bytes(3, 'little') + bytes([clock_flags]))[:length])
        write(self.cpu, URB + 0x24, length)
        return status

    def negotiate(self, initial, requested, busy=0, *, replies=None, out_status=0, out_length=4,
                  stopped=False, missing=False):
        self.reset([STATUS['packet'](phase=0, rate=initial), STATUS['packet'](rate=initial)], stopped=stopped)
        self.requests, self.clock_waits = [], 0
        self.pool_live, self.allocations, self.frees = False, 0, 0
        self.out_status, self.out_length = out_status, out_length
        self.replies = list(replies) if replies is not None else [(0, 4, requested, 1)] * busy + [(0, 4, requested, 0)]
        write(self.cpu, self.manager + 8, CONTEXT, 'Q')
        write(self.cpu, self.manager + 0x2c, initial)
        write(self.cpu, CONTEXT + 0xbc38, PROPS + 0x400, 'Q')
        write(self.cpu, PROPS + 0x410, PROPS + 0x500, 'Q')
        if missing:
            write(self.cpu, self.manager + 0x38, 0, 'Q')
        HELPERS['execute'](self.cpu, 0x140005b00, (self.manager, requested, 0, 0), windows=True)
        require(not self.pool_live and self.allocations == self.frees and not self.lock_depth,
                'Original clock path retained an allocation or lock.')
        return bool(self.cpu.reg_read(STATUS['registers'].UC_X86_REG_RAX) & 0xffffffff)


def normal_cases(reference, binary):
    output = subprocess.run([str(binary), '--reference'], check=True, capture_output=True, text=True, timeout=10)
    require(len(output.stdout) < 16384, 'Oversized Linux clock fixture output.')
    seen = set()
    for line in output.stdout.splitlines():
        initial, requested, busy, result, writes, sleeps, frame = line.split('\t')
        initial, requested, busy, result, writes, sleeps = map(int, (initial, requested, busy, result, writes, sleeps))
        key = initial, requested, busy
        require(key not in seen, 'Duplicate Linux clock reference case.')
        seen.add(key)
        success = reference.negotiate(initial, requested, busy)
        frames = [row[-1] for row in reference.requests if row[0] == 0x40]
        transfers = 0 if initial == requested else 1 + min(busy + 1, 40)
        require(len(reference.requests) == transfers and reference.transfers == 2,
                'Original clock request count or phase acquisition differs.')
        require(success == (result == 0) and len(frames) == writes and reference.clock_waits == sleeps,
                f'Windows/Linux negotiation differs: {key}, {result}, {reference.clock_waits}.')
        require(frames == ([] if frame == '-' else [frame]), f'Clock payload differs from actual Linux C: {key}.')
        require(read(reference.cpu, reference.manager + 0x2c) == (requested if success else initial),
                'Original clock cache committed a failed request.')
    expected = {(initial, requested, busy) for initial in STATUS['RATES'] for requested in STATUS['RATES']
                for busy in ((0,) if initial == requested else (0, 1, 39, 40))}
    require(seen == expected, 'Incomplete rate-pair/poll-boundary coverage.')
    return len(seen)


def boundary_cases(reference):
    initial, requested = 44100, 48000
    failures = [{'out_status': 0xc0000001}, {'replies': [(0xc0000001, 4, requested, 0)]},
                *({'replies': [(0, size, requested, 0)]} for size in range(4)),
                *({'replies': [(0, 4, requested, flags)]} for flags in (2, 3)),
                {'replies': [(0, 4, initial, 0)]}, {'stopped': True}, {'missing': True}]
    for case in failures:
        require(not reference.negotiate(initial, requested, **case), f'Original accepted failed negotiation: {case}.')
        require(read(reference.cpu, reference.manager + 0x2c) == initial, 'Failed negotiation changed the original cache.')
    # Record the reference's unchecked OUT completion length; Linux rejects it.
    for size in range(4):
        require(reference.negotiate(initial, requested, out_length=size), 'Original short OUT behavior changed.')
    return len(failures) + 4


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('windows_kernel', type=Path, help='hash-pinned Windows 1.5.3 x64 RDWM1110.SYS')
    parser.add_argument('--clock-test', type=Path, default=ROOT / 'build/strict/clock_test')
    args = parser.parse_args()
    reference = WindowsClock(args.windows_kernel)
    count = normal_cases(reference, args.clock_test)
    boundaries = boundary_cases(reference)
    print(json.dumps({'rate_cases': count, 'boundary_cases': boundaries,
                      'source_sha256': HELPERS['WINDOWS_SHA256'], 'hardware_validation': False,
                      'host_dependencies': 'memory-only USB completion, allocation/free, status queue, waits and locks'}))
    print('PASS original status acquisition and clock URB construction, 40-poll limit, 200-ms delays and cache commit; '
          'actual Linux C agrees on rate pairs/poll boundaries. Windows accepts short successful OUT completions; '
          'Linux keeps its exact-length guard. Outer IOCTL/worker dispatch and physical clock changes remain outside this check.')


if __name__ == '__main__':
    main()
