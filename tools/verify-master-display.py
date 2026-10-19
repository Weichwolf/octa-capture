#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Execute original master readout selection/getters offline; compare web traces."""

import argparse
import copy
import json
from pathlib import Path
import runpy
import struct
import subprocess

from unicorn import x86_const as registers

HELPERS = runpy.run_path(str(Path(__file__).with_name('verify-pcm-peaks.py')))
CONTEXT, PROPS, DATA = (HELPERS[name] for name in ('CONTEXT', 'PROPS', 'DATA'))
WINDOWS_SHA = '634c140bc4e49b0d65c70d5156be19573ad3d440ac5ea24e24d3ad59974da406'
MAC_SHA = '1ada3d6be71a6559ff84e7b21af1c964f0df2ce305c1f45fe0018cc148922092'


class Reference:
    def __init__(self, path, windows):
        self.windows = windows
        self.cpu = HELPERS['cpu_image'](path, WINDOWS_SHA if windows else MAC_SHA, windows=windows)
        self.previous = {'input': [], 'playback': []}
        if windows:
            self.cpu.mem_write(PROPS, bytes(self.cpu.mem_read(0x1402009a0, 0x3e8)))
            # Visibility is a host stub; actual value read/conversion/selection execute.
            self.write(PROPS + 0x3a8, 0x14000ac60, 'Q')
        for index in range(4):
            control = CONTEXT + 0x1000 + index * 0x400
            if windows:
                self.write(control, PROPS, 'Q')
                self.write(control + 0x198, DATA, 'Q')
                self.write(control + 0x1a0, 2 + index // 2)
                self.write(control + 0x1a4, index % 2)
                self.write(control + 0x1a8, 1)
                self.write(control + 0x108, 1)  # Skip window-position painting.
            else:
                self.write(control + 0xe8, DATA, 'Q')
                self.write(control + 0xf0, 2 + index // 2)
                self.write(control + 0xf4, index % 2)
        self.write(CONTEXT + (0x110 if windows else 0x60), DATA, 'Q')

    def write(self, address, value, code='I'):
        self.cpu.mem_write(address, struct.pack('<' + code, value))

    def call(self, address, *arguments):
        HELPERS['execute'](self.cpu, address, (*arguments, *([0] * (4 - len(arguments)))), windows=self.windows)
        return self.cpu.reg_read(registers.UC_X86_REG_RAX) & 0xffffffff

    def select(self, source, channel):
        index = (0 if source == 'input' else 2) + channel
        self.call(0x14001f040 if self.windows else 0x100023844, CONTEXT + 0x1000 + index * 0x400)

    def sync(self, bus, values):
        self.write(DATA + 0x224, bus)
        for source_index, source in enumerate(('input', 'playback')):
            offset, stride = (0x600, 0x208) if self.windows else (0x37c, 0x198)
            for channel, value in enumerate(values[source]):
                self.write(DATA + offset + source_index * 0x10 + bus * stride + 8 + channel * 4, value)
        for source_index, source in enumerate(('input', 'playback')):
            for channel, value in enumerate(values[source]):
                if self.windows:
                    self.call(0x140017f10, CONTEXT + 0x1000 + (source_index * 2 + channel) * 0x400,
                              int(not self.previous[source]))
                elif not self.previous[source] or value != self.previous[source][channel]:
                    # The macOS notification scheduler is modeled; its selection callback executes.
                    self.select(source, channel)
            self.previous[source] = list(values[source])

    def displayed(self, bus):
        result = {}
        for index, source in enumerate(('input', 'playback')):
            self.write(CONTEXT + (0x190 if self.windows else 0xd8), index + 2)
            self.write(CONTEXT + (0x194 if self.windows else 0xdc), bus)
            result[source] = self.call(0x140019900 if self.windows else 0x10002402c, CONTEXT)
        return result


def traces():
    for bus in range(4):
        initial = {'input': [0x200000, 0x100000 + bus], 'playback': [0x400000, 0x80000 + bus]}
        steps = [{'values': copy.deepcopy(initial)}]
        for source in ('input', 'playback'):
            steps.append({'values': copy.deepcopy(initial), 'select': [source, 0]})
        steps.append({'values': copy.deepcopy(initial)})  # An unchanged poll retains selection.
        initial['input'][1] = 0x4074f
        steps.append({'values': copy.deepcopy(initial)})
        initial['playback'] = [0, 0x7fffff]
        steps.append({'values': copy.deepcopy(initial)})
        steps.append({'values': copy.deepcopy(initial), 'select': ['input', 0]})
        steps.append({'values': copy.deepcopy(initial), 'select': ['playback', 0]})
        yield {'bus': bus, 'steps': steps}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('windows_gui', type=Path)
    parser.add_argument('--macos', type=Path, required=True)
    parser.add_argument('--save-vectors', type=Path)
    args = parser.parse_args()
    references = [Reference(args.windows_gui, True), Reference(args.macos, False)]
    cases = list(traces())
    for case in cases:
        for step in case['steps']:
            results = []
            for reference in references:
                reference.sync(case['bus'], step['values'])
                if 'select' in step:
                    reference.select(*step['select'])
                results.append(reference.displayed(case['bus']))
            if results[0] != results[1]:
                raise RuntimeError(f'Windows/macOS master readout differs: {case["bus"]}/{step}: {results}')
            step['expected'] = results[0]
    script = """
import {MasterFaderDisplay} from './gui/web/faders.js';
let text=''; for await (const part of process.stdin) text+=part;
const display=new MasterFaderDisplay();
console.log(JSON.stringify(JSON.parse(text).map(test=>test.steps.map(step=>{
 const mixer={input_master:{volume:step.values.input},playback_master:{volume:step.values.playback}};
 display.sync(mixer,[]); if(step.select) display.select(...step.select);
 return display.sync(mixer,[]);
}))));
"""
    actual = json.loads(subprocess.check_output(['node', '--input-type=module', '-e', script],
                                              input=json.dumps(cases).encode(), cwd=Path(__file__).resolve().parent.parent))
    if actual != [[step['expected'] for step in case['steps']] for case in cases]:
        raise RuntimeError(f'Web master readout differs from original execution: {actual}')
    if args.save_vectors:
        args.save_vectors.write_text(json.dumps({'windows_sha256': WINDOWS_SHA, 'macos_sha256': MAC_SHA,
                                               'cases': cases}, separators=(',', ':')) + '\n')
    print('PASS 32 master readout traces per reference: all buses/sources, independent side selection, '
          'changed/unchanged polls and raw coefficients; web results match both originals.')
    print('Window visibility/painting and macOS notification scheduling are modeled. No hardware access or fuzzing.')


if __name__ == '__main__':
    main()
