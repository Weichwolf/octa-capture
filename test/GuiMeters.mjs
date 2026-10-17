// SPDX-License-Identifier: GPL-2.0-only
import assert from 'node:assert/strict';
import {MeterBallistics, meterDb, meterIndex, headroomIndex, meterY,
  METER_TICK_MS, METER_FALL_DB, METER_HOLD_TICKS, CLIP_HOLD_TICKS} from '../gui/web/meters.js';

const close = (actual, expected) => assert.ok(Math.abs(actual - expected) < 1e-10,
  `${actual} differs from ${expected}`);
assert.equal(METER_TICK_MS, 30);
assert.equal(METER_FALL_DB, 0.72);
assert.equal(METER_HOLD_TICKS, 50);
assert.equal(CLIP_HOLD_TICKS, 133);
for (const [level, low, high, y] of [
  [-96, 0, 0, 59], [-48, 1, 0, 54], [-24, 65, 0, 41], [-12, 129, 0, 29],
  [-6, 193, 0, 16], [-2, 257, 0, 4], [-1, -1, 17, 2], [0, -1, -1, 0],
]) {
  assert.equal(meterIndex(level), low);
  assert.equal(headroomIndex(level), high);
  assert.equal(meterY(level, 59), y);
}
assert.equal(meterDb(0), -96);
assert.equal(meterDb(16383), 0);
close(meterDb(8192), -6.020069752469006);
for (const value of [NaN, -1, 16384, Infinity, 1.5]) assert.equal(meterDb(value), -96);

const model = new MeterBallistics();
model.feed(0); model.step();
assert.equal(model.level, 0);
assert.equal(model.peak, 0);
assert.equal(model.hold, 50);
assert.equal(model.clipHold, 133);
model.feed(-96);
for (let tick = 1; tick <= 49; tick++) model.step();
close(model.level, -35.28);
assert.equal(model.peak, 0);
assert.equal(model.hold, 1);
assert.equal(model.clipHold, 84);
model.step();
close(model.peak, -0.72);
assert.equal(model.hold, 0);
for (let tick = 50; tick < 133; tick++) model.step();
assert.equal(model.clipHold, 0);
model.feed(-2); model.step();
assert.equal(model.peak, -2);
assert.equal(model.hold, 50);
assert.equal(model.clipHold, 0);

const input = new MeterBallistics(-2);
input.feed(-2); input.step();
assert.equal(input.clipHold, 133, 'preamp input warning threshold differs from the output clip');
input.reset();
input.feed(0); input.feed(-96); input.step();
assert.equal(input.peak, 0, 'coalesced notifications retain a short peak');
input.step();
close(input.level, -0.72);
for (let tick = 0; tick < 250; tick++) input.step();
assert.equal(input.level, -96);
assert.equal(input.peak, -96);
assert.equal(input.clipHold, 0);
for (const value of [NaN, Infinity, -100, 1]) input.feed(value);
input.step();
assert.equal(input.level, -96);
console.log('PASS reference meter scale, 30 ms fall/hold/clip timing, transient retention and reset');
