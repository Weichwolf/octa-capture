// SPDX-License-Identifier: GPL-2.0-only
// Actual Playwright input with a delayed control request and independent readback.
import assert from 'node:assert/strict';
import {until} from './BrowserClient.mjs';
import {VOLUME, volumeIndex} from '../gui/web/parameters.js';

const names = ['input master 1 volume', 'input master 2 volume',
  'playback master 1 volume', 'playback master 2 volume'];
const control = (index, parameter, value) => ({kind: 'master', bus: 0,
  source: index < 2 ? 'input' : 'playback', channel: index % 2, parameter, value});
const volumes = state => [...state.mixers[0].input_master.volume, ...state.mixers[0].playback_master.volume];

export async function checkMasterFaders(browser, api, restore) {
  const read = async () => (await api('/api/state')).state;
  const displayed = () => browser.page.locator('.master-faders input').evaluateAll(inputs =>
    inputs.map(input => ({position: Number(input.value), text: input.nextElementSibling.textContent})));
  const base = volumeIndex(256);
  // Explicit expected peers include the device's asymmetric, one-way expansion.
  const cases = [
    {input: false, playback: false, io: false, origin: 0, peers: [0]},
    {input: true, playback: false, io: false, origin: 0, peers: [0, 1]},
    {input: true, playback: true, io: true, origin: 0, peers: [0, 1, 2, 3]},
    {input: false, playback: true, io: true, origin: 0, peers: [0, 2, 3]}
  ];
  try {
    for (const settings of cases) {
      await api('/api/control', control(0, 'io-link', 0));
      for (const index of [0, 2]) await api('/api/control', control(index, 'link', 0));
      for (let index = 0; index < 4; index++) await api('/api/control', control(index, 'volume', VOLUME[base]));
      await api('/api/control', control(0, 'link', Number(settings.input)));
      await api('/api/control', control(2, 'link', Number(settings.playback)));
      await api('/api/control', control(0, 'io-link', Number(settings.io)));
      await until(async () => (await displayed()).every(value => value.position === base) &&
        await browser.evaluate(`document.querySelector('.master-io-link').getAttribute('aria-pressed') === '${settings.io}' &&
          [...document.querySelectorAll('.master-group > .toggle')].map(node=>node.getAttribute('aria-pressed')).join() === '${settings.input},${settings.playback}'`),
      'master values and all independent link states synchronized');
      let release, arrived, continued;
      const held = new Promise(resolve => { release = resolve; });
      const intercepted = new Promise(resolve => { arrived = resolve; });
      const resumed = new Promise(resolve => { continued = resolve; });
      let first = true;
      const route = async request => {
        if (first && request.request().postDataJSON()?.kind === 'master') {
          first = false; arrived(); await held;
          try { await request.continue(); } finally { continued(); }
          return;
        }
        await request.continue();
      };
      await browser.page.route('**/api/control', route);
      const start = browser.controls.length;
      try {
        const selector = `[aria-label="${names[settings.origin]}"]`;
        for (let step = 1; step <= 3; step++) {
          await browser.key(selector, 'ArrowUp');
          await intercepted;
          await browser.page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => resolve())));
          const actual = await displayed();
          assert.deepEqual(actual.map(value => value.position), names.map((_, index) =>
            settings.peers.includes(index) ? base + step : base), 'linked thumbs move together before any hardware reply');
          assert.equal(new Set(settings.peers.map(index => actual[index].text)).size, 1,
            'linked numeric readouts move together');
        }
        // State polls still report the old hardware value while this request is held.
        await new Promise(resolve => setTimeout(resolve, 1100));
        assert.deepEqual(volumes(await read()), Array(4).fill(VOLUME[base]));
        assert.deepEqual((await displayed()).map(value => value.position), names.map((_, index) =>
          settings.peers.includes(index) ? base + 3 : base), 'polling cannot overwrite the pending preview');
      } finally {
        release();
        if (!first) await resumed;
        await browser.page.unroute('**/api/control', route);
      }
      const expected = names.map((_, index) => VOLUME[settings.peers.includes(index) ? base + 3 : base]);
      await until(async () => JSON.stringify(volumes(await read())) === JSON.stringify(expected), 'latest queued master value reaches hardware');
      await until(() => browser.controls.slice(start).filter(change => change.httpStatus === 200).length === 2,
        'first and coalesced latest master writes acknowledged');
      assert.equal(browser.controls.length - start, 2, 'peer movement sends no redundant control requests');
    }
  } finally { await restore(); }
  console.log('PASS Playwright master faders: immediate linked thumbs/readouts, delayed replies, poll stability, latest queued value and hardware coupling');
}
