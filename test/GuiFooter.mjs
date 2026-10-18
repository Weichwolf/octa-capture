// SPDX-License-Identifier: GPL-2.0-only
// Connected reference footer, real rate changes and unavailable-status display.
import assert from 'node:assert/strict';
import {until} from './BrowserClient.mjs';

export async function checkFooter(browser, api) {
  const document = await api('/api/state');
  const rate = [44100, 48000, 96000, 192000][parseInt(document.state.payload_hex.slice(4, 6), 16)];
  await until(() => browser.evaluate(`Number(document.querySelector('#footer-rate').value) === ${rate}`), 'footer rate readback');
  assert.deepEqual(await browser.evaluate(`[...document.querySelectorAll('#footer-rate option:not(:disabled)')].map(node=>Number(node.value))`), [44100, 48000, 96000, 192000]);
  assert.equal(await browser.evaluate('document.querySelector("#device-label").textContent'), 'OCTA-CAPTURE');
  assert.equal(await browser.evaluate('document.querySelector("#footer-clock").textContent === document.querySelector("#settings-clock").textContent'), true);
  await browser.call('Emulation.setDeviceMetricsOverride', {width: 978, height: 429, deviceScaleFactor: 1, mobile: false});
  assert.equal(await browser.evaluate(`(() => {
    const panel=document.querySelector('#panel').getBoundingClientRect();
    const bar=document.querySelector('.device-bar').getBoundingClientRect();
    return panel.width===978 && panel.height===429 && panel.top===0 && panel.bottom<=innerHeight &&
      bar.bottom<=panel.bottom && bar.top>=document.querySelector('main').getBoundingClientRect().bottom &&
      ['#device-status','#footer-rate'].every(selector=>{
        const node=document.querySelector(selector),box=node.getBoundingClientRect();
        return box.height>0 && node.contains(document.elementFromPoint(box.x+box.width/2,box.y+box.height/2));
      });
  })()`), true, 'Full panel and footer controls fit and receive pointer input at the native window size');
  await browser.call('Emulation.setDeviceMetricsOverride', {width: 1100, height: 650, deviceScaleFactor: 1, mobile: false});
  if (browser.page) {
    const originalClock = await browser.evaluate('document.querySelector("#footer-clock").textContent');
    const unavailable = async route => {
      const response = await route.fetch();
      const original = await response.json();
      await route.fulfill({response, json: {...original, status: null}});
    };
    await browser.page.route('**/api/state', unavailable);
    try {
      await until(() => browser.evaluate('document.querySelector("#footer-clock").textContent === "UNKNOWN" && document.querySelector("#settings-clock").textContent === "UNKNOWN"'), 'missing status must not claim an internal clock');
    } finally { await browser.page.unroute('**/api/state', unavailable); }
    await until(() => browser.evaluate(`document.querySelector('#footer-clock').textContent === ${JSON.stringify(originalClock)}`), 'actual clock status restored after response simulation');
  }
  console.log('PASS connected footer geometry, device access, synchronized rate/clock fields and missing-status response simulation');
}

export async function checkFooterRates(browser, api) {
  const before = (await api('/api/state')).state;
  const rates = [44100, 48000, 96000, 192000];
  const originalRate = rates[parseInt(before.payload_hex.slice(4, 6), 16)];
  const preferences = await api('/api/preferences');
  try {
    // Warning behavior is checked separately by GuiRates; retain the user's preference.
    await api('/api/preferences', {show_port_warning: false});
    if (browser.page) {
      let release;
      const held = new Promise(resolve => { release = resolve; });
      let finish;
      const continued = new Promise(resolve => { finish = resolve; });
      let observed = false;
      const pause = async route => {
        if (route.request().postDataJSON()?.action === 'sample-rate') { observed = true; await held; }
        try { await route.continue(); } finally { finish(); }
      };
      await browser.page.route('**/api/action', pause);
      try {
        await browser.page.locator('#footer-rate').selectOption(String(originalRate === 44100 ? 48000 : 44100));
        await until(() => observed, 'actual footer rate control request');
        assert.equal(await browser.evaluate(`[...document.querySelectorAll('[data-rate-select]')].every(node=>node.disabled) && !document.querySelector('#busy-message').hidden`), true, 'Both rate controls are disabled while the operation is pending');
      } finally {
        release();
        if (observed) await continued;
        await browser.page.unroute('**/api/action', pause);
      }
      await until(() => browser.evaluate('document.querySelector("#busy-message").hidden && !document.querySelector("#footer-rate").disabled'), 'held footer operation completes', 20000);
    }
    for (const [index, rate] of rates.entries()) {
      await browser.key('#footer-rate', 'Home');
      for (let step = 0; step < index; step++) await browser.key('#footer-rate', 'ArrowDown');
      await browser.key('#footer-rate', 'Enter');
      await until(async () => {
        const current = await api('/api/state');
        return !current.busy && parseInt(current.state.payload_hex.slice(4, 6), 16) === index &&
          current.status?.sample_rate_hz === rate;
      }, `${rate} Hz footer request agrees with kernel and DSP`, 20000);
      await until(() => browser.evaluate(`[...document.querySelectorAll('[data-rate-select]')].every(node=>Number(node.value)===${rate} && !node.disabled)`), 'both rate controls agree after completion');
      assert.equal(await browser.evaluate('document.querySelector("#toast").hidden'), true, 'No footer rate error');
      assert.equal(await browser.evaluate('document.querySelector("#settings").open'), false, 'Footer changes need no settings dialog');
      await until(async () => {
        const pcm = (await api('/api/meters')).meters?.pcm;
        return pcm?.capture.fresh && pcm.playback.fresh && pcm.capture.rate === rate && pcm.playback.rate === rate;
      }, `${rate} Hz footer PCM meter recovery`, 20000);
    }
  } finally {
    try { await api('/api/action', {action: 'sample-rate', rate: originalRate}); }
    finally { await api('/api/preferences', preferences); }
  }
  await until(() => browser.evaluate(`Number(document.querySelector('#footer-rate').value) === ${originalRate}`), 'footer original rate restored');
  assert.deepEqual((await api('/api/state')).state, before, 'Footer rate checks preserve complete device state');
  console.log('PASS all four footer rate selections, synchronized settings, kernel/DSP readback, actual PCM meters and recovery');
}
