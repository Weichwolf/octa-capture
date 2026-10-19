#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Compare fixed mouse traces against original Windows knob handlers offline."""

import argparse
import json
from pathlib import Path
import runpy
import struct
import subprocess

from unicorn import UC_HOOK_CODE
from unicorn import x86_const as registers

HELPERS = runpy.run_path(str(Path(__file__).with_name('verify-pcm-peaks.py')))
GUI_SHA = '634c140bc4e49b0d65c70d5156be19573ad3d440ac5ea24e24d3ad59974da406'
CONTEXT, PROPS, STOP = (HELPERS[name] for name in ('CONTEXT', 'PROPS', 'STOP'))
VTABLES = {'sensitivity': 0x140200e80, 'compressor': 0x140203240,
           'pan': 0x140205070, 'volume': 0x140205a10}


def read(cpu, address, code='i'):
    return struct.unpack('<' + code, cpu.mem_read(address, struct.calcsize('<' + code)))[0]


class Reference:
    def __init__(self, executable):
        self.cpu = HELPERS['cpu_image'](executable, GUI_SHA, windows=True)
        self.stub(PROPS + 0x800, lambda: 0)
        self.stub(PROPS + 0x810, lambda: 64)  # Display position interval, not pixel size.
        self.stub(PROPS + 0x820, lambda: 0x55)  # GetCapture.
        self.stub(0x140035400, lambda: CONTEXT)  # CWnd::FromHandle.
        self.stub(0x140021210, lambda: 0)  # Pan backend notification.
        self.stub(0x140034ed4, lambda: 0)  # Default window key handler.
        self.write(0x1401efde0, PROPS + 0x820, 'Q')
        # The original resource constructor selects 65 bitmap positions.
        if read(self.cpu, 0x140012f95, 'I') != 65:
            raise RuntimeError('Original knob resource frame count differs.')

    def write(self, address, value, code='i'):
        self.cpu.mem_write(address, struct.pack('<' + code, value))

    def stub(self, address, callback):
        def hook(cpu, _address, _size, _context):
            stack = cpu.reg_read(registers.UC_X86_REG_RSP)
            cpu.reg_write(registers.UC_X86_REG_RAX, callback())
            cpu.reg_write(registers.UC_X86_REG_RIP, read(cpu, stack, 'Q'))
            cpu.reg_write(registers.UC_X86_REG_RSP, stack + 8)
        self.cpu.hook_add(UC_HOOK_CODE, hook, begin=address, end=address)

    def prepare(self, kind, maximum, value):
        self.cpu.mem_write(CONTEXT, bytes(0x40000))
        self.cpu.mem_write(PROPS, bytes(0x800))
        self.cpu.mem_write(PROPS, bytes(self.cpu.mem_read(VTABLES[kind], 0x410)))
        for offset in (0x318, 0x320, 0x398):
            self.write(PROPS + offset, PROPS + 0x800, 'Q')
        for offset in {'sensitivity': (0x3d0,), 'compressor': (0x3d8,),
                       'pan': (), 'volume': (0x3b8,)}[kind]:
            self.write(PROPS + offset, PROPS + 0x800, 'Q')
        self.write(CONTEXT, PROPS, 'Q')
        self.write(CONTEXT + 0xf8, PROPS + 0x600, 'Q')
        self.write(PROPS + 0x600, PROPS + 0x700, 'Q')
        self.write(PROPS + 0x728, PROPS + 0x810, 'Q')
        self.write(CONTEXT + 0x198, CONTEXT + 0x2000, 'Q')
        self.write(CONTEXT + 0x108, 1)
        self.cpu.mem_write(CONTEXT + 0x10c, struct.pack('<6i', 0, 0, value, value, 0, maximum))
        self.write(CONTEXT + 0x124, maximum)
        self.kind = kind

    def step(self, point, flags):
        packed = (point[0] & 0xffffffff) | ((point[1] & 0xffffffff) << 32)
        HELPERS['execute'](self.cpu, 0x140003300, (CONTEXT, flags | 1, packed, 0), windows=True)
        x, y, anchor, value = struct.unpack('<4i', self.cpu.mem_read(CONTEXT + 0x10c, 16))
        wire = read(self.cpu, CONTEXT + (0x1b0 if self.kind == 'pan' else 0x1ac))
        if self.kind == 'compressor':
            wire = read(self.cpu, CONTEXT + 0x1a8)
        return {'x': x, 'y': y, 'anchor': anchor, 'value': value, 'wire': wire}

    def key(self, key):
        HELPERS['execute'](self.cpu, 0x140003150, (CONTEXT, key, 1, 0), windows=True)
        return read(self.cpu, CONTEXT + 0x118)


def cases():
    points = [(0, 0), (1, 0), (2, 0), (3, -1), (3, -2), (3, -3), (5, -5),
              (7, 8), (-5, -8), (-20, 15), (20, -15), (1000, -1000),
              (999, -999), (-1000, 1000), (-999, 999), (0, 0)]
    for kind, maxima in (('sensitivity', (100,)), ('compressor', (8, 25, 45, 50, 74)),
                          ('pan', (200,)), ('volume', (1024,))):
        for maximum in maxima:
            for initial in (0, maximum // 2, maximum):
                for flags in (0, 4, 8, 12):
                    events = [{'point': point, 'flags': flags} for point in points]
                    events += [{'point': point, 'flags': mask} for point, mask in
                               zip(points[:8], (0, 4, 8, 12, 4, 0, 8, 0), strict=True)]
                    yield {'kind': kind, 'maximum': maximum, 'initial': initial, 'events': events}


def web_results(items):
    module = (Path(__file__).resolve().parent.parent / 'gui/web/knobs.js').as_uri()
    parameters = (Path(__file__).resolve().parent.parent / 'gui/web/parameters.js').as_uri()
    source = f'''import {{knobDrag}} from {json.dumps(module)};
import {{VOLUME,panToWire}} from {json.dumps(parameters)};
let input='';for await(const chunk of process.stdin)input+=chunk;
console.log(JSON.stringify(JSON.parse(input).map(test=>{{
 let state={{x:0,y:0,anchor:test.initial,value:test.initial}};
 return test.events.map(event=>{{state=knobDrag(test.kind,test.maximum,state,
  {{x:event.point[0],y:event.point[1]}},Boolean(event.flags&4),Boolean(event.flags&8));
  const wire=state.wireValue ?? (test.kind==='volume'?VOLUME[state.value]:
   test.kind==='pan'?panToWire(state.value):state.value);
  const {{x,y,anchor,value}}=state;return {{x,y,anchor,value,wire}};
 }});
}})));'''
    result = subprocess.run(['node', '--input-type=module', '-e', source], input=json.dumps(items),
                            capture_output=True, text=True, timeout=10, check=True)
    return json.loads(result.stdout)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('windows_gui', type=Path)
    parser.add_argument('--save-vectors', type=Path, help='save compared numeric regression traces')
    args = parser.parse_args()
    reference = Reference(args.windows_gui)
    items = list(cases())
    expected = web_results(items)
    count = 0
    for test, web in zip(items, expected, strict=True):
        reference.prepare(test['kind'], test['maximum'], test['initial'])
        original = [reference.step(event['point'], event['flags']) for event in test['events']]
        if web != original:
            mismatch = next(i for i, (a, b) in enumerate(zip(web, original, strict=True)) if a != b)
            raise RuntimeError(f'Original {test["kind"]}, max={test["maximum"]}, start={test["initial"]}, '
                               f'event {test["events"][mismatch]}: web {web[mismatch]}, original {original[mismatch]}')
        test['expected'] = original
        count += len(original)
    keys = {'ArrowLeft': 0x25, 'ArrowUp': 0x26, 'ArrowRight': 0x27, 'ArrowDown': 0x28,
            'Home': 0x24, 'End': 0x23}
    key_count = 0
    for test in items[::4]:
        test['keys'] = {}
        for name, code in keys.items():
            reference.prepare(test['kind'], test['maximum'], test['initial'])
            original = reference.key(code)
            delta = 1 if name in ('ArrowUp', 'ArrowRight') else -1 if name in ('ArrowLeft', 'ArrowDown') else 0
            expected = min(test['maximum'], max(0, test['initial'] + delta))
            if original != expected:
                raise RuntimeError(f'Original knob key differs: {test["kind"]}, {name}, {original}, {expected}')
            test['keys'][name] = original
            key_count += 1
    if args.save_vectors:
        for test in items:
            test['events'] = [[*event['point'], event['flags']] for event in test['events']]
            test['expected'] = [[value[field] for field in ('x', 'y', 'anchor', 'value', 'wire')]
                                for value in test['expected']]
        args.save_vectors.write_text('[\n' + ',\n'.join(json.dumps(test, separators=(',', ':')) for test in items) + '\n]\n')
    print(f'PASS {count} original Windows knob drag events: axes, modifiers, truncation, rebasing, bounds and wire values.')
    print(f'PASS {key_count} original arrow/Home/End key cases.')
    print('Window capture/painting and backend notifications are memory stubs. No hardware access or fuzzing.')


if __name__ == '__main__':
    main()
