#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Compare fixed wheel cases against the original Windows control handlers offline."""

import argparse
import json
from pathlib import Path
import runpy
import subprocess

REFERENCE = runpy.run_path(str(Path(__file__).with_name('verify-knob-drag.py')))
CONTEXT, PROPS = (REFERENCE[name] for name in ('CONTEXT', 'PROPS'))


def cases():
    deltas = [-32768, -241, -240, -239, -121, -120, -119, -1, 0, 1, 119, 120, 121, 239, 240, 241, 32767]
    for kind, maxima in (('sensitivity', (100,)), ('compressor', (8, 25, 45, 50, 74)),
                         ('pan', (200,)), ('volume', (1024,)), ('fader', (1024,))):
        for maximum in maxima:
            for initial in (0, 1, maximum // 2, maximum - 1, maximum):
                for flags in (0, 4, 8, 12):
                    yield {'kind': kind, 'maximum': maximum, 'initial': initial, 'flags': flags, 'deltas': deltas}


def web_results(items):
    root = Path(__file__).resolve().parent.parent / 'gui/web'
    source = f'''import {{wheelValue}} from {json.dumps((root / 'knobs.js').as_uri())};
import {{VOLUME,panToWire}} from {json.dumps((root / 'parameters.js').as_uri())};
let input='';for await(const chunk of process.stdin)input+=chunk;
console.log(JSON.stringify(JSON.parse(input).map(t=>t.deltas.map(delta=>{{
 const v=wheelValue(t.initial,t.maximum,delta);
 return [v,['volume','fader'].includes(t.kind)?VOLUME[v]:t.kind==='pan'?panToWire(v):v];
}}))));'''
    result = subprocess.run(['node', '--input-type=module', '-e', source], input=json.dumps(items),
                            capture_output=True, text=True, timeout=10, check=True)
    return json.loads(result.stdout)


def original(reference, test, delta):
    reference.prepare(test['kind'], test['maximum'], test['initial'])
    cpu = reference.cpu
    wheel_handler = REFERENCE['read'](cpu, PROPS + 0x390, 'Q')
    if wheel_handler != 0x140022af0:
        raise RuntimeError('Original wheel virtual handler differs.')
    send_wheel(reference, delta, test['flags'])
    offset = 0x1b0 if test['kind'] == 'pan' else 0x1a8 if test['kind'] == 'compressor' else 0x1ac
    return [REFERENCE['read'](cpu, CONTEXT + address) for address in (0x118, offset)]


def send_wheel(reference, delta, flags):
    reference.write(0x1401efdd8, PROPS + 0x820, 'Q')  # GetFocus; CWnd::FromHandle is already stubbed.
    packed = ((delta & 0xffff) << 16) | flags
    REFERENCE['HELPERS']['execute'](reference.cpu, 0x140003610, (CONTEXT, packed, 0, 0), windows=True)


def interleaved(reference, destination):
    items = []
    for test in list(cases())[::4]:
        if test['initial'] not in (0, test['maximum'] // 2, test['maximum']):
            continue
        events = []
        for index, flags in enumerate((0, 4, 8, 12)):
            x, y = index * 3, index * 2
            events += [['drag', x + 1, y + 1, flags], ['wheel', 120, 0, flags],
                       ['drag', x + 2, y + 1, flags], ['wheel', -120, 0, flags], ['drag', x + 3, y + 2, flags]]
        items.append({key: test[key] for key in ('kind', 'maximum', 'initial')} | {'events': events})
    root = Path(__file__).resolve().parent.parent / 'gui/web'
    source = f'''import {{knobDrag,wheelValue}} from {json.dumps((root / 'knobs.js').as_uri())};
import {{faderDrag}} from {json.dumps((root / 'faders.js').as_uri())};
import {{VOLUME,panToWire}} from {json.dumps((root / 'parameters.js').as_uri())};
let input='';for await(const chunk of process.stdin)input+=chunk;
console.log(JSON.stringify(JSON.parse(input).map(t=>{{
 let s={{x:0,y:0,anchor:t.initial,value:t.initial}};
 return t.events.map(([kind,x,y,flags])=>{{
  if(kind==='wheel'){{s={{...s,value:wheelValue(s.value,t.maximum,x)}};delete s.wireValue;}}
  else s=t.kind==='fader'?faderDrag(s,{{x,y}},Boolean(flags&4),Boolean(flags&8)):
   knobDrag(t.kind,t.maximum,s,{{x,y}},Boolean(flags&4),Boolean(flags&8));
  const wire=s.wireValue??(['volume','fader'].includes(t.kind)?VOLUME[s.value]:t.kind==='pan'?panToWire(s.value):s.value);
  return [s.x,s.y,s.anchor,s.value,wire];
 }});
}})));'''
    result = subprocess.run(['node', '--input-type=module', '-e', source], input=json.dumps(items),
                            capture_output=True, text=True, timeout=10, check=True)
    for test, web in zip(items, json.loads(result.stdout), strict=True):
        reference.prepare(test['kind'], test['maximum'], test['initial'])
        expected = []
        for event, value in zip(test['events'], web, strict=True):
            kind, x, y, flags = event
            if kind == 'drag':
                step = reference.step((x, y), flags)
                actual = [step[field] for field in ('x', 'y', 'anchor', 'value', 'wire')]
            else:
                send_wheel(reference, x, flags)
                offset = 0x1b0 if test['kind'] == 'pan' else 0x1a8 if test['kind'] == 'compressor' else 0x1ac
                actual = [REFERENCE['read'](reference.cpu, CONTEXT + address)
                          for address in (0x10c, 0x110, 0x114, 0x118, offset)]
            if actual != value:
                raise RuntimeError(f'Interleaved wheel/drag differs: {test}, {event}, original {actual}, web {value}')
            expected.append(actual)
        test['expected'] = expected
    if destination:
        destination.write_text('[\n' + ',\n'.join(json.dumps(t, separators=(',', ':')) for t in items) + '\n]\n')
    print(f'PASS {sum(len(t["events"]) for t in items)} original interleaved wheel/drag events.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('windows_gui', type=Path)
    parser.add_argument('--save-vectors', type=Path, help='save compared numeric regression cases')
    parser.add_argument('--save-interleaved', type=Path, help='save compared wheel/drag regression traces')
    args = parser.parse_args()
    reference = REFERENCE['Reference'](args.windows_gui)
    items = list(cases())
    count = 0
    for test, web in zip(items, web_results(items), strict=True):
        expected = []
        for delta, value in zip(test['deltas'], web, strict=True):
            actual = original(reference, test, delta)
            if actual != value:
                raise RuntimeError(f'Original wheel differs: {test}, delta {delta}, original {actual}, web {value}')
            expected.append(actual)
            count += 1
        test['expected'] = expected
    if args.save_vectors:
        args.save_vectors.write_text('[\n' + ',\n'.join(json.dumps(t, separators=(',', ':')) for t in items) + '\n]\n')
    interleaved(reference, args.save_interleaved)
    print(f'PASS {count} original Windows wheel cases: fine steps, signed deltas, truncation, modifiers and bounds.')
    print('Focus/capture, painting and backend notifications are memory stubs. No hardware access or fuzzing.')


if __name__ == '__main__':
    main()
