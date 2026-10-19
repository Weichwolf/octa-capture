#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Compare fixed control hit cases with original Windows right-button handlers."""

import argparse
import json
from pathlib import Path
import runpy
import struct
import subprocess

REFERENCE = runpy.run_path(str(Path(__file__).with_name('verify-knob-drag.py')))
CONTEXT, PROPS = REFERENCE['CONTEXT'], REFERENCE['PROPS']
REGISTERS = REFERENCE['registers']


class Reference(REFERENCE['Reference']):
    def __init__(self, path):
        super().__init__(path)
        self.stub(PROPS + 0xa00, self.rectangle)
        self.stub(PROPS + 0xa10, self.menu)
        self.stub(PROPS + 0xa20, lambda: int(self.enabled))
        self.write(0x1401eff28, PROPS + 0xa00, 'Q')  # GetClientRect.

    def rectangle(self):
        destination = self.cpu.reg_read(REGISTERS.UC_X86_REG_RDX)
        self.cpu.mem_write(destination, struct.pack('<4i', 0, 0, self.width, self.height))
        return 1

    def menu(self):
        if self.cpu.reg_read(REGISTERS.UC_X86_REG_RCX) != CONTEXT:
            raise RuntimeError('Unexpected original context-menu owner.')
        self.menus += 1
        return 0

    def hit(self, case, enabled=True):
        self.prepare('sensitivity', 100, 50)
        self.width, self.height = case['width'], case['height']
        self.enabled, self.menus = enabled, 0
        self.write(CONTEXT + 0x40, 0x55, 'Q')
        for offset, address in ((0x2e0, 0xa00), (0x328, 0xa20), (0x330, 0xa10), (0x360, 0xa10)):
            self.write(PROPS + offset, PROPS + address, 'Q')
        x, y = case['point']
        point = (x & 0xffffffff) | ((y & 0xffffffff) << 32)
        handler = 0x140003510 if case['kind'] == 'button' else 0x1400035a0
        REFERENCE['HELPERS']['execute'](self.cpu, handler, (CONTEXT, case['flags'], point, 0), windows=True)
        if self.menus not in (0, 1):
            raise RuntimeError('Original handler opened multiple context menus.')
        return self.menus == 1


def cases():
    for kind, width, height in (('knob', 25, 25), ('button', 23, 19)):
        points = ((-1, -1), (0, 0), (width - 1, height - 1), (width, height - 1), (width - 1, height),
                  (-1, height // 2), (width // 2, -1), (width, height // 2), (width // 2, height),
                  (width // 2, height // 2), (0, height - 1), (width - 1, 0))
        for flags in (0, 1, 2, 3, 4, 5, 8, 9):
            for point in points:
                yield {'kind': kind, 'width': width, 'height': height, 'point': point, 'flags': flags}


def web_results(items):
    module = (Path(__file__).resolve().parent.parent / 'gui/web/knobs.js').as_uri()
    source = f'''import {{controlContextHit}} from {json.dumps(module)};
let input='';for await(const chunk of process.stdin)input+=chunk;
console.log(JSON.stringify(JSON.parse(input).map(t=>controlContextHit({{type:'mouseup',button:2,
 buttons:t.flags&3,shiftKey:Boolean(t.flags&4),ctrlKey:Boolean(t.flags&8),clientX:t.point[0],clientY:t.point[1]}},
 {{getBoundingClientRect:()=>({{left:0,top:0,right:t.width,bottom:t.height}})}}))));'''
    output = subprocess.run(['node', '--input-type=module', '-e', source], input=json.dumps(items),
                            capture_output=True, text=True, timeout=10, check=True)
    return json.loads(output.stdout)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('windows_gui', type=Path)
    parser.add_argument('--save-vectors', type=Path, help='write compared numeric regression cases')
    args = parser.parse_args()
    reference, items = Reference(args.windows_gui), list(cases())
    disabled = 0
    for test, web in zip(items, web_results(items), strict=True):
        original = reference.hit(test)
        if original != web:
            raise RuntimeError(f'Original hit test differs: {test}, original {original}, web {web}')
        test['expected'] = original
        if test['kind'] == 'button':
            if reference.hit(test, enabled=False):
                raise RuntimeError('Original disabled button opened a context menu.')
            disabled += 1
    if args.save_vectors:
        args.save_vectors.write_text('[\n' + ',\n'.join(json.dumps(test, separators=(',', ':')) for test in items) + '\n]\n')
    print(json.dumps({'original_right_release_cases': len(items), 'disabled_button_cases': disabled,
                      'hardware_validation': False, 'host_dependencies': 'client rectangles, enabled gate and popup dispatch stubs'}))


if __name__ == '__main__':
    main()
