// SPDX-License-Identifier: GPL-2.0-only
// Optional development-helper test on native Debian or WSL: reload our module.
import assert from 'node:assert/strict';
import {until} from './BrowserClient.mjs';

export async function checkReconnect(browser, api, launch, run) {
  const tracked = Array.isArray(browser.httpErrors);
  if (tracked) {
    assert.deepEqual(browser.consoleErrors, [], 'No console errors before intentional device loss');
    assert.deepEqual(browser.httpErrors, [], 'No HTTP errors before intentional device loss');
    assert.deepEqual(browser.networkErrors, [], 'No failed requests before intentional device loss');
  }
  const helper = '/usr/local/sbin/octa-capture-dev';
  const before = (await api('/api/state')).state;
  const selected = (await api('/api/devices')).devices.find(device=>device.selected);
  assert.ok(selected, 'Selected physical unit is present');
  await browser.blank();
  await api('/api/disconnect', {});
  let removed = false;
  try {
    await run('sudo', ['-n', helper, 'unload'], {timeout: 15000});
    removed = true;
    await browser.call('Page.navigate', {url: launch});
    await until(() => browser.evaluate(`document.querySelector('#connection-message')?.textContent === 'The device could not be found.' && !document.querySelector('#connection-message').hidden`), 'missing ALSA card shown in browser');
    assert.equal((await api('/api/devices')).devices.length, 0, 'Module removal removes its ALSA card');
    await browser.key('#device-status', 'ContextMenu');
    await until(() => browser.evaluate(`document.querySelector('#menus button')?.textContent === 'The device could not be found.'`), 'empty device menu');
    // An empty menu has no enabled item; Escape still restores its anchor.
    await browser.call('Input.dispatchKeyEvent', {type: 'keyDown', key: 'Escape', windowsVirtualKeyCode: 27});
    await browser.call('Input.dispatchKeyEvent', {type: 'keyUp', key: 'Escape', windowsVirtualKeyCode: 27});
  } finally {
    if (removed) await run('sudo', ['-n', helper, 'install'], {timeout: 30000});
  }
  await until(() => browser.evaluate('document.querySelector("#connection-message")?.hidden === true'), 'automatic GUI reconnection after module reload', 20000);
  const expected = {httpErrors: [], consoleErrors: []};
  if (tracked) {
    // State polling reports deliberate device absence as HTTP 503. Preserve all
    // records and require exact endpoint/status/console correspondence.
    assert.ok(browser.httpErrors.length > 0, 'Actual absent-device state requests were observed');
    for (const error of browser.httpErrors) assert.deepEqual(error, {path: '/api/state', status: 503});
    assert.equal(browser.consoleEvents.length, browser.httpErrors.length, 'One resource error per absent-device reply');
    for (const event of browser.consoleEvents) {
      assert.equal(event.path, '/api/state');
      assert.equal(event.text, 'Failed to load resource: the server responded with a status of 503 (Service Unavailable)');
    }
    expected.httpErrors = browser.httpErrors.slice();
    expected.consoleErrors = browser.consoleErrors.slice();
  }
  const devices = (await api('/api/devices')).devices;
  assert.ok(devices.some(device=>device.id === selected.id && device.selected), 'Selected serial identity is retained');
  assert.deepEqual((await api('/api/state')).state, before, 'Module reload preserves complete raw audio state');
  await until(async () => {
    const meters = (await api('/api/meters')).meters;
    return meters?.age_ms < 1000 && meters.pcm?.capture.fresh && meters.pcm.playback.fresh;
  }, 'actual MIDI/PCM meters after reconnection', 20000);
  if (tracked) {
    assert.deepEqual(browser.httpErrors, expected.httpErrors, 'No HTTP errors after reconnection');
    assert.deepEqual(browser.consoleErrors, expected.consoleErrors, 'No console errors after reconnection');
    assert.deepEqual(browser.networkErrors, [], 'No failed requests during module lifecycle');
  }
  console.log('PASS actual module unload/reload: absent-card GUI/menu, automatic same-unit reconnection, meters and raw preservation');
  return expected;
}
