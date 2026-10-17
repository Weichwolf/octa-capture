// SPDX-License-Identifier: GPL-2.0-only
import assert from 'node:assert/strict';
import {ATTACK, RELEASE, RATIO_TEXT, PRE_DELAY, VOLUME, volumeIndex, volumeText,
  parameterText, panToWire, wireToPan, compressorOutput} from '../gui/web/parameters.js';

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
for (const [parameter, index, expected] of [
  ['sensitivity', 1, '0.5 dB'], ['gate', 0, '-INF dB'], ['gate', 1, '-69.0 dB'],
  ['gate', 50, '-20.0 dB'], ['threshold', 0, '-50.0 dB'], ['threshold', 50, '0.0 dB'],
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
console.log('PASS fixed fader/parameter tables, rounding, units and compressor curve');
