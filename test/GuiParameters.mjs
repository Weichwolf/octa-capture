// SPDX-License-Identifier: GPL-2.0-only
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {ATTACK, RELEASE, RATIO_TEXT, PRE_DELAY, VOLUME, volumeIndex, volumeText,
  parameterText, panToWire, wireToPan, compressorOutput, sensitivityIndex, preDelayText, reverbTimeText, outputJackStates,
  availableSampleRates} from '../gui/web/parameters.js';

assert.equal(VOLUME.length, 1025);
assert.equal(VOLUME[0], 0);
assert.equal(VOLUME[768], 0x200000);
assert.equal(VOLUME[1024], 0x7fffff);
for (let index = 0; index < VOLUME.length; index++) {
  assert.equal(volumeIndex(VOLUME[index]), index);
  if (index > 0) {
    const low = VOLUME[index - 1], high = VOLUME[index];
    assert.ok(low < high);
    const middle = Math.floor((low + high) / 2);
    assert.equal(volumeIndex(middle), middle - low < high - middle ? index - 1 : index);
  }
}
for (const value of [-1, 0x800000, NaN, 1.5, undefined]) assert.equal(volumeIndex(value), null);
assert.equal(volumeText(0), '-INF dB');
assert.equal(volumeText(0x200000), '0.0 dB');
assert.equal(ATTACK.length, 26);
assert.equal(RELEASE.length, 46);
assert.equal(RATIO_TEXT.length, 9);
assert.equal(PRE_DELAY.length, 13);
assert.equal(preDelayText(0), '0.0');
assert.equal(preDelayText(1), '0.1');
assert.equal(preDelayText(8), '10');
assert.equal(preDelayText(12), '160');
assert.equal(reverbTimeText(0), '0.1');
assert.equal(reverbTimeText(9), '1.0');
assert.equal(reverbTimeText(49), '5.0');
for (const value of [-1, NaN, 1.5, undefined]) {
  assert.equal(preDelayText(value), '--'); assert.equal(reverbTimeText(value), '--');
}
assert.equal(preDelayText(13), '--');
assert.equal(reverbTimeText(50), '--');
for (const [parameter, index, expected] of [
  ['sensitivity', 1, '0.5 dB'], ['gate', 0, '-INF dB'], ['gate', 1, '-69.0 dB'],
  ['gate', 50, '-20.0 dB'], ['threshold', 0, '-50.0 dB'], ['threshold', 50, '0.0 dB'],
  ['threshold', 40, '-10.0 dB'], ['threshold', 41, '- 9.0 dB'], ['threshold', 49, '- 1.0 dB'],
  ['gain', 40, '-10.0 dB'], ['gain', 41, '- 9.0 dB'], ['gain', 49, '- 1.0 dB'], ['gain', 50, '0.0 dB'],
  ['gain', 74, '24.0 dB'], ['attack', 0, '0.2 ms'], ['attack', 1, '0.5 ms'],
  ['attack', 25, '100 ms'], ['release', 1, '12 ms'], ['release', 45, '500 ms'],
  ['ratio', 8, '1:INF'], ['ratio', 9, '--'], ['attack', -1, '--'], ['gain', 1.5, '--'],
]) assert.equal(parameterText(parameter, index), expected);
for (let position = 0; position <= 200; position++) assert.equal(wireToPan(panToWire(position)), position);
const compressor = {threshold_index: 30, gain_index: 50, ratio_index: 5};
assert.equal(compressorOutput(-30, compressor), -30);
assert.equal(compressorOutput(-20, compressor), -20);
assert.equal(compressorOutput(-10, compressor), -17.5);
assert.equal(compressorOutput(0, {...compressor, ratio_index: 8}), -20);
assert.equal(compressorOutput(0, {...compressor, gain_index: 56}), -9);
for (const [text, index] of [['0', 0], ['.5', 1], ['1.', 2], ['8.25', 16], ['25.9', 51], ['50.0', 100]]) {
  assert.equal(sensitivityIndex(text), index);
}
for (const text of ['', '.', '1..2', '1e1', '-1', '50.1', '100.0', 'NaN', ' 1', null]) {
  assert.equal(sensitivityIndex(text), null);
}
console.log('PASS fixed fader/parameter tables, rounding, units and compressor curve');

const jackStates = ['other', 'assigned', 'unavailable'];
const jackReference = JSON.parse(readFileSync(new URL('data/output-jacks.json', import.meta.url), 'utf8'));
let jackCases = 0;
for (let rate = 0; rate < 4; rate++) {
  for (let bus = 0; bus < 4; bus++) {
    for (let pair = 0; pair < 5; pair++) {
      for (let route = 0; route < 9; route++) {
        const routes = Array.from({length: 5}, (_, index) => (index + route) % 9);
        routes[pair] = route;
        assert.equal(outputJackStates(rate, bus, routes)[pair], jackStates[jackReference.states[rate][bus][pair][route]],
          'Output-jack state matches actual Windows/macOS selector execution');
        jackCases++;
      }
    }
  }
}
assert.equal(jackCases, 720);
console.log('PASS 720 original output-jack selectors: all rates, mixers, pairs and routes');

const clocks = JSON.parse(readFileSync(new URL('data/rate-availability.json', import.meta.url), 'utf8'));
assert.equal(clocks.cases.length, 64);
for (const {status, allowed} of clocks.cases) assert.deepEqual(availableSampleRates(status), allowed);
for (const status of [null, false, [], {}, {raw_status_hex: []}, {raw_status_hex: 'xx'.repeat(8)},
  {sample_rate_hz: 48000, raw_status_hex: '00000080bb000001'},
  {sample_rate_hz: 96000, raw_status_hex: '01000080bb000001'},
  {sample_rate_hz: 48000, raw_status_hex: '01000080bb000001 '},
  {sample_rate_hz: 0, raw_status_hex: '0100000000000001'}]) assert.deepEqual(availableSampleRates(status), []);
console.log('PASS 64 original clock-availability vectors and unavailable/malformed-status guards');
