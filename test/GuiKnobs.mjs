// SPDX-License-Identifier: GPL-2.0-only
// Fixed numeric traces executed by verify-knob-drag.py against the original GUI.
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {knobDrag, knobKey, controlPointerHit, controlContextHit, controlContext, controlButton, switchFrame} from '../gui/web/knobs.js';
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

const hits = JSON.parse(readFileSync(new URL('data/control-hit.json', import.meta.url), 'utf8'));
for (const test of hits) {
  const target = {getBoundingClientRect: () => ({left: 0, top: 0, right: test.width, bottom: test.height})};
  assert.equal(controlContextHit({type: 'mouseup', button: 2, buttons: test.flags & 3,
    clientX: test.point[0], clientY: test.point[1]}, target), test.expected, JSON.stringify(test));
}
assert.equal(hits.length, 192);
const target = new EventTarget(), menus = [];
target.getBoundingClientRect = () => ({left: 10, top: 20, right: 35, bottom: 45});
controlContext(target, event => { if (controlContextHit(event, target)) menus.push(event.type); });
function mouse(type, fields) {
  const event = Object.assign(new Event(type, {cancelable: true}), fields);
  target.dispatchEvent(event); return event;
}
assert.equal(mouse('contextmenu', {button: 2, buttons: 2, clientX: 20, clientY: 30}).defaultPrevented, true);
assert.deepEqual(menus, [], 'Native early context event waits for right-button release');
mouse('mouseup', {button: 2, buttons: 1, clientX: 20, clientY: 30});
mouse('mouseup', {button: 2, buttons: 0, clientX: 35, clientY: 30});
assert.deepEqual(menus, [], 'Left-held or outside releases cannot open menus');
mouse('mouseup', {button: 2, buttons: 0, clientX: 20, clientY: 30});
mouse('contextmenu', {button: 2, buttons: 0, clientX: 20, clientY: 30});
assert.deepEqual(menus, ['mouseup'], 'Late native context event cannot duplicate the popup');
mouse('contextmenu', {button: -1, buttons: 0});
assert.deepEqual(menus, ['mouseup', 'contextmenu'], 'Keyboard context remains available');
assert.equal(mouse('keydown', {key: 'F10', shiftKey: true}).defaultPrevented, true);
assert.equal(mouse('keydown', {key: 'ContextMenu'}).defaultPrevented, true);
assert.deepEqual(menus, ['mouseup', 'contextmenu', 'keydown', 'keydown'], 'Keyboard context works without a browser default event');
mouse('keydown', {key: 'F10', shiftKey: true, repeat: true});
mouse('keydown', {key: 'F10', shiftKey: true, ctrlKey: true});
assert.equal(menus.length, 4, 'Repeat and extra modifiers cannot reopen a keyboard menu');
assert.equal(controlPointerHit({type: 'click', detail: 0}, target), true, 'Keyboard activation ignores mouse coordinates');
assert.equal(controlPointerHit({type: 'click', detail: 1, clientX: 9, clientY: 30}, target), false);
target.dataset = {};
target.querySelector = () => target;
target.classList = {contains: () => false};
target.disabled = false;
const window = new EventTarget(), captures = new Set();
window.MutationObserver = class { observe() {} };
target.ownerDocument = Object.assign(new EventTarget(), {defaultView: window});
target.setPointerCapture = pointer => captures.add(pointer);
target.hasPointerCapture = pointer => captures.has(pointer);
target.releasePointerCapture = pointer => captures.delete(pointer);
let clicks = 0;
controlButton(target, '.lamp', () => { clicks++; });
assert.equal(mouse('pointerdown', {button: 0, clientX: 9, clientY: 30}).defaultPrevented, true);
mouse('click', {detail: 1, clientX: 9, clientY: 30});
assert.equal(clicks, 0, 'Label does not activate the button');
mouse('click', {detail: 1, clientX: 10, clientY: 20});
assert.equal(clicks, 0, 'Moving from an inactive label to a skin cannot activate it');
mouse('pointerdown', {button: 0, clientX: 10, clientY: 20});
mouse('click', {detail: 1, clientX: 10, clientY: 20});
mouse('click', {detail: 0});
assert.equal(clicks, 2, 'Skin hit and keyboard click activate the button');
const frames = JSON.parse(readFileSync(new URL('data/switch-frames.json', import.meta.url), 'utf8'));
for (const test of frames.cases) {
  assert.equal(switchFrame(test.lit, test.pressed, test.disabled), test.expected, 'Original Windows/macOS switch clip');
}
assert.equal(frames.cases.length, 8);
console.log('PASS 192 original context hit guards, release timing, keyboard access and inactive labels');
console.log('PASS eight original Windows/macOS switch clip selectors');
