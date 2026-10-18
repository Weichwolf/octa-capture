// SPDX-License-Identifier: GPL-2.0-only
/* eslint-env node */
// Isolated control lifecycle: no HTTP peer, MIDI port or hardware is opened.
import assert from 'node:assert/strict';
import {mock} from 'node:test';
import {ReferenceStepper} from '../gui/web/steppers.js';
import {preDelayText, reverbTimeText} from '../gui/web/parameters.js';

global.window = new EventTarget();
global.document = new EventTarget();
document.hidden = false;
let now = 0, serial = 0;
const timers = new Map();
mock.method(global, 'setTimeout', (callback, delay) => {
  const id = ++serial; timers.set(id, {at: now + delay, callback}); return id;
});
mock.method(global, 'clearTimeout', id => timers.delete(id));
function tick(milliseconds) {
  const end = now + milliseconds;
  while (timers.size) {
    const next = [...timers].sort((left, right)=>left[1].at - right[1].at)[0];
    if (!next || next[1].at > end) break;
    now = next[1].at; timers.delete(next[0]); next[1].callback();
  }
  now = end;
}
function element(step) {
  const node = new EventTarget(), attributes = new Map();
  node.dataset = {step: String(step)};
  node.setAttribute = (name, value) => attributes.set(name, value);
  node.getAttribute = name => attributes.get(name);
  node.focus = () => {};
  node.setPointerCapture = () => {};
  return node;
}
function fire(node, name, fields = {}) {
  const event = Object.assign(new Event(name, {cancelable: true}), fields);
  node.dispatchEvent(event);
  return event;
}
function fixture(maximum, format, unit) {
  const input = element(), up = element(1), down = element(-1), sent = [];
  const node = {querySelector: () => input, querySelectorAll: () => [up, down]};
  const control = new ReferenceStepper(node, maximum, format, unit, index => sent.push(index));
  return {control, input, up, down, sent};
}

const delay = fixture(12, preDelayText, 'ms');
assert.equal(delay.input.value, '0.0');
assert.equal(delay.input.disabled, true);
fire(delay.input, 'keydown', {key: 'ArrowUp'});
fire(delay.up, 'click', {detail: 0});
assert.deepEqual(delay.sent, []);
delay.control.sync(7, true);
assert.equal(delay.input.getAttribute('aria-valuetext'), '6.4 ms');
assert.equal(fire(delay.input, 'keydown', {key: 'ArrowUp'}).defaultPrevented, true);
assert.equal(delay.input.value, '10');
assert.deepEqual(delay.sent, [8]);
delay.control.sync(7, true, true);
assert.equal(delay.input.value, '10', 'Pending device readback cannot undo a local step');
delay.control.sync(9, true);
assert.equal(delay.input.value, '20', 'Completed independent device updates are shown');
fire(delay.input, 'keydown', {key: 'x'});
assert.deepEqual(delay.sent, [8]);
delay.control.sync(12, true);
fire(delay.up, 'click', {detail: 0});
assert.equal(delay.input.value, '160');
assert.deepEqual(delay.sent, [8], 'Upper bound does not wrap');
delay.control.sync(0, true);
fire(delay.down, 'click', {detail: 0});
assert.equal(delay.input.value, '0.0');
assert.deepEqual(delay.sent, [8], 'Lower bound does not wrap');

const time = fixture(49, reverbTimeText, 'seconds');
time.control.sync(0, true);
fire(time.up, 'pointerdown', {button: 0, pointerId: 1});
assert.equal(time.input.value, '0.2');
fire(time.up, 'click', {detail: 1});
assert.deepEqual(time.sent, [1], 'Pointer click cannot duplicate the initial pointer-down step');
tick(499); assert.deepEqual(time.sent, [1]);
tick(1); assert.deepEqual(time.sent, [1, 2]);
tick(200); assert.deepEqual(time.sent, [1, 2, 3, 4]);
fire(time.up, 'pointerup', {pointerId: 1});
tick(2000); assert.deepEqual(time.sent, [1, 2, 3, 4]);
for (const reason of ['pointercancel', 'lostpointercapture', 'blur', 'hidden', 'disabled']) {
  time.control.sync(0, true);
  fire(time.up, 'pointerdown', {button: 0, pointerId: 2});
  const count = time.sent.length;
  if (reason === 'disabled') time.control.sync(time.control.value, false, true);
  else if (reason === 'blur') window.dispatchEvent(new Event('blur'));
  else if (reason === 'hidden') { document.hidden = true; document.dispatchEvent(new Event('visibilitychange')); }
  else fire(time.up, reason, {pointerId: 2});
  tick(2000);
  assert.equal(time.sent.length, count, `${reason} stops autorepeat writes`);
  document.hidden = false;
}
time.control.sync(49, true);
fire(time.input, 'keydown', {key: 'ArrowUp'});
assert.equal(time.input.value, '5.0');
assert.equal(time.input.getAttribute('aria-valuenow'), '5.0');
assert.equal(timers.size, 0, 'No abandoned repeat timer');
mock.restoreAll();
console.log('PASS reverb step bounds, reference text, pending readback, pointer/keyboard and autorepeat cleanup');
