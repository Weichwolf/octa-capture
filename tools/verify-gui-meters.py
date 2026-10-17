#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Compare meter tables and execute original Windows display math offline."""

import argparse
import hashlib
import json
from pathlib import Path
import random
import runpy
import struct
import subprocess

from unicorn import UC_HOOK_CODE
from unicorn import x86_const as registers

MAC_SHA = '1ada3d6be71a6559ff84e7b21af1c964f0df2ce305c1f45fe0018cc148922092'
WINDOWS_SHA = '634c140bc4e49b0d65c70d5156be19573ad3d440ac5ea24e24d3ad59974da406'


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def read_pe(image, address, count):
    header = struct.unpack_from('<I', image, 0x3c)[0]
    sections = struct.unpack_from('<H', image, header + 6)[0]
    start = header + 24 + struct.unpack_from('<H', image, header + 20)[0]
    for index in range(sections):
        _, _, rva, size, offset = struct.unpack_from('<8sIIII', image, start + index * 40)
        position = address - 0x140000000 - rva
        if 0 <= position and position + count <= size:
            return image[offset + position:offset + position + count]
    raise RuntimeError(f'Unmapped reference address {address:x}')


def node_values(root, samples, levels):
    module = (root / 'gui/web/meters.js').as_uri()
    code = f'''import * as m from {json.dumps(module)};
let data='';for await(const chunk of process.stdin)data+=chunk;
const {{samples,levels}}=JSON.parse(data);
const traces = [0,-2].map(threshold=>{{const model=new m.MeterBallistics(threshold);
 return levels.map(value=>{{model.feed(value);model.step();return [model.level,model.peak,model.hold,model.clipHold];}});}});
console.log(JSON.stringify({{low:m.LEVEL_THRESHOLDS,high:m.HEADROOM_THRESHOLDS,
 indices:samples.map(value=>[m.meterIndex(value),m.headroomIndex(value)]),traces}}));'''
    result = subprocess.run(['node', '--input-type=module', '-e', code],
                            input=json.dumps({'samples': samples, 'levels': levels}), text=True,
                            capture_output=True, timeout=10, check=True)
    return json.loads(result.stdout)


def execute(cpu, core, address):
    core['execute'](cpu, address, (core['CONTEXT'], 0, 0, 0), windows=True)


def check_scale(cpu, core, samples, values):
    for sample, expected in zip(samples, values['indices'], strict=True):
        for address, result in zip((0x140014e20, 0x140014d60), expected, strict=True):
            bits = int.from_bytes(struct.pack('<d', sample), 'little')
            cpu.reg_write(registers.UC_X86_REG_XMM0, bits)
            execute(cpu, core, address)
            actual = cpu.reg_read(registers.UC_X86_REG_EAX)
            if actual & 0x80000000:
                actual -= 0x100000000
            require(actual == result, f'Meter scale differs at {sample}: {actual} vs {result}')
    print(f'PASS original Windows scale execution: {len(samples) * 2} results', flush=True)


def check_trace(cpu, core, levels, values):
    context, backend, backend_vtable, vtable, stub = (
        core['CONTEXT'], core['PROPS'], core['DATA'], core['REQUEST'], core['STOP'] + 0x100)
    write = core['write']
    write(cpu, context, vtable, 'Q')
    write(cpu, context + 0x128, backend, 'Q')
    write(cpu, backend, backend_vtable, 'Q')
    write(cpu, backend_vtable + 0x118, stub, 'Q')
    write(cpu, 0x1401eff58, stub + 1, 'Q')  # InvalidateRect: rendering is outside this CPU test.
    cpu.mem_write(stub, b'\xc3\xc3')
    sample = [-96.0]

    def supply_level(machine, address, _size, _data):
        if address == stub:
            machine.reg_write(registers.UC_X86_REG_XMM0,
                              int.from_bytes(struct.pack('<d', sample[0]), 'little'))

    hook = cpu.hook_add(UC_HOOK_CODE, supply_level)
    try:
        for threshold, expected_trace in zip((0, -2), values['traces'], strict=True):
            write(cpu, vtable + 0x368, 0x14001b070, 'Q')
            write(cpu, vtable + 0x370, 0x14000f5a0, 'Q')
            write(cpu, 0x1402a6588, 0.72, 'd')
            write(cpu, 0x1402a6590, 0.72, 'd')
            write(cpu, 0x1402a6598, 50)
            write(cpu, 0x1402a659c, 133)
            write(cpu, context + 0x140, -96.0, 'd')
            write(cpu, context + 0x148, -96.0, 'd')
            write(cpu, context + 0x150, 0)
            clip = context + 0x2000
            clip_vtable = backend_vtable + 0x2000
            write(cpu, clip, clip_vtable, 'Q')
            write(cpu, clip + 0x128, backend, 'Q')
            write(cpu, clip + 0x148, 0)
            write(cpu, clip + 0x14c, 0)
            write(cpu, clip_vtable + 0x368, 0x14001b090 if threshold == 0 else 0x14001b0b0, 'Q')
            write(cpu, clip_vtable + 0x370, 0x1400199b0, 'Q')
            for index, (value, expected) in enumerate(zip(levels, expected_trace, strict=True)):
                sample[0] = value
                hold = struct.unpack('<I', cpu.mem_read(context + 0x150, 4))[0]
                write(cpu, context + 0x150, max(0, hold - 1))
                execute(cpu, core, 0x140018110)
                level, peak, hold = struct.unpack('<ddI', cpu.mem_read(context + 0x140, 20))
                count = struct.unpack('<I', cpu.mem_read(clip + 0x14c, 4))[0]
                write(cpu, clip + 0x14c, max(0, count - 1))
                core['execute'](cpu, 0x1400181a0, (clip, 0, 0, 0), windows=True)
                count = struct.unpack('<I', cpu.mem_read(clip + 0x14c, 4))[0]
                actual = (level, peak, hold, count)
                require(all(abs(a - b) < 1e-10 for a, b in zip(actual, expected, strict=True)),
                        f'Ballistics differ at threshold {threshold}, tick {index}: {actual} vs {expected}')
    finally:
        cpu.hook_del(hook)
    print(f'PASS original Windows level/hold/clip execution: {len(levels) * 2} ticks', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mac_gui', type=Path)
    parser.add_argument('windows_gui', type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parent.parent
    mac = args.mac_gui.read_bytes()
    require(hashlib.sha256(mac).hexdigest() == MAC_SHA, 'Unexpected macOS GUI')
    reference = runpy.run_path(str(root / 'tools/verify-gui-parameters.py'))
    require(hashlib.sha256(args.windows_gui.read_bytes()).hexdigest() == WINDOWS_SHA, 'Unexpected Windows GUI')
    low = struct.unpack('<257d', reference['read_address'](mac, 0x10005beb0, 257 * 8))
    high = struct.unpack('<33d', reference['read_address'](mac, 0x10005c6c0, 33 * 8))
    samples = sorted(set([*low, *high, -100.0, 0.0, 1.0] +
                         [(a + b) / 2 for table in (low, high) for a, b in zip(table, table[1:])] +
                         [index / 10 for index in range(-960, 11)]))
    random_source = random.Random(0)
    levels = [-96, 0] + [-96] * 190 + [-24] * 60 + [-2, -6, -12, 0] * 15 + [
        random_source.choice((-96, -48, -24, -12, -6, -2, 0)) for _ in range(300)] + [-96] * 190
    values = node_values(root, samples, levels)
    windows = args.windows_gui.read_bytes()
    require(list(low) == values['low'] == list(struct.unpack('<257d', read_pe(windows, 0x14029b000, 257 * 8))), 'Low-scale table mismatch')
    require(list(high) == values['high'] == list(struct.unpack('<33d', read_pe(windows, 0x14029b830, 33 * 8))), 'Headroom table mismatch')
    print('PASS all 290 meter thresholds match both original panels', flush=True)
    core = runpy.run_path(str(root / 'tools/verify-pcm-peaks.py'))
    cpu = core['cpu_image'](args.windows_gui, WINDOWS_SHA, windows=True)
    check_scale(cpu, core, samples, values)
    check_trace(cpu, core, levels, values)


if __name__ == '__main__':
    main()
