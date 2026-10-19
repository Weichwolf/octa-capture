# SPDX-License-Identifier: GPL-2.0-only
"""Isolated original Windows MIDI encoding, FIFO writes and receive dispatch."""

from pathlib import Path
import runpy
import struct

from unicorn import UC_HOOK_CODE
from unicorn import x86_const as registers

HELPERS = runpy.run_path(str(Path(__file__).with_name('verify-pcm-peaks.py')))
IMPORTS = runpy.run_path(str(Path(__file__).with_name('verify-power-lifecycle.py')))['pe_imports']
CONTEXT, PROPS, DATA, REQUEST, URB, STACK, STOP = (
    HELPERS[name] for name in ('CONTEXT', 'PROPS', 'DATA', 'REQUEST', 'URB', 'STACK', 'STOP'))
BUFFERS, SLOT_CAPACITY, BYTE_CAPACITY = 0x78000000, 0x2800, 0x2800
require, write = HELPERS['require'], HELPERS['write']


def read(cpu, address, code='I'):
    return struct.unpack('<' + code, cpu.mem_read(address, struct.calcsize('<' + code)))[0]


def returned(cpu, value):
    stack = cpu.reg_read(registers.UC_X86_REG_RSP)
    cpu.reg_write(registers.UC_X86_REG_RAX, value)
    cpu.reg_write(registers.UC_X86_REG_RIP, read(cpu, stack, 'Q'))
    cpu.reg_write(registers.UC_X86_REG_RSP, stack + 8)


class WindowsMidi:
    def __init__(self, path):
        self.cpu = HELPERS['cpu_image'](path, HELPERS['WINDOWS_SHA256'], windows=True)
        self.cpu.mem_map(BUFFERS, 0x100000)
        self.packets = []
        self.rx = CONTEXT + 0x10000
        self.sender = CONTEXT + 0x20000
        require(read(self.cpu, 0x14002b248, 'Q') == 0x140029710 and
                bytes(self.cpu.mem_read(0x140029710, 2)) == b'\xff\xe0',
                'Unexpected original indirect-call dispatcher.')
        require(struct.unpack('<16I', self.cpu.mem_read(0x140030110, 64)) == tuple(range(16)),
                'Unexpected original receive cable map.')
        self.cpu.hook_add(UC_HOOK_CODE, self.out, begin=0x14001b1d0, end=0x14001b1d0)
        table = IMPORTS(self.cpu, path)
        for index, name in enumerate(('KeAcquireSpinLockRaiseToDpc', 'KeReleaseSpinLock')):
            address = STOP + 0x100 + index * 16
            write(self.cpu, table[name], address, 'Q')
            self.stub(address, lambda: 0)
        # Completed user-read IRPs/notifications are outside byte decoding.
        self.stub(0x14000c020, lambda: 0)

    def stub(self, address, callback):
        self.cpu.hook_add(UC_HOOK_CODE, lambda cpu, *_: returned(cpu, callback()), begin=address, end=address)

    def execute(self, address, arguments):
        HELPERS['execute'](self.cpu, address, arguments, windows=True)
        return self.cpu.reg_read(registers.UC_X86_REG_RAX)

    def out(self, cpu, _address, _size, _context):
        pointer = cpu.reg_read(registers.UC_X86_REG_RCX)
        self.packets.append(bytes(cpu.mem_read(pointer + 0x10, 4)))
        # The original FIFO writer continues; this is an observation hook.

    def tx_reset(self):
        self.packets.clear()
        for cable in range(16):
            context, fifo = CONTEXT + cable * 0x100, BUFFERS + cable * 0x10000
            self.cpu.mem_write(context, bytes(0x30))
            write(self.cpu, context, 0x14002bed0, 'Q')
            write(self.cpu, context + 0x14, cable << 4, 'B')
            write(self.cpu, context + 0x18, fifo, 'Q')
            write(self.cpu, context + 0x20, PROPS + 0x300, 'Q')
            for offset, value in ((0, fifo + 24 + SLOT_CAPACITY * 4), (8, fifo + 24), (16, fifo + 24)):
                write(self.cpu, fifo + offset, value, 'Q')

    def tx_byte(self, cable, byte):
        context = CONTEXT + cable * 0x100
        self.cpu.mem_write(DATA + 0xe000, bytes([byte]))
        write(self.cpu, DATA + 0xf000, 1)
        write(self.cpu, STACK + 0x28, DATA + 0xf000, 'Q')
        result = self.execute(0x14001ad50, (context, DATA + 0xf008, read(self.cpu, context + 8), DATA + 0xe000))
        require(result == DATA + 0xf008 and read(self.cpu, DATA + 0xf008, 'Q') == read(self.cpu, context + 8, 'Q'),
                'Original encoder returned an inconsistent running state.')
        return read(self.cpu, DATA + 0xf000)

    def transmit(self, events):
        self.tx_reset()
        for cable, byte in events:
            require(self.tx_byte(cable, byte) == 0, 'Original Windows MIDI FIFO stopped unexpectedly.')
        for cable in range(16):
            fifo = BUFFERS + cable * 0x10000
            expected = b''.join(packet for packet in self.packets if packet[0] >> 4 == cable)
            require(read(self.cpu, fifo + 8, 'Q') == fifo + 24 + len(expected) and
                    bytes(self.cpu.mem_read(fifo + 24, len(expected))) == expected,
                    'Original packet FIFO contents differ from observed encoding.')
        return b''.join(self.packets)

    def rx_reset(self, enabled=(True, True), private=True):
        self.cpu.mem_write(self.rx, bytes(0x2000))
        # Suppress URB resubmission after the actual completion parser runs.
        write(self.cpu, self.rx + 8, 1)
        for cable in range(3):
            fifo = DATA + cable * 0x4000
            self.cpu.mem_write(fifo, bytes(0x3000))
            for offset, value in ((0, fifo + 24 + BYTE_CAPACITY), (8, fifo + 24), (16, fifo + 24)):
                write(self.cpu, fifo + offset, value, 'Q')
            if cable < 2:
                write(self.cpu, fifo + 0x2818, 0x14002bee0, 'Q')
                write(self.cpu, fifo + 0x2820, fifo, 'Q')
                write(self.cpu, fifo + 0x2828, int(enabled[cable]))
                write(self.cpu, self.rx + 0xb8 + cable * 0x20, fifo, 'Q')
            else:
                write(self.cpu, self.rx + 0xf8, 0x14002bee0, 'Q')
                write(self.cpu, self.rx + 0x100, fifo if private else 0, 'Q')
        self.cpu.mem_write(REQUEST, bytes(0x100))
        self.cpu.mem_write(URB, bytes(0x100))
        write(self.cpu, REQUEST + 0x28, URB, 'Q')
        write(self.cpu, URB + 0x28, DATA + 0xc000, 'Q')

    def receive_chunks(self, packets, status=0):
        chunks = [packets[index:index + 512] for index in range(0, len(packets), 512)] or [b'']
        for chunk in chunks:
            if chunk:
                self.cpu.mem_write(DATA + 0xc000, chunk)
            write(self.cpu, URB + 4, status & 0xffffffff)
            write(self.cpu, URB + 0x24, len(chunk))
            result = self.execute(0x14001a8f0, (self.rx, 0, 0, REQUEST))
            require(result == 0xc0000016, 'Original receive completion returned an unexpected status.')

    def receive_all(self, packets, enabled=(True, True), private=True, status=0):
        self.rx_reset(enabled, private)
        self.receive_chunks(packets, status)
        output = []
        for cable in range(3):
            fifo = DATA + cable * 0x4000
            length = read(self.cpu, fifo + 8, 'Q') - (fifo + 24)
            require(0 <= length < BYTE_CAPACITY, 'Receive vector exceeded the modeled original byte FIFO.')
            output.append(bytes(self.cpu.mem_read(fifo + 24, length)))
        return tuple(output)

    def receive(self, packets):
        return self.receive_all(packets)[:2]

    def control_sender(self, frame):
        require(0 < len(frame) <= 0x2000, 'Control frame exceeds the isolated source buffer.')
        self.packets.clear()
        self.cpu.mem_write(self.sender, bytes(0xa200))
        fifo = self.sender + 0x40
        for offset, value in ((0, fifo + 24 + SLOT_CAPACITY * 4), (8, fifo + 24), (16, fifo + 24)):
            write(self.cpu, fifo + offset, value, 'Q')
        # Queue packets without invoking host USB submission/timer machinery.
        write(self.cpu, self.sender + 0xa118, 1)
        self.cpu.mem_write(DATA + 0xc000, frame)
        write(self.cpu, DATA + 0xf000, len(frame))
        write(self.cpu, DATA + 0xf008, 0, 'Q')
        write(self.cpu, STACK + 0x28, DATA + 0xf008, 'Q')
        result = self.execute(0x14001b5e0, (self.sender, 2, DATA + 0xc000, DATA + 0xf000))
        require(result == 1 and read(self.cpu, DATA + 0xf000) == 0, 'Original CTRL sender did not consume its frame.')
        expected = b''.join(self.packets)
        require(read(self.cpu, fifo + 8, 'Q') == fifo + 24 + len(expected) and
                bytes(self.cpu.mem_read(fifo + 24, len(expected))) == expected,
                'Original control sender FIFO differs from its emitted packets.')
        return expected

    def capacity(self):
        cases = 0
        for free in range(3, 7):
            self.tx_reset()
            fifo = BUFFERS
            write(self.cpu, fifo + 8, fifo + 24 + (SLOT_CAPACITY - free) * 4, 'Q')
            remaining = self.tx_byte(0, 0xf8)
            require(remaining == int(free <= 4) and len(self.packets) == int(free > 4),
                    'Original transmit FIFO reservation differs.')
            cases += 1
        self.tx_reset()
        fifo = BUFFERS
        write(self.cpu, fifo + 8, fifo + 24 + (SLOT_CAPACITY - 1) * 4, 'Q')
        write(self.cpu, fifo + 16, fifo + 24 + 8 * 4, 'Q')
        require(self.tx_byte(0, 0xf8) == 0 and read(self.cpu, fifo + 8, 'Q') == fifo + 24,
                'Original packet FIFO did not wrap at its end.')
        require(bytes(self.cpu.mem_read(fifo + 24 + (SLOT_CAPACITY - 1) * 4, 4)) == bytes.fromhex('0ff80000'),
                'Original packet FIFO wrote different bytes at its wrap boundary.')
        return cases + 1

    def receive_capacity(self):
        wire = bytes.fromhex('0990407f')
        for free in range(1, 5):
            self.rx_reset()
            fifo, start = DATA, DATA + 24
            pointer = start + BYTE_CAPACITY - free
            write(self.cpu, fifo + 8, pointer, 'Q')
            self.receive_chunks(wire)
            length = 3 if free > 1 else 0
            expected = start + (BYTE_CAPACITY - free + length) % BYTE_CAPACITY
            require(read(self.cpu, fifo + 8, 'Q') == expected and read(self.cpu, fifo + 16, 'Q') == start,
                    'Original receive FIFO free-space guard differs.')
            for index, byte in enumerate(wire[1:1 + length]):
                address = start + (BYTE_CAPACITY - free + index) % BYTE_CAPACITY
                require(read(self.cpu, address, 'B') == byte, 'Original receive FIFO boundary byte differs.')
            if not length:
                require(read(self.cpu, pointer, 'B') == 0, 'Full original receive FIFO accepted a byte.')
        self.rx_reset()
        pointer = DATA + 24 + BYTE_CAPACITY - 1
        write(self.cpu, DATA + 8, pointer, 'Q')
        write(self.cpu, DATA + 16, pointer, 'Q')
        self.receive_chunks(wire)
        require(read(self.cpu, DATA + 8, 'Q') == DATA + 26 and
                read(self.cpu, pointer, 'B') == 0x90 and bytes(self.cpu.mem_read(DATA + 24, 2)) == b'\x40\x7f',
                'Original byte FIFO did not wrap at its end.')
        return 5
