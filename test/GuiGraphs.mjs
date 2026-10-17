// SPDX-License-Identifier: GPL-2.0-only
// Filled polygons and line vertices are captured from original CPU execution.
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import {GRAPH_STYLE, compressorShape} from '../gui/web/graphs.js';

const reference = JSON.parse(await fs.readFile(new URL('./data/compressor-render.json', import.meta.url), 'utf8'));
assert.deepEqual(GRAPH_STYLE, reference.style);
assert.equal(reference.cases.length, 48);
for (const {input, shape} of reference.cases) {
  const actual = compressorShape(input);
  for (const name of ['polygon', 'line']) {
    assert.equal(actual[name].length, shape[name].length);
    for (let index = 0; index < shape[name].length; index++) {
      for (let axis = 0; axis < 2; axis++) {
        assert.ok(Math.abs(actual[name][index][axis] - shape[name][index][axis]) < 1e-9,
          JSON.stringify({input, name, index, axis}));
      }
    }
  }
}
console.log('PASS original compressor polygons, line vertices and both drawing palettes');
