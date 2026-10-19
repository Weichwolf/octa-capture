#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Compare fixed mouse traces against original Windows knob/fader handlers offline."""

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
VTABLES = {'fader': 0x1402004c0, 'sensitivity': 0x140200e80, 'compressor': 0x140203240,
           'pan': 0x140205070, 'volume': 0x140205a10}


def read(cpu, address, code='i'):
    return struct.unpack('<' + code, cpu.mem_read(address, struct.calcsize('<' + code)))[0]


class Reference:
    def __init__(self, executable):
        self.cpu = HELPERS['cpu_image'](executable, GUI_SHA, windows=True)
        self.stub(PROPS + 0x800, lambda: 0)
        self.stub(PROPS + 0x810, lambda: 56 if self.kind == 'fader' else 64)
        self.stub(PROPS + 0x820, lambda: 0x55)  # GetCapture.
        self.stub(PROPS + 0x830, lambda: 0)
        self.stub(PROPS + 0x840, lambda: 56)
        self.stub(PROPS + 0x850, self.point_inside)
        self.stub(0x140035400, lambda: CONTEXT)  # CWnd::FromHandle.
        self.stub(0x140021210, lambda: 0)  # Pan backend notification.
        self.stub(0x140034ed4, lambda: 0)  # Default window key handler.
        self.write(0x1401efde0, PROPS + 0x820, 'Q')
        self.write(0x1401eff30, PROPS + 0x850, 'Q')  # PtInRect.
        # The original resource constructor selects 65 bitmap positions.
        if read(self.cpu, 0x140012f95, 'I') != 65:
            raise RuntimeError('Original knob resource frame count differs.')
        for resource in (0x14046ef08, 0x14046f298):
            if struct.unpack('>II', self.cpu.mem_read(resource + 16, 8)) != (18, 120):
                raise RuntimeError('Original fader resource dimensions differ.')
        if read(self.cpu, 0x14001306e, 'I') != 88 or read(self.cpu, 0x14001313e, 'I') != 88:
            raise RuntimeError('Original fader base height differs.')
        for offset in (0x338, 0x368, 0x370, 0x388, 0x3b0, 0x3b8):
            if read(self.cpu, 0x1402004c0 + offset, 'Q') != read(self.cpu, 0x1402009a0 + offset, 'Q'):
                raise RuntimeError('Channel and master fader handlers differ.')

    def point_inside(self):
        rect = self.cpu.reg_read(registers.UC_X86_REG_RCX)
        point = self.cpu.reg_read(registers.UC_X86_REG_RDX)
        left, top, right, bottom = struct.unpack('<4i', self.cpu.mem_read(rect, 16))
        x, y = struct.unpack('<2i', struct.pack('<Q', point))
        return int(left <= x < right and top <= y < bottom)

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
                       'pan': (), 'volume': (0x3b8,), 'fader': (0x3d0,)}[kind]:
            self.write(PROPS + offset, PROPS + 0x800, 'Q')
        self.write(CONTEXT, PROPS, 'Q')
        self.write(CONTEXT + 0xf8, PROPS + 0x600, 'Q')
        self.write(PROPS + 0x600, PROPS + 0x700, 'Q')
        self.write(PROPS + 0x728, PROPS + 0x810, 'Q')
        self.write(CONTEXT + 0x198, CONTEXT + 0x2000, 'Q')
        self.write(CONTEXT + 0x108, 1)
        self.cpu.mem_write(CONTEXT + 0x10c, struct.pack('<6i', 0, 0, value, value, 0, maximum))
        self.write(CONTEXT + 0x124, maximum)
        if kind == 'fader':
            self.write(CONTEXT + 0x11c, maximum)
            self.write(CONTEXT + 0x120, 0)
            self.write(CONTEXT + 0x124, -maximum)
            self.write(PROPS + 0x718, PROPS + 0x830, 'Q')
            self.write(PROPS + 0x720, PROPS + 0x840, 'Q')
            self.write(CONTEXT + 0x160, PROPS + 0x900, 'Q')
            self.write(PROPS + 0x900, 0x1401f5050, 'Q')
            self.cpu.mem_write(PROPS + 0x970, struct.pack('<8i', 0, 0, 18, 88, 0, 88, 18, 120))
            self.write(PROPS + 0x990, 0)
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

    def click(self, value, y, flags):
        self.prepare('fader', 1024, value)
        self.write(CONTEXT + 0x1ac, read(self.cpu, 0x14029b960 + value * 4))
        HELPERS['execute'](self.cpu, 0x14001f0e0, (CONTEXT, value, 0, 0), windows=True)
        self.write(CONTEXT + 0x168, self.cpu.reg_read(registers.UC_X86_REG_RAX))
        self.write(CONTEXT + 0x128, flags)
        HELPERS['execute'](self.cpu, 0x14001f820, (CONTEXT, 9 | ((y & 0xffffffff) << 32), 0, 0), windows=True)
        return [read(self.cpu, CONTEXT + offset) for offset in (0x114, 0x118, 0x1ac)]


def cases(faders=False):
    points = [(0, 0), (1, 0), (2, 0), (3, -1), (3, -2), (3, -3), (5, -5),
              (7, 8), (-5, -8), (-20, 15), (20, -15), (1000, -1000),
              (999, -999), (-1000, 1000), (-999, 999), (0, 0)]
    kinds = (('fader', (1024,)),) if faders else (
        ('sensitivity', (100,)), ('compressor', (8, 25, 45, 50, 74)), ('pan', (200,)), ('volume', (1024,)))
    for kind, maxima in kinds:
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
    faders = (Path(__file__).resolve().parent.parent / 'gui/web/faders.js').as_uri()
    source = f'''import {{knobDrag}} from {json.dumps(module)};
import {{VOLUME,panToWire}} from {json.dumps(parameters)};
import {{faderDrag}} from {json.dumps(faders)};
let input='';for await(const chunk of process.stdin)input+=chunk;
console.log(JSON.stringify(JSON.parse(input).map(test=>{{
 let state={{x:0,y:0,anchor:test.initial,value:test.initial}};
 return test.events.map(event=>{{const point={{x:event.point[0],y:event.point[1]}};
  state=test.kind==='fader'?faderDrag(state,point,Boolean(event.flags&4),Boolean(event.flags&8)):
   knobDrag(test.kind,test.maximum,state,point,Boolean(event.flags&4),Boolean(event.flags&8));
  const wire=state.wireValue ?? (['volume','fader'].includes(test.kind)?VOLUME[state.value]:
   test.kind==='pan'?panToWire(state.value):state.value);
  const {{x,y,anchor,value}}=state;return {{x,y,anchor,value,wire}};
 }});
}})));'''
    result = subprocess.run(['node', '--input-type=module', '-e', source], input=json.dumps(items),
                            capture_output=True, text=True, timeout=10, check=True)
    return json.loads(result.stdout)


def verify_clicks(reference, destination):
    items = [{'initial': value, 'y': y, 'flags': flags} for value in (0, 1, 511, 512, 513, 1023, 1024)
             for flags in (0, 4, 8, 12) for y in (-1, 0, 15, 16, 17, 27, 28, 31, 32, 43, 44, 59, 60, 71, 72, 73, 87, 88)]
    module = (Path(__file__).resolve().parent.parent / 'gui/web/faders.js').as_uri()
    parameters = (Path(__file__).resolve().parent.parent / 'gui/web/parameters.js').as_uri()
    source = f'''import {{faderClick}} from {json.dumps(module)};
import {{VOLUME}} from {json.dumps(parameters)};
let input='';for await(const chunk of process.stdin)input+=chunk;
console.log(JSON.stringify(JSON.parse(input).map(t=>{{
 const r=faderClick(t.initial,t.y,Boolean(t.flags&4),Boolean(t.flags&8));
 return r?[r.value,r.value,r.wireValue??VOLUME[r.value]]:[t.initial,t.initial,VOLUME[t.initial]];
}})));'''
    result = subprocess.run(['node', '--input-type=module', '-e', source], input=json.dumps(items),
                            capture_output=True, text=True, timeout=10, check=True)
    for test, expected in zip(items, json.loads(result.stdout), strict=True):
        original = reference.click(test['initial'], test['y'], test['flags'])
        if original != expected:
            raise RuntimeError(f'Original fader click differs: {test}, original {original}, web {expected}')
        test['expected'] = original
    if destination:
        destination.write_text('[\n' + ',\n'.join(json.dumps(test, separators=(',', ':')) for test in items) + '\n]\n')
    print(f'PASS {len(items)} original fader click cases: thumb hit-testing, track jumps, bounds and modifiers.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('windows_gui', type=Path)
    parser.add_argument('--save-vectors', type=Path, help='save compared numeric regression traces')
    parser.add_argument('--faders', action='store_true', help='compare channel/master faders instead of knobs')
    parser.add_argument('--save-clicks', type=Path, help='save compared fader click regression cases')
    args = parser.parse_args()
    if args.save_clicks and not args.faders:
        parser.error('--save-clicks requires --faders')
    reference = Reference(args.windows_gui)
    items = list(cases(args.faders))
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
    if args.faders:
        verify_clicks(reference, args.save_clicks)
    print(f'PASS {count} original Windows {"fader" if args.faders else "knob"} drag events: axes, modifiers, truncation, rebasing, bounds and wire values.')
    print(f'PASS {key_count} original arrow/Home/End key cases.')
    print('Window capture/painting and backend notifications are memory stubs. No hardware access or fuzzing.')


if __name__ == '__main__':
    main()
