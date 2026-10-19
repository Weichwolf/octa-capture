// SPDX-License-Identifier: GPL-2.0-only
// Real pointer events and independent device readback against original traces.
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {until} from './BrowserClient.mjs';
import {VOLUME} from '../gui/web/parameters.js';

const traces = JSON.parse(readFileSync(new URL('data/fader-drag.json', import.meta.url), 'utf8'));
const clicks = JSON.parse(readFileSync(new URL('data/fader-click.json', import.meta.url), 'utf8'));

export async function checkFaderDrags(browser, api, restore) {
  const work = [];
  const track = request => {
    if (request.method() === 'POST' && new URL(request.url()).pathname === '/api/control') {
      work.push(request.response().then(response => response?.finished()));
    }
  };
  const settled = async () => {
    let previous;
    do {
      previous = work.length;
      await Promise.all(work);
      await browser.page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => resolve())));
    } while (work.length !== previous);
  };
  const modifiers = async (flags, action) => {
    if (flags & 4) await browser.page.keyboard[action]('Shift');
    if (flags & 8) await browser.page.keyboard[action]('Control');
  };
  browser.page.on('request', track);
  let count = 0;
  try {
    if (!await browser.evaluate('document.querySelector("#preamp-view").hidden')) await browser.click('#view-switch');
    await api('/api/control', {kind: 'master', source: 'input', bus: 0, channel: 0, parameter: 'io-link', value: 0});
    for (const [kind, source] of [['mixer', 'input'], ['mixer', 'playback'], ['master', 'input'], ['master', 'playback']]) {
      const control = {kind, source, bus: 0, channel: 0, parameter: 'volume'};
      await api('/api/control', {...control, parameter: 'link', value: 0});
      const selector = `[aria-label="${source}${kind === 'master' ? ' master' : ''} 1 volume"]`;
      const input = browser.page.locator(selector);
      const read = state => kind === 'master' ? state.mixers[0][`${source}_master`].volume[0] :
        state.mixers[0][source].channels[0].volume;
      const seed = async () => {
        await settled();
        await api('/api/control', {...control, value: VOLUME[512]});
        await until(async () => Number(await input.inputValue()) === 512, 'seeded reference fader');
        await browser.ready(selector);
      };
      const check = async (value, wire) => {
        assert.equal(Number(await input.inputValue()), value, `${kind}/${source} pointer position`);
        await settled();
        await until(async () => read((await api('/api/state')).state) === wire,
          `${kind}/${source} independent pointer wire value`);
        count++;
      };
      for (const flags of [0, 4, 8, 12]) {
        const trace = traces.find(test => test.initial === 512 && test.events[0][2] === flags);
        await seed();
        const box = await input.boundingBox(), x = box.x + box.width / 2, y = box.y + box.height / 2;
        await browser.page.mouse.move(x, y);
        await modifiers(flags, 'down');
        await browser.page.mouse.down();
        try {
          for (let index = 0; index < 9; index++) {
            const [dx, dy] = trace.events[index], expected = trace.expected[index];
            await browser.page.mouse.move(x + dx, y + dy);
            await check(expected[3], expected[4]);
          }
        } finally { await browser.page.mouse.up(); await modifiers(flags, 'up'); }
        for (const target of [0, 27, 28, 59, 60, 87]) {
          await seed();
          const expected = clicks.find(test => test.initial === 512 && test.flags === flags && test.y === target).expected;
          await modifiers(flags, 'down');
          try { await browser.page.mouse.click(x, box.y + target + 0.5); }
          finally { await modifiers(flags, 'up'); }
          await check(expected[1], expected[2]);
        }
        const previous = await input.inputValue(), requests = browser.controls.length;
        for (const key of ['Home', 'End']) await browser.key(selector, key);
        assert.equal(await input.inputValue(), previous, 'Home/End leave a reference fader unchanged');
        assert.equal(browser.controls.length, requests, 'Home/End send no fader command');
        console.log(`PASS ${kind}/${source} fader drag/track/thumb/key cases, flags ${flags}`);
      }
    }
  } finally { browser.page.off('request', track); await restore(); }
  console.log(`PASS ${count} fader pointer events with hardware readback and raw recovery`);
}
