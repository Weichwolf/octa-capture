// SPDX-License-Identifier: GPL-2.0-only
import assert from 'node:assert/strict';
import {masterFaderPreview} from '../gui/web/faders.js';
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
