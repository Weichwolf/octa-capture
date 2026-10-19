// SPDX-License-Identifier: GPL-2.0-only
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {masterFaderPreview, faderDrag, faderClick} from '../gui/web/faders.js';
import {knobKey} from '../gui/web/knobs.js';
import {VOLUME} from '../gui/web/parameters.js';

const traces = JSON.parse(readFileSync(new URL('data/fader-drag.json', import.meta.url), 'utf8'));
let events = 0, keys = 0;
for (const test of traces) {
  let position = {x: 0, y: 0, anchor: test.initial, value: test.initial};
  for (const [index, [x, y, flags]] of test.events.entries()) {
    position = faderDrag(position, {x, y}, Boolean(flags & 4), Boolean(flags & 8));
    assert.deepEqual([position.x, position.y, position.anchor, position.value,
      position.wireValue ?? VOLUME[position.value]], test.expected[index], `fader start ${test.initial}, event ${index}`);
    events++;
  }
  for (const [key, expected] of Object.entries(test.keys || {})) {
    assert.equal(knobKey(test.initial, 1024, key) ?? test.initial, expected);
    keys++;
  }
}
assert.equal(events, 288);
assert.equal(keys, 18);
console.log(`PASS ${events} original fader drag vectors and ${keys} key cases`);
const clicks = JSON.parse(readFileSync(new URL('data/fader-click.json', import.meta.url), 'utf8'));
for (const test of clicks) {
  const result = faderClick(test.initial, test.y, Boolean(test.flags & 4), Boolean(test.flags & 8));
  const actual = result ? [result.value, result.value, result.wireValue ?? VOLUME[result.value]] :
    [test.initial, test.initial, VOLUME[test.initial]];
  assert.deepEqual(actual, test.expected, `fader click ${JSON.stringify(test)}`);
}
assert.equal(clicks.length, 504);
console.log('PASS 504 original thumb/track click cases');

const state = (input, playback, io) => ({mixers: [{input_master: {linked: input},
  playback_master: {linked: playback}, input_output_link: io}]});
const change = (source = 'input', channel = 0, value = 123) =>
  ({kind: 'master', source, channel, value, bus: 0, parameter: 'volume'});
const values = (settings, changes) => Object.fromEntries(masterFaderPreview(settings, changes)
  .map(value => [`${value.source}:${value.channel}`, value.value]));
assert.deepEqual(values(state(false, false, false), [change()]), {'input:0': 123});
assert.deepEqual(values(state(true, false, false), [change()]), {'input:0': 123, 'input:1': 123});
assert.deepEqual(values(state(false, false, true), [change('input', 1)]), {'input:1': 123, 'playback:1': 123});
assert.deepEqual(values(state(false, true, true), [change()]), {'input:0': 123, 'playback:0': 123, 'playback:1': 123});
assert.deepEqual(values(state(true, false, true), [change('playback', 1)]), {'playback:1': 123, 'input:0': 123, 'input:1': 123});
assert.deepEqual(values(state(true, true, true), [change()]), {'input:0': 123, 'input:1': 123, 'playback:0': 123, 'playback:1': 123});
assert.deepEqual(values(state(false, true, true), [change(), change('input', 1, 456)]),
  {'input:0': 123, 'input:1': 456, 'playback:0': 456, 'playback:1': 456});
assert.deepEqual(masterFaderPreview(state(true, true, true), []), []);
assert.deepEqual(masterFaderPreview(state(true, true, true), [{...change(), kind: 'mixer'}, {...change(), parameter: 'link'}]), []);
console.log('PASS immediate master preview: independent stereo links, one-way I/O expansion and ordered pending writes');
