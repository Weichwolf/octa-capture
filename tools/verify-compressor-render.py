#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Execute original compressor drawing routines and compare our generated geometry."""

import argparse
import hashlib
import json
import math
from pathlib import Path
import runpy
import struct
import subprocess

from unicorn import Uc, UC_ARCH_X86, UC_MODE_64, UC_HOOK_CODE
from unicorn import x86_const as registers

CONTEXT, RESOURCE, STOP, STACK = 0x71000000, 0x72000000, 0x73000000, 0x74000ff8


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def node_values(root):
    graph = (root / 'gui/web/graphs.js').as_uri()
    parameters = (root / 'gui/web/parameters.js').as_uri()
    source = f'''import {{compressorShape,GRAPH_STYLE}} from {json.dumps(graph)};
import {{RATIO}} from {json.dumps(parameters)};
const cases=[];
for(const gate_index of [0,1,10,30,50])for(const threshold_index of [0,10,30,50])
 for(let ratio_index=0;ratio_index<9;ratio_index++)for(const gain_index of [0,30,50,74])
  for(const enabled of [false,true]){{const input={{gate_index,threshold_index,ratio_index,gain_index,enabled}};
   cases.push({{input,shape:compressorShape(input)}});}}
console.log(JSON.stringify({{cases,style:GRAPH_STYLE,ratio:RATIO}}));'''
    result = subprocess.run(['node', '--input-type=module', '-e', source], capture_output=True,
                            check=True, text=True, timeout=10)
    return json.loads(result.stdout)


class Reference:
    def __init__(self, image):
        self.helpers = runpy.run_path(str(Path(__file__).with_name('emulate-bulk-preset.py')))
        require(hashlib.sha256(image).hexdigest() == self.helpers['REFERENCE_SHA256'], 'Unexpected macOS GUI hash.')
        self.cpu = Uc(UC_ARCH_X86, UC_MODE_64)
        self.cpu.ctl_set_cpu_model(registers.UC_CPU_X86_HASWELL)
        self.helpers['load_image'](self.cpu, image)
        for address in (CONTEXT, RESOURCE, STOP, STACK & ~0xfff):
            self.cpu.mem_map(address, 0x1000)
        self.cpu.reg_write(registers.UC_X86_REG_CR4, self.cpu.reg_read(registers.UC_X86_REG_CR4) | 0x200)
        self.pointer(0x100048098, RESOURCE + 0x200)
        self.pointer(CONTEXT + 0x90, RESOURCE)
        self.cpu.mem_write(CONTEXT + 0x20, struct.pack('<ii', 50, 50))
        self.shape = {}
        self.brushes = {}
        for address in (0x10000c8ea, 0x1000176b0, 0x1000177f0):
            self.cpu.hook_add(UC_HOOK_CODE, self.capture, begin=address, end=address)
        self.call(0x10001c186, RESOURCE)

    def pointer(self, address, value):
        self.helpers['write_pointer'](self.cpu, address, value)

    def call(self, address, argument):
        self.pointer(STACK, STOP)
        self.cpu.reg_write(registers.UC_X86_REG_RSP, STACK)
        self.cpu.reg_write(registers.UC_X86_REG_RDI, argument)
        self.cpu.emu_start(address, STOP, timeout=1_000_000, count=5000)
        require(self.cpu.reg_read(registers.UC_X86_REG_RIP) == STOP, 'Original drawing routine did not return.')

    def capture(self, engine, address, _size, _context):
        if address != 0x10000c8ea:  # Bitmap loader is an in-memory success stub.
            name = 'polygon' if address == 0x1000176b0 else 'line'
            count = engine.reg_read(registers.UC_X86_REG_RCX)
            require(count in (3, 4, 5), 'Unexpected original drawing point count.')
            pointer = engine.reg_read(registers.UC_X86_REG_RDX)
            self.shape[name] = [list(struct.unpack('<dd', engine.mem_read(pointer + index * 16, 16)))
                                for index in range(count)]
            self.brushes[name] = engine.reg_read(registers.UC_X86_REG_RSI)
        stack = engine.reg_read(registers.UC_X86_REG_RSP)
        returned = self.helpers['pointer'](engine, stack)
        engine.reg_write(registers.UC_X86_REG_RSP, stack + 8)
        engine.reg_write(registers.UC_X86_REG_RAX, 1)
        engine.reg_write(registers.UC_X86_REG_RIP, returned)

    def color(self, offset):
        red, green, blue, _alpha = self.cpu.mem_read(RESOURCE + offset, 4)
        return f'#{red:02x}{green:02x}{blue:02x}'

    def run(self, value, ratios):
        self.shape.clear()
        self.brushes.clear()
        gate = value['gate_index'] - 70 if value['gate_index'] else 0
        self.cpu.mem_write(CONTEXT + 0x70, struct.pack('<dddd', gate, value['threshold_index'] - 50,
                                                     ratios[value['ratio_index']], value['gain_index'] - 50))
        self.cpu.mem_write(CONTEXT + 0x6c, bytes([value['enabled']]))
        self.call(0x100016ee4, CONTEXT)
        require(self.brushes['polygon'] == RESOURCE + (0x80 if value['enabled'] else 0x84), 'Wrong original fill brush.')
        require(self.brushes['line'] == RESOURCE + (0x88 if value['enabled'] else 0x98), 'Wrong original line pen.')
        return {name: points[:] for name, points in self.shape.items()}


def asset_color(directory, name):
    image = directory / f'{name}.png'
    result = subprocess.run(['convert', str(image), '-format', '%[hex:p{0,0}]', 'info:'],
                            capture_output=True, text=True, check=True, timeout=5)
    return '#' + result.stdout[:6].lower()


def styles(reference, resources, windows):
    helpers = runpy.run_path(str(Path(__file__).with_name('verify-gui-meters.py')))
    require(hashlib.sha256(windows).hexdigest() == helpers['WINDOWS_SHA'], 'Unexpected Windows GUI hash.')
    result = {}
    for name, prefix, fill, line, fill_address, line_address in (
            ('enabled', 'ON', 0x80, 0x88, 0x14001253b, 0x14001262a),
            ('bypassed', 'OFF', 0x84, 0x98, 0x1400125af, 0x140012686)):
        entry = {'background': asset_color(resources, 'COMP_' + prefix),
                 'fill': reference.color(fill), 'line': reference.color(line),
                 'grid': asset_color(resources, 'COMP_' + prefix + 'GRID')}
        for field, address in (('fill', fill_address), ('line', line_address)):
            color = struct.unpack('<I', helpers['read_pe'](windows, address, 4))[0] & 0xffffff
            require(entry[field] == f'#{color:06x}', 'Windows/macOS compressor colors differ.')
        result[name] = entry
    result['lineWidth'] = struct.unpack('<d', reference.cpu.mem_read(RESOURCE + 0x90, 8))[0]
    width = struct.unpack('<f', helpers['read_pe'](windows, 0x140209700, 4))[0]
    require(width == result['lineWidth'], 'Windows/macOS compressor pen widths differ.')
    return result


def same_shape(left, right):
    return (left.keys() == right.keys() and all(len(left[name]) == len(right[name]) and
            all(math.isclose(a, b, rel_tol=0, abs_tol=1e-9) for p, q in zip(left[name], right[name], strict=True)
                for a, b in zip(p, q, strict=True)) for name in left))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mac', type=Path)
    parser.add_argument('windows', type=Path)
    parser.add_argument('--resources', type=Path, required=True)
    parser.add_argument('--write-golden', type=Path)
    arguments = parser.parse_args()
    reference = Reference(arguments.mac.read_bytes())
    values = node_values(Path(__file__).resolve().parent.parent)
    style = styles(reference, arguments.resources, arguments.windows.read_bytes())
    require(style == values['style'], 'GUI drawing style differs from original references.')
    golden = []
    for case in values['cases']:
        shape = reference.run(case['input'], values['ratio'])
        require(same_shape(shape, case['shape']), f'Original compressor polygon differs: {case["input"]}')
        value = case['input']
        if value['gate_index'] in (0, 30, 50) and value['threshold_index'] in (0, 50) and \
                value['ratio_index'] in (0, 8) and value['gain_index'] in (0, 74):
            golden.append({'input': value, 'shape': shape})
    if arguments.write_golden:
        arguments.write_golden.write_text(json.dumps({'style': style, 'cases': golden}, separators=(',', ':')) + '\n')
    print(f'PASS {len(values["cases"])} original filled-curve/line cases, both palettes and pen width; no hardware access.')


if __name__ == '__main__':
    main()
