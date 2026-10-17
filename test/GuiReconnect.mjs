// SPDX-License-Identifier: GPL-2.0-only
// Optional WSL development-helper test: remove/reload only our ALSA module.
import assert from 'node:assert/strict';
import {until} from './BrowserClient.mjs';

export async function checkReconnect(browser, api, launch, run) {
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
  const devices = (await api('/api/devices')).devices;
  assert.ok(devices.some(device=>device.id === selected.id && device.selected), 'Selected serial identity is retained');
  assert.deepEqual((await api('/api/state')).state, before, 'Module reload preserves complete raw audio state');
  await until(async () => {
    const meters = (await api('/api/meters')).meters;
    return meters?.age_ms < 1000 && meters.pcm?.capture.fresh && meters.pcm.playback.fresh;
  }, 'actual MIDI/PCM meters after reconnection', 20000);
  console.log('PASS actual module unload/reload: absent-card GUI/menu, automatic same-unit reconnection, meters and raw preservation');
}
