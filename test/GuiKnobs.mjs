// SPDX-License-Identifier: GPL-2.0-only
// Fixed numeric traces executed by verify-knob-drag.py against the original GUI.
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {knobDrag, knobKey} from '../gui/web/knobs.js';
import {VOLUME, panToWire} from '../gui/web/parameters.js';

const cases = JSON.parse(readFileSync(new URL('data/knob-drag.json', import.meta.url), 'utf8'));
let count = 0, keys = 0;
for (const test of cases) {
  let state = {x: 0, y: 0, anchor: test.initial, value: test.initial};
  for (const [index, [x, y, flags]] of test.events.entries()) {
    state = knobDrag(test.kind, test.maximum, state, {x, y}, Boolean(flags & 4), Boolean(flags & 8));
    const wire = state.wireValue ?? (test.kind === 'volume' ? VOLUME[state.value] :
      test.kind === 'pan' ? panToWire(state.value) : state.value);
    assert.deepEqual([state.x, state.y, state.anchor, state.value, wire], test.expected[index],
      `${test.kind}, maximum ${test.maximum}, start ${test.initial}, event ${index}`);
    count++;
  }
  for (const [key, expected] of Object.entries(test.keys || {})) {
    assert.equal(knobKey(test.initial, test.maximum, key) ?? test.initial, expected,
      `${test.kind}, maximum ${test.maximum}, start ${test.initial}, key ${key}`);
    keys++;
  }
}
assert.equal(count, 2304);
assert.equal(keys, 144);
console.log(`PASS ${count} original knob drag vectors and ${keys} key cases, including modifier changes and endpoint reversals`);
