// SPDX-License-Identifier: GPL-2.0-only
import {knobKey, volumePosition, wheelDelta, wheelValue} from './knobs.js';
import {VOLUME} from './parameters.js';

export const FADER_HEIGHT = 88, FADER_THUMB = 32;
const TRAVEL = FADER_HEIGHT - FADER_THUMB;

export function faderDrag(state, point, shift = false, control = false) {
  const x = Math.trunc(point.x), y = Math.trunc(point.y);
  const raw = Math.max(0, Math.min(1024, state.anchor + Math.trunc((y - state.y) * (shift ? -1 : -1024 / TRAVEL))));
  const result = {x: state.x, y: state.y, anchor: state.anchor, ...volumePosition(raw, shift, control)};
  if (raw !== state.value) Object.assign(result, {x, y, anchor: raw});
  return result;
}

export function faderClick(value, y, shift = false, control = false) {
  const top = Math.round((1024 - value) * TRAVEL / 1024);
  if (y >= top && y < top + FADER_THUMB) return null;
  const raw = Math.max(0, Math.min(1024, Math.trunc((FADER_HEIGHT - FADER_THUMB / 2 - y) * 1024 / TRAVEL + 0.5)));
  return volumePosition(raw, shift, control);
}

export class ReferenceFader {
  constructor(input, change, wheelAllowed = () => true) {
    this.input = input;
    this.changed = change;
    this.dragging = false;
    this.pointer = null;
    this.lastPointer = null;
    input.addEventListener('input', () => change(VOLUME[Number(input.value)]));
    input.addEventListener('pointerdown', event => { this.lastPointer = event.pointerId; this.start(event); });
    input.addEventListener('pointermove', event => {
      if (event.button === 0 && (event.buttons & 1) && !this.dragging) this.start(event);
      else this.move(event);
    });
    input.addEventListener('mousedown', event => {
      if (event.button !== 0) return;
      event.preventDefault();
      if (!this.dragging && this.lastPointer !== null) this.start(event, this.lastPointer);
    });
    for (const name of ['pointerup', 'pointercancel', 'lostpointercapture']) {
      input.addEventListener(name, event => { if (event.pointerId === this.pointer) this.end(); });
    }
    input.addEventListener('mouseup', event => { if (event.button === 0) this.end(); });
    input.addEventListener('keydown', event => {
      if (input.disabled) return;
      const value = knobKey(Number(input.value), 1024, event.key);
      if (value !== null || ['Home', 'End', 'PageUp', 'PageDown'].includes(event.key)) event.preventDefault();
      if (value !== null && value !== Number(input.value)) this.change({value});
    });
    input.addEventListener('wheel', event => {
      event.preventDefault();
      if (input.disabled || !wheelAllowed()) return;
      input.focus();
      const value = wheelValue(Number(input.value), 1024, wheelDelta(event));
      if (value !== Number(input.value)) this.change({value});
    }, {passive: false});
  }
  change(result) {
    this.input.value = result.value;
    this.changed(result.wireValue ?? VOLUME[result.value]);
  }
  end() {
    this.dragging = false;
    const pointer = this.pointer;
    this.pointer = null;
    if (pointer !== null && this.input.hasPointerCapture(pointer)) this.input.releasePointerCapture(pointer);
  }
  start(event, pointer = event.pointerId) {
    if (event.button !== 0 || this.input.disabled) return;
    this.input.focus();
    this.input.setPointerCapture(pointer);
    this.pointer = pointer;
    const value = Number(this.input.value);
    this.origin = {x: Math.trunc(event.clientX), y: Math.trunc(event.clientY), anchor: value, value};
    this.dragging = true;
    const clicked = faderClick(value, Math.trunc(event.clientY - this.input.getBoundingClientRect().top), event.shiftKey, event.ctrlKey);
    if (clicked) {
      this.change(clicked);
      this.origin.anchor = this.origin.value = clicked.value;
    }
  }
  move(event) {
    if (!this.dragging || this.input.disabled) return;
    if (!(event.buttons & 1)) { this.end(); return; }
    if (event.button !== -1) return;
    this.origin.value = Number(this.input.value);
    this.origin = faderDrag(this.origin, {x: event.clientX, y: event.clientY}, event.shiftKey, event.ctrlKey);
    if (this.origin.value !== Number(this.input.value) || this.origin.wireValue !== undefined) this.change(this.origin);
  }
}

// Preview only; the device's fresh bulk reply remains authoritative.
export function masterFaderPreview(state, controls) {
  const result = [];
  for (const control of controls) {
    if (control.kind !== 'master' || control.parameter !== 'volume') continue;
    const mixer = state.mixers[control.bus];
    if (!mixer || !['input', 'playback'].includes(control.source) || ![0, 1].includes(control.channel)) continue;
    const other = control.source === 'input' ? 'playback' : 'input';
    const channels = mixer[`${control.source}_master`].linked ? [0, 1] : [control.channel];
    for (const channel of channels) result.push({...control, channel});
    if (mixer.input_output_link) {
      const peers = mixer[`${other}_master`].linked ? [0, 1] : channels;
      for (const channel of peers) result.push({...control, source: other, channel});
    }
  }
  return result;
}
