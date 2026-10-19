// SPDX-License-Identifier: GPL-2.0-only
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {wheelValue, wheelDelta, knobDrag} from '../gui/web/knobs.js';
import {faderDrag} from '../gui/web/faders.js';
import {VOLUME, panToWire} from '../gui/web/parameters.js';

const cases = JSON.parse(readFileSync(new URL('data/control-wheel.json', import.meta.url), 'utf8'));
let count = 0;
for (const test of cases) {
  for (const [index, delta] of test.deltas.entries()) {
    const value = wheelValue(test.initial, test.maximum, delta);
    const wire = ['volume', 'fader'].includes(test.kind) ? VOLUME[value] : test.kind === 'pan' ? panToWire(value) : value;
    assert.deepEqual([value, wire], test.expected[index], `${test.kind}, maximum ${test.maximum}, start ${test.initial}, delta ${delta}, flags ${test.flags}`);
    count++;
  }
}
assert.equal(count, 3060);
const traces = JSON.parse(readFileSync(new URL('data/wheel-drag.json', import.meta.url), 'utf8'));
let drags = 0;
for (const test of traces) {
  let state = {x: 0, y: 0, anchor: test.initial, value: test.initial};
  for (const [index, [kind, x, y, flags]] of test.events.entries()) {
    if (kind === 'wheel') {
      state = {...state, value: wheelValue(state.value, test.maximum, x)};
      delete state.wireValue;
    } else {
      state = test.kind === 'fader' ? faderDrag(state, {x, y}, Boolean(flags & 4), Boolean(flags & 8)) :
        knobDrag(test.kind, test.maximum, state, {x, y}, Boolean(flags & 4), Boolean(flags & 8));
    }
    const wire = state.wireValue ?? (['volume', 'fader'].includes(test.kind) ? VOLUME[state.value] :
      test.kind === 'pan' ? panToWire(state.value) : state.value);
    assert.deepEqual([state.x, state.y, state.anchor, state.value, wire], test.expected[index],
      `${test.kind}, interleaved wheel/drag ${index}`);
    drags++;
  }
}
assert.equal(drags, 540);
for (const [event, expected] of [
  [{wheelDeltaY: 120, deltaY: -100, deltaMode: 0}, 120],
  [{wheelDeltaY: 60, deltaY: -20, deltaMode: 0}, 60],
  [{wheelDeltaY: 0, wheelDelta: 120, deltaY: 0}, 0],
  [{wheelDeltaY: -240, deltaY: 100, deltaMode: 0}, -240],
  [{wheelDelta: -120, deltaY: 100, deltaMode: 0}, -120],
  [{deltaY: 120, deltaMode: 0}, -120],
  [{deltaY: -3, deltaMode: 1}, 120],
  [{deltaY: 1, deltaMode: 2}, -120],
  [{deltaY: 0, deltaX: 120, deltaMode: 0}, -0],
  [{deltaY: NaN, deltaMode: 0}, 0],
]) assert.equal(wheelDelta(event), expected);
assert.equal(wheelValue(wheelValue(10, 100, 60), 100, 60), 10, 'Partial deltas are not accumulated');
console.log(`PASS ${count} original Windows wheel cases, ${drags} interleaved events and DOM native/pixel/line/page normalization`);
