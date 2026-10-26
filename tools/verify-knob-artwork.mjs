#!/usr/bin/env node
// SPDX-License-Identifier: GPL-2.0-only
/* eslint-env node */
// Measure reference needle positions; ship numerical facts and authored vectors only.
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import fs from 'node:fs/promises';
import path from 'node:path';
import {parseArgs} from 'node:util';
import {PlaywrightClient} from '../test/PlaywrightClient.mjs';
import {knobAngle} from '../gui/web/knobs.js';

const root = path.resolve(new URL('..', import.meta.url).pathname);
const windowsHash = '634c140bc4e49b0d65c70d5156be19573ad3d440ac5ea24e24d3ad59974da406';
const pngHash = 'fa24d51104ab36d2902bb9d36d75913269270121ab0e57d948bc4417e51e9401';
const hash = value => createHash('sha256').update(value).digest('hex');
const {values, positionals} = parseArgs({allowPositionals: true, options: {
  macos: {type: 'string'}, 'save-vectors': {type: 'string'},
}});
assert.equal(positionals.length, 1,
  'Usage: node tools/verify-knob-artwork.mjs RDDP1110.EXE [--macos KNOB.png] [--save-vectors FILE]');
const executable = await fs.readFile(positionals[0]);
assert.equal(hash(executable), windowsHash, 'Expected original Windows 1.5.3 x64 panel');
const png = executable.subarray(0x4817d0, 0x4817d0 + 12535);
assert.equal(hash(png), pngHash, 'Expected PNG resource 300');
if (values.macos) assert.deepEqual(await fs.readFile(values.macos), png, 'Both references must use identical artwork');

// Runs in Chromium for PNG decoding and measurement, independently of the web controls.
function measurements({reference, sheet}) {
  function marker(data, width, x, y) {
    let weight = 0, sumX = 0, sumY = 0;
    for (let row = 0; row < 25; row++) {
      for (let col = 0; col < 25; col++) {
        const index = ((y + row) * width + x + col) * 4;
        const red = data[index], green = data[index + 1], blue = data[index + 2];
        if (red - blue <= 20 || red - green <= 6) continue;
        const coverage = red - blue;
        weight += coverage; sumX += (col + .5) * coverage; sumY += (row + .5) * coverage;
      }
    }
    if (!weight) throw new Error('No reference-colored needle found.');
    return {center_x: Number((sumX / weight).toFixed(6)), center_y: Number((sumY / weight).toFixed(6))};
  }
  async function decode(url) {
    const image = new Image(); image.src = url; await image.decode();
    const canvas = document.createElement('canvas'); canvas.width = image.naturalWidth; canvas.height = image.naturalHeight;
    const context = canvas.getContext('2d'); context.drawImage(image, 0, 0);
    return {width: canvas.width, height: canvas.height, data: context.getImageData(0, 0, canvas.width, canvas.height).data};
  }
  return (async () => {
    const original = await decode(`data:image/png;base64,${reference}`);
    if (original.width !== 1625 || original.height !== 25) throw new Error('Unexpected original rotary strip geometry.');
    const frames = Array.from({length: 65}, (_, frame) => ({frame, ...marker(original.data, original.width, frame * 25, 0)}));
    if (!sheet) return {frames};
    const painted = await decode(`data:image/png;base64,${sheet}`);
    if (painted.width !== 349 || painted.height !== 133) throw new Error('Unexpected rendered control geometry.');
    const rendered = frames.map(({frame}) => ({frame,
      ...marker(painted.data, painted.width, (frame % 13) * 27, Math.floor(frame / 13) * 27)}));
    const errors = frames.map((row, index) => Math.hypot(row.center_x - rendered[index].center_x, row.center_y - rendered[index].center_y));
    return {frames, rendered, maximum_center_error_px: Math.max(...errors)};
  })();
}

const browser = await PlaywrightClient.attach();
try {
  const reference = png.toString('base64');
  const observed = await browser.page.evaluate(measurements, {reference});
  const vectors = {windows_sha256: windowsHash, png_sha256: pngHash, resource_id: 300,
    measurement: 'R-B coverage centroid at pixel centers; R-B > 20 and R-G > 6', frames: observed.frames};
  if (values['save-vectors']) await fs.writeFile(values['save-vectors'], JSON.stringify(vectors, null, 2) + '\n');
  assert.deepEqual(JSON.parse(await fs.readFile(path.join(root, 'test/data/knob-needle.json'), 'utf8')), vectors);
  const css = await fs.readFile(path.join(root, 'gui/web/style.css'), 'utf8');
  const face = (await fs.readFile(path.join(root, 'gui/web/knob-face.svg'))).toString('base64');
  assert.ok(css.includes("url('/knob-face.svg')"));
  const localCss = css.replace("url('/knob-face.svg')", `url('data:image/svg+xml;base64,${face}')`);
  const controls = observed.frames.map(({frame}) => `<span class="knob-dial" style="--angle:${knobAngle(frame, 64)}deg"><span class="knob-needle"></span></span>`).join('');
  await browser.page.setContent(`<style>${localCss} body{margin:0;padding:0;background:#272727}.sheet{display:grid;grid-template-columns:repeat(13,25px);gap:2px;width:max-content}</style><div class="sheet">${controls}</div>`);
  const sheet = (await browser.page.locator('.sheet').screenshot()).toString('base64');
  const result = await browser.page.evaluate(measurements, {reference, sheet});
  assert.ok(result.maximum_center_error_px <= .5,
    `Rendered needle exceeds half a pixel from the reference: ${result.maximum_center_error_px}`);
  console.log(`PASS 65 original/rendered needle positions: maximum centroid error ${result.maximum_center_error_px.toFixed(4)} px.`);
  console.log('Only numerical measurements and authored SVG/CSS are public. This is a Chromium rendering comparison, without hardware I/O.');
} finally { await browser.close(); }
