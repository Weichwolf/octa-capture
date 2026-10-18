// SPDX-License-Identifier: GPL-2.0-only
// Optional real-device rate transitions through the browser's settings dialog.
import assert from 'node:assert/strict';
import path from 'node:path';
import {until} from './BrowserClient.mjs';

export async function checkRates(browser, api, directory, originalRate) {
  const rates = [44100, 48000, 96000, 192000];
  const preferences = await api('/api/preferences');
  const change = async rate => {
    const index = rates.indexOf(rate);
    await browser.click('[data-menu="driver"]');
    await browser.click('#menus button:first-of-type');
    await until(() => browser.evaluate('document.querySelector("#settings").open'), 'driver settings');
    await browser.key('#sample-rate', 'Home');
    for (let step = 0; step < index; step++) await browser.key('#sample-rate', 'ArrowDown');
    await browser.key('#sample-rate', 'Enter');
    assert.equal(await browser.evaluate('Number(document.querySelector("#sample-rate").value)'), rate);
    await until(async () => {
      const document = await api('/api/state');
      return !document.busy && parseInt(document.state.payload_hex.slice(4, 6), 16) === index &&
        document.status?.sample_rate_hz === rate;
    }, `${rate} Hz bulk and kernel status`, 20000);
    await until(() => browser.evaluate(`document.querySelector('#device-status').textContent.includes('${rate / 1000} kHz') && document.querySelector('#busy-message').hidden`), 'rate shown in browser');
    assert.equal(await browser.evaluate('document.querySelector("#toast").hidden'), true, 'No rate change error');
    assert.equal(await browser.evaluate('document.querySelector("#settings-channels").textContent'),
      rate === 192000 ? '4 in / 4 out' : '12 in / 10 out');
    await browser.key('#settings [data-close]', 'Escape');
    assert.equal(await browser.evaluate('document.querySelector("#settings").open'), false, 'Escape closes Linux audio settings');
    assert.equal((await api('/api/state')).status.sample_rate_hz, rate, 'Closing keeps the immediate rate change');
  };
  try {
    await api('/api/preferences', {show_port_warning: true});
    await until(() => browser.evaluate('document.querySelector("#show-port-warning").checked && !document.querySelector("#show-port-warning").disabled'), 'warning preference synchronized');
    for (const rate of rates) {
      await change(rate);
      const high = rate === 192000;
      if (high) {
        await until(() => browser.evaluate('document.querySelector("#port-warning").open'), 'original 192 kHz audio-port warning');
        assert.match(await browser.evaluate('document.querySelector("#port-warning .dialog-content").textContent'), /inputs 1–4 and outputs 1–4/);
        await browser.screenshot(path.join(directory, 'port-warning.png'));
        await browser.click('#hide-port-warning');
        await until(async () => !(await api('/api/preferences')).show_port_warning, 'warning suppression stored');
        await browser.click('#port-warning [data-close]');
        await browser.click('[data-menu="driver"]');
        await browser.click('#menus button:nth-of-type(4)');
        assert.equal(await browser.evaluate('document.querySelector("#show-port-warning").checked'), false);
        await browser.click('#show-port-warning');
        await until(async () => (await api('/api/preferences')).show_port_warning, 'About warning checkbox stored');
        await browser.click('#about [data-close]');
        assert.equal(await browser.evaluate('document.querySelector("#port-warning").open'), false, 'Reenabling does not repeat the warning at the same rate');
      }
      const expectedChannels = high ? 4 : 8;
      await until(() => browser.evaluate(`document.querySelectorAll('.preamp-strip:not(.unavailable-channel)').length === ${expectedChannels}`), 'preamp channel availability');
      // Inspect both views regardless of the caller's selected view.
      if (await browser.evaluate('document.querySelector("#preamp-view").hidden')) await browser.click('#view-switch');
      await browser.click('#header-action');
      assert.equal(await browser.evaluate('document.querySelectorAll("#auto-channels input:not(:disabled)").length'), expectedChannels);
      await browser.click('#auto-cancel');
      await browser.click('#view-switch');
      assert.equal(await browser.evaluate('document.querySelectorAll(".mixer-strip:not(.unavailable-channel)").length'), high ? 8 : 20);
      assert.equal(await browser.evaluate('document.querySelector("#header-action").disabled'), high);
      assert.equal(await browser.evaluate('document.querySelector("#header-action").style.visibility'), high ? 'hidden' : 'visible');
      assert.equal(await browser.evaluate('document.querySelector(".mixer-send").style.visibility'), high ? 'hidden' : 'visible');
      assert.equal(await browser.evaluate('document.querySelector(".master-reverb-type").style.visibility'), high ? 'hidden' : 'visible');
      if (high) assert.equal(await browser.evaluate('document.querySelectorAll("#mixer-tabs > button:not([hidden])").length'), 1);
      await browser.click('[data-menu="device"]');
      assert.deepEqual(await browser.evaluate(`[...document.querySelectorAll('#menus button')].filter(node=>node.textContent.startsWith('Open the Patch Bay') || node.textContent === 'Directly Output the Inputs').map(node=>node.disabled)`), [high, high]);
      const initialize = '#menus button[aria-haspopup="menu"]';
      await browser.key(initialize, 'ArrowRight');
      assert.deepEqual(await browser.evaluate(`[...document.querySelectorAll('#menus .menu[data-depth="1"] button')].map(node=>node.disabled)`), [false, false, false, false, false]);
      await browser.key('#menus .menu[data-depth="1"] button:first-of-type', 'Escape');
      await browser.key(initialize, 'Escape');
      for (const dialog of ['reverb', 'patchbay']) {
        await browser.evaluate(`document.querySelector('#${dialog}').showModal()`);
        assert.equal(await browser.evaluate(`[...document.querySelectorAll('#${dialog} select')].every(node=>node.disabled)`), high);
        await browser.click(`#${dialog} [data-close]`);
      }
      await until(async () => {
        const pcm = (await api('/api/meters')).meters?.pcm;
        return pcm?.capture.fresh && pcm.playback.fresh && pcm.capture.rate === rate && pcm.playback.rate === rate &&
          pcm.capture.channels === (high ? 4 : 12) && pcm.playback.channels === (high ? 4 : 10);
      }, `${rate} Hz actual PCM meter recovery`, 20000);
      await browser.screenshot(path.join(directory, `mixer-${rate}.png`));
      console.log(`PASS browser ${rate} Hz: Linux audio information, immediate selection, Close/Escape, kernel/bulk state, channel/dialog gating and real PCM meters`);
    }
  } finally {
    try {
      await api('/api/action', {action: 'sample-rate', rate: originalRate});
      await until(async () => parseInt((await api('/api/state')).state.payload_hex.slice(4, 6), 16) === rates.indexOf(originalRate), 'original sample rate restored');
    } finally { await api('/api/preferences', preferences); }
  }
}
