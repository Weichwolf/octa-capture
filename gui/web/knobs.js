// SPDX-License-Identifier: GPL-2.0-only
import {VOLUME, volumeIndex} from './parameters.js';

const VOLUME_STOPS = [8388607, 4184368, 2097152, 1051065, 526780, 264015, 132321, 66317, 33237, 0];

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
