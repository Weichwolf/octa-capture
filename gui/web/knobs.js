// SPDX-License-Identifier: GPL-2.0-only
import {VOLUME, volumeIndex} from './parameters.js';

const VOLUME_STOPS = [8388607, 4184368, 2097152, 1051065, 526780, 264015, 132321, 66317, 33237, 0];

export function knobFrame(value, maximum) {
  return Math.max(0, Math.min(64, Math.trunc((value * 64 + Math.trunc(maximum / 2)) / maximum)));
}

export function knobAngle(value, maximum) {
  return -128 + 4 * knobFrame(value, maximum);
}

export function controlPointerHit(event, target) {
  if (event.type === 'click' && event.detail === 0) return true;
  if (event.type === 'contextmenu' && event.button !== 2) return true;
  if (event.type === 'keydown' && (event.key === 'ContextMenu' || (event.shiftKey && event.key === 'F10'))) return true;
  const rect = target.getBoundingClientRect();
  return event.clientX >= rect.left && event.clientX < rect.right && event.clientY >= rect.top && event.clientY < rect.bottom;
}

export function controlContextHit(event, target) {
  return !(event.buttons & 1) && controlPointerHit(event, target);
}

export function switchFrame(lit, pressed, disabled) {
  return Number(disabled) * 4 + Number(lit) * 2 + Number(pressed);
}

export function controlButton(node, selector, activate) {
  let pressedInside = false, pressed = false, pointer = null, lastPointer = null;
  node.dataset.pointerTarget = selector;
  const paint = () => {
    node.dataset.buttonFrame = String(switchFrame(node.classList.contains('lit'), pressed && !node.disabled, node.disabled));
  };
  const release = () => {
    const previous = pointer;
    pointer = null;
    pressed = false;
    if (previous !== null && node.hasPointerCapture(previous)) node.releasePointerCapture(previous);
    paint();
  };
  const cancel = () => { pressedInside = false; release(); };
  const begin = (event, id) => {
    if (event.button !== 0 || node.disabled) return;
    pressedInside = controlPointerHit(event, node.querySelector(selector));
    pressed = pressedInside;
    if (pressedInside && id !== null) { pointer = id; node.setPointerCapture(id); }
    paint();
    if (!pressedInside) event.preventDefault();
  };
  node.addEventListener('pointerdown', event => {
    lastPointer = event.pointerId;
    begin(event, event.pointerId);
  });
  node.addEventListener('mousedown', event => { begin(event, lastPointer); });
  node.addEventListener('pointermove', event => {
    if (event.pointerId !== pointer) return;
    if (!(event.buttons & 1)) { cancel(); return; }
    pressed = pressedInside && controlPointerHit(event, node.querySelector(selector));
    paint();
  });
  for (const name of ['pointerup', 'mouseup']) {
    node.addEventListener(name, event => { if (event.button === 0) release(); });
  }
  for (const name of ['pointercancel', 'lostpointercapture']) {
    node.addEventListener(name, event => { if (event.pointerId === pointer) cancel(); });
  }
  node.addEventListener('click', event => {
    const activatePointer = pressedInside && controlPointerHit(event, node.querySelector(selector));
    cancel();
    if (!node.disabled && (event.detail === 0 || activatePointer)) activate(event);
  });
  const window = node.ownerDocument.defaultView;
  window.addEventListener('blur', cancel);
  node.ownerDocument.addEventListener('visibilitychange', () => { if (node.ownerDocument.hidden) cancel(); });
  new window.MutationObserver(() => { if (node.disabled) cancel(); else paint(); })
    .observe(node, {attributes: true, attributeFilter: ['class', 'disabled']});
  paint();
}

export function controlContext(node, open) {
  // Windows opens these menus on right-button release, including chorded input.
  node.addEventListener('mouseup', event => { if (event.button === 2) open(event); });
  node.addEventListener('contextmenu', event => {
    event.preventDefault();
    // Native keyboard/assistive context events have no right mouse button.
    if (event.button !== 2) open(event);
  });
  node.addEventListener('keydown', event => {
    if (event.ctrlKey || event.altKey || event.metaKey ||
        (event.key !== 'ContextMenu' && !(event.shiftKey && event.key === 'F10'))) return;
    event.preventDefault();
    if (!event.repeat) open(event);
  });
}

export function wheelValue(value, maximum, delta) {
  return Math.max(0, Math.min(maximum, value + Math.trunc(delta / 120)));
}

export function wheelDelta(event) {
  // Chromium/WebKit preserve native wheel units separately from scroll pixels.
  if (Number.isFinite(event.wheelDeltaY)) return event.wheelDeltaY;
  if (Number.isFinite(event.wheelDelta)) return event.wheelDelta;
  const scale = event.deltaMode === 1 ? 40 : event.deltaMode === 2 ? 120 : 1;
  return Number.isFinite(event.deltaY) ? -event.deltaY * scale : 0;
}

export function volumePosition(raw, shift, control) {
  if (control) {
    let index = 0, threshold = 0x5aaad6;
    while (VOLUME[raw] < threshold && index < 9) { threshold = Math.trunc(threshold / 2); index++; }
    const value = VOLUME_STOPS[index];
    const wireValue = value < 33237 ? 0 : value > 4184368 ? 8388607 : value;
    return {value: volumeIndex(wireValue), wireValue};
  }
  return {value: shift ? raw : Math.trunc((raw + 7) / 8) * 8};
}

export function knobKey(value, maximum, key) {
  if (key === 'ArrowUp' || key === 'ArrowRight') return Math.min(maximum, value + 1);
  if (key === 'ArrowDown' || key === 'ArrowLeft') return Math.max(0, value - 1);
  return null;
}

// The original bitmap has 65 positions; movement uses their 64 intervals.
export function knobDrag(kind, maximum, state, point, shift = false, control = false) {
  const x = Math.trunc(point.x), y = Math.trunc(point.y);
  const dx = x - state.x, dy = y - state.y;
  const delta = Math.abs(dx) > Math.abs(dy) ? dx : -dy;
  const scale = kind === 'pan' ? (shift ? 0.5 : 2) :
    kind === 'sensitivity' ? (shift ? -0.2 : maximum / 64) :
      kind === 'volume' ? (shift ? 1 : maximum / 192) : shift ? maximum / 256 : maximum * 2 / 192;
  const raw = Math.max(0, Math.min(maximum, state.anchor + Math.trunc(delta * scale)));
  const result = {x: state.x, y: state.y, anchor: state.anchor, value: raw};
  // Rebase only on an integer change, before parameter-specific quantization.
  if (raw !== state.value) Object.assign(result, {x, y, anchor: raw});
  if (kind === 'pan') {
    if (!shift && !control) result.value = Math.trunc((raw + 4) / 5) * 5;
    if (control) result.value = Math.trunc(raw / 67) * 100;
  } else if (kind === 'volume') {
    Object.assign(result, volumePosition(raw, shift, control));
  }
  return result;
}
