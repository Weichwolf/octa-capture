// SPDX-License-Identifier: GPL-2.0-only
// Goldens are state transitions captured by executing the original macOS routine.
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import {PortWarningState} from '../gui/web/warnings.js';

const reference = JSON.parse(await fs.readFile(new URL('./data/port-warning.json', import.meta.url), 'utf8'));
assert.equal(reference.sha256, '1ada3d6be71a6559ff84e7b21af1c964f0df2ce305c1f45fe0018cc148922092');
assert.equal(reference.function, '0x100032042');
assert.equal(reference.cases.length, 160);
for (const [previous, pending, rate, enabled, ready, show, after] of reference.cases) {
  const state = new PortWarningState();
  state.rate = previous; state.pending = pending;
  assert.equal(state.update(rate, enabled, ready), show,
    JSON.stringify({previous, pending, rate, enabled, ready}));
  assert.equal(state.pending, after);
  assert.equal(state.rate, rate);
}
console.log('PASS all 160 original CPU-derived port-warning transitions');
