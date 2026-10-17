#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Verify GUI mappings against both reference binaries, optionally executing Mac math."""

import argparse
import hashlib
import json
import math
from pathlib import Path
import runpy
import struct
import subprocess


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def segments(image):
    position = 32
    result = []
    for _ in range(struct.unpack_from('<I', image, 16)[0]):
        command, size = struct.unpack_from('<II', image, position)
        if command == 0x19:
            result.append(struct.unpack_from('<4Q', image, position + 24))
        position += size
    return result


def read_address(image, address, length):
    for start, _memory, offset, size in segments(image):
        if start <= address and address + length <= start + size:
            return image[offset + address - start:offset + address - start + length]
    raise RuntimeError(f'Unmapped reference address {address:x}')


def mac_strings(image, address, count):
    pointers = struct.unpack(f'<{count}Q', read_address(image, address, count * 8))
    return [read_address(image, pointer, 16).split(b'\0', 1)[0].decode('ascii').strip()
            for pointer in pointers]


def windows_strings(image, table, count):
    pe = struct.unpack_from('<I', image, 0x3c)[0]
    sections = struct.unpack_from('<H', image, pe + 6)[0]
    start = pe + 24 + struct.unpack_from('<H', image, pe + 20)[0]

    def string(address):
        rva = address - 0x140000000
        for index in range(sections):
            _name, _size, section, raw_size, offset = struct.unpack_from('<8sIIII', image, start + index * 40)
            if section <= rva < section + raw_size:
                position = offset + rva - section
                return image[position:position + 16].split(b'\0', 1)[0].decode('ascii').strip()
        raise RuntimeError('Unmapped Windows string pointer')
    return [string(pointer) for pointer in struct.unpack_from(f'<{count}Q', image, table)]


def gui_values(root):
    module = (root / 'gui/web/parameters.js').as_uri()
    code = f'''import * as p from {json.dumps(module)};
const values = [...new Set([...p.VOLUME, ...p.VOLUME.slice(1).map((v,i)=>Math.floor((v+p.VOLUME[i])/2))])];
const curves = [];
for (let ratio=0;ratio<9;ratio++) for (const threshold of [0,30,50])
 for (const gain of [0,50,74]) for (const input of [-48,-24,0]) {{
  const compressor = {{ratio_index:ratio,threshold_index:threshold,gain_index:gain}};
  curves.push([input,threshold-50,p.RATIO[ratio],gain-50,p.compressorOutput(input,compressor)]);
 }}
console.log(JSON.stringify({{volume:p.VOLUME,attack:p.ATTACK,release:p.RELEASE,ratio:p.RATIO_TEXT,minimum:p.GRAPH_MIN_DB,
 indices:values.map(v=>[v,p.volumeIndex(v)]),curves}}));'''
    output = subprocess.run(['node', '--input-type=module', '-e', code], check=True,
                            capture_output=True, text=True, timeout=10)
    return json.loads(output.stdout)


def execute_math(executable, values):
    from unicorn import Uc, UC_ARCH_X86, UC_MODE_64
    from unicorn import x86_const as registers
    reference = runpy.run_path(str(Path(__file__).with_name('emulate-bulk-preset.py')))
    cpu = Uc(UC_ARCH_X86, UC_MODE_64)
    cpu.ctl_set_cpu_model(registers.UC_CPU_X86_HASWELL)
    reference['load_image'](cpu, executable)
    context, stack, stop = 0x71000000, 0x72000ff8, 0x73000000
    for address in (context, stack & ~0xfff, stop):
        cpu.mem_map(address, 0x1000)
    cpu.reg_write(registers.UC_X86_REG_CR4, cpu.reg_read(registers.UC_X86_REG_CR4) | 0x200)
    cpu.mem_write(context + 0x20, struct.pack('<ii', 48, 48))

    def execute(address):
        cpu.mem_write(stack, struct.pack('<Q', stop))
        cpu.reg_write(registers.UC_X86_REG_RSP, stack)
        cpu.emu_start(address, stop, timeout=1_000_000, count=1000)
        require(cpu.reg_read(registers.UC_X86_REG_RIP) == stop, 'Reference function did not return')

    for value, expected in values['indices']:
        cpu.reg_write(registers.UC_X86_REG_RDI, value)
        execute(0x10001ecfa)
        require(cpu.reg_read(registers.UC_X86_REG_RAX) & 0xffffffff == expected,
                f'Reference fader index differs at {value}')
    for input_db, threshold, ratio, gain, expected in values['curves']:
        cpu.mem_write(context + 0x78, struct.pack('<ddd', threshold, ratio, gain))
        cpu.reg_write(registers.UC_X86_REG_RDI, context)
        cpu.reg_write(registers.UC_X86_REG_XMM0, int.from_bytes(struct.pack('<d', input_db), 'little'))
        execute(0x10001760c)
        position = cpu.reg_read(registers.UC_X86_REG_XMM1) & 0xffffffffffffffff
        actual = struct.unpack('<d', position.to_bytes(8, 'little'))[0] * values['minimum'] / 48
        require(math.isclose(actual, expected, rel_tol=0, abs_tol=1e-9),
                f'Reference compressor curve differs: {input_db, threshold, ratio, gain, actual, expected}')
    print(f'PASS original CPU math: {len(values["indices"])} fader index cases and '
          f'{len(values["curves"])} compressor graph points')


def main():
    root = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mac', type=Path)
    parser.add_argument('windows', type=Path)
    parser.add_argument('--cpu', action='store_true', help='requires local Unicorn bindings')
    args = parser.parse_args()
    mac, windows = args.mac.read_bytes(), args.windows.read_bytes()
    require(hashlib.sha256(mac).hexdigest() ==
            '1ada3d6be71a6559ff84e7b21af1c964f0df2ce305c1f45fe0018cc148922092', 'Unexpected Mac reference')
    require(hashlib.sha256(windows).hexdigest() ==
            '634c140bc4e49b0d65c70d5156be19573ad3d440ac5ea24e24d3ad59974da406', 'Unexpected Windows reference')
    values = gui_values(root)
    minimum = struct.unpack('<d', read_address(mac, 0x100041840, 8))[0]
    require(minimum == values['minimum'], 'Compressor graph scale differs from reference')
    coefficient_bytes = struct.pack('<1025I', *values['volume'])
    require(read_address(mac, 0x10005c7d0, 4100) == coefficient_bytes and
            windows[0x299f60:0x299f60 + 4100] == coefficient_bytes,
            'GUI fader coefficients differ from a reference')
    for name, mac_address, windows_offset, count in [
            ('attack', 0x10005dee0, 0x29b2f0, 26),
            ('release', 0x10005dfb0, 0x29b3c0, 46),
            ('ratio', 0x10005de90, 0x29b2a0, 9)]:
        left = mac_strings(mac, mac_address, count)
        right = windows_strings(windows, windows_offset, count)
        if name != 'ratio':
            left, right = list(map(float, left)), list(map(float, right))
        require(left == right == values[name], f'{name} mapping differs between GUI/references')
    print('PASS both original binaries: all 1025 fader coefficients and complete attack/release/ratio tables')
    if args.cpu:
        execute_math(mac, values)


if __name__ == '__main__':
    main()
