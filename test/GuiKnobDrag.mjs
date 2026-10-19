// SPDX-License-Identifier: GPL-2.0-only
// Actual pointer input and device readback; the outer runner owns raw recovery.
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {until} from './BrowserClient.mjs';
import {VOLUME} from '../gui/web/parameters.js';

const vectors = JSON.parse(readFileSync(new URL('data/knob-drag.json', import.meta.url), 'utf8'));

export async function checkKnobDrags(browser, api, restore) {
  let count = 0;
  const work = [];
  const track = request => {
    if (request.method() === 'POST' && new URL(request.url()).pathname === '/api/control') {
      work.push(request.response().then(response => response?.finished()));
    }
  };
  browser.page.on('request', track);
  const settled = async () => {
    let previous;
    do {
      previous = work.length;
      await Promise.all(work);
      // Response headers alone precede fetch JSON processing and queued writes.
      await browser.page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => resolve())));
    } while (work.length !== previous);
  };
  try {
    for (const [kind, maximum, selector, control, read] of [
      ['sensitivity', 100, '[aria-label="Input 1 / SENS"]', {kind: 'preamp', channel: 0, parameter: 'sensitivity'}, state => state.preamps[0].sensitivity_index],
      ['compressor', 50, '[aria-label="Input 1 / GATE"]', {kind: 'preamp', channel: 0, parameter: 'gate'}, state => state.preamps[0].compressor.gate_index],
      ['pan', 200, '[aria-label="input 1 / PAN"]', {kind: 'mixer', source: 'input', bus: 0, channel: 0, parameter: 'pan'}, state => state.mixers[0].input.channels[0].pan],
      ['volume', 1024, '[aria-label="Input 1 / REVERB"]', {kind: 'mixer', source: 'input', bus: 0, channel: 0, parameter: 'send'}, state => state.reverb.input_send[0]],
    ]) {
      const preamp = kind === 'sensitivity' || kind === 'compressor';
      if (await browser.evaluate(`document.querySelector('#preamp-view').hidden === ${preamp}`)) await browser.click('#view-switch');
      for (const flags of [0, 4, 8, 12]) {
        const test = vectors.find(value => value.kind === kind && value.maximum === maximum &&
          value.initial === maximum / 2 && value.events[0][2] === flags);
        assert.ok(test, 'Original reference trace');
        const initial = kind === 'pan' ? 16384 : kind === 'volume' ? VOLUME[maximum / 2] : maximum / 2;
        await settled();
        await api('/api/control', {...control, value: initial});
        try {
          await until(() => browser.evaluate(`document.querySelector(${JSON.stringify(selector)}).getAttribute('aria-valuenow') === '${maximum / 2}'`),
            `seeded ${kind} knob position, flags ${flags}`);
        } catch (error) {
          const displayed = await browser.evaluate(`document.querySelector(${JSON.stringify(selector)}).getAttribute('aria-valuenow')`);
          throw new Error(`${error.message}; displayed ${displayed}, hardware ${read((await api('/api/state')).state)}, seed ${initial}`);
        }
        await browser.ready(selector);
        const box = await browser.page.locator(`${selector} .knob-dial`).boundingBox();
        const x = box.x + box.width / 2, y = box.y + box.height / 2;
        await browser.page.mouse.move(x, y);
        if (flags & 4) await browser.page.keyboard.down('Shift');
        if (flags & 8) await browser.page.keyboard.down('Control');
        await browser.page.mouse.down();
        try {
          for (let index = 0; index < 9; index++) {
            const [dx, dy] = test.events[index];
            const expected = test.expected[index];
            await browser.page.mouse.move(x + dx, y + dy);
            await until(() => browser.evaluate(`document.querySelector(${JSON.stringify(selector)}).getAttribute('aria-valuenow') === '${expected[3]}'`), 'reference pointer position');
            await settled();
            await until(async () => read((await api('/api/state')).state) === expected[4], 'reference pointer wire value on hardware');
            count++;
          }
        } finally {
          await browser.page.mouse.up();
          if (flags & 4) await browser.page.keyboard.up('Shift');
          if (flags & 8) await browser.page.keyboard.up('Control');
        }
        console.log(`PASS native ${kind} pointer trace, flags ${flags}`);
      }
    }
  } finally { browser.page.off('request', track); await restore(); }
  console.log(`PASS ${count} actual pointer events with independent hardware readback: sensitivity, compressor, pan and send, all modifier combinations and raw recovery`);
}
