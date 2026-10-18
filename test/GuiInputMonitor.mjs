// SPDX-License-Identifier: GPL-2.0-only
// Exercise an external monitoring update through the real browser and device API.
import assert from 'node:assert/strict';
import {until} from './BrowserClient.mjs';

export async function checkInputMonitor(browser, api, restore) {
  const original = (await api('/api/state')).state;
  const silentBus = original.payload_hex.slice(4, 6) === '03' ? 0 : 1;
  try {
    for (const source of ['input', 'playback']) {
      for (const channel of [0, 1]) {
        await api('/api/control', {kind: 'master', source, bus: silentBus, channel, parameter: 'volume', value: 0});
      }
    }
    for (let pair = 0; pair < 5; pair++) await api('/api/control', {kind: 'route', pair, selection: silentBus});
    await api('/api/control', {kind: 'mixer', source: 'input', bus: 0, channel: 0, parameter: 'link', value: 0});
    await browser.evaluate(`(() => {
      if (document.querySelector('#mixer-view').hidden) document.querySelector('#view-switch').click();
      document.querySelector('#mixer-tabs > button:first-of-type').click();
    })()`);
    const before = (await api('/api/state')).state;
    assert.deepEqual(before.output_routes, Array(5).fill(silentBus));
    for (const source of ['input', 'playback']) assert.deepEqual(before.mixers[silentBus][`${source}_master`].volume, [0, 0]);
    const pan = 0x2345, volume = 0x123456;
    for (const enabled of [false, true]) {
      const control = {kind: 'monitor', channel: 0, enabled, pan, volume};
      const result = await browser.evaluate(`(async () => {
        const response = await fetch('/api/control', {method:'POST',
          headers:{Authorization:'Bearer '+sessionStorage.getItem('octa-token'), 'Content-Type':'application/json'},
          body:JSON.stringify(${JSON.stringify(control)})});
        return {status:response.status, document:await response.json()};
      })()`);
      assert.equal(result.status, 200);
      const actual = result.document.state.mixers[0].input.channels[0];
      assert.deepEqual({mute: actual.mute, pan: actual.pan, volume: actual.volume}, {mute: !enabled, pan, volume});
      await until(() => browser.evaluate(`(async () => {
        const {volumeIndex, volumeText, wireToPan} = await import('/parameters.js');
        const strip = document.querySelector('.mixer-strip[data-source=input][data-channel="0"]');
        const fader = strip.querySelector('input[type=range]');
        return fader.value === String(volumeIndex(${volume})) && fader.title === volumeText(${volume}) &&
          strip.querySelector('[aria-label="input 1 / PAN"]').getAttribute('aria-valuenow') === String(wireToPan(${pan})) &&
          strip.querySelector('[aria-label=MUTE]').getAttribute('aria-pressed') === '${!enabled}';
      })()`), 'composite monitor values appear in the actual GUI');
    }
  } finally { await restore(); }
  assert.deepEqual((await api('/api/state')).state, original, 'Complete state preserved after monitor GUI check');
  console.log('PASS browser-originated composite monitoring, GUI fader/pan/mute readback and raw restoration');
}
