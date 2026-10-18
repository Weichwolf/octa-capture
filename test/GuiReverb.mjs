// SPDX-License-Identifier: GPL-2.0-only
// Real pointer/keyboard writes to every reverb bank, with complete effect recovery.
import assert from 'node:assert/strict';
import {pause, until} from './BrowserClient.mjs';
import {preDelayText, reverbTimeText} from '../gui/web/parameters.js';

export async function checkReverb(browser, api) {
  const before = (await api('/api/state')).state;
  const wasPreamp = !await browser.evaluate('document.querySelector("#preamp-view").hidden');
  if (wasPreamp) await browser.click('#view-switch');
  await browser.click('#mixer-tabs > button:first-of-type');
  await browser.click('.master-reverb-type');
  assert.equal(await browser.evaluate('document.querySelector("#reverb-delay").readOnly && document.querySelector("#reverb-time").readOnly'), true);
  const waitValue = async (field, bank, value, selector, format) => {
    await until(async () => (await api('/api/state')).state.reverb[field][bank] === value, 'reverb hardware bank readback');
    await until(() => browser.evaluate(`document.querySelector(${JSON.stringify(selector)}).value === ${JSON.stringify(format(value))}`), 'reference reverb value shown');
  };
  try {
    for (let bank = 1; bank <= 4; bank++) {
      await browser.key('#reverb-type', 'Home');
      for (let step = 0; step < bank; step++) await browser.key('#reverb-type', 'ArrowDown');
      await browser.key('#reverb-type', 'Enter');
      await until(async () => (await api('/api/state')).state.reverb.type === bank, 'reverb type hardware readback');
      for (const [parameter, field, selector, format, maximum, suffix] of [
        ['pre-delay', 'pre_delay_indices', '#reverb-delay', preDelayText, 12, 'pre-delay'],
        ['time', 'time_indices', '#reverb-time', reverbTimeText, 49, 'time']]) {
        const original = (await api('/api/state')).state.reverb[field][bank];
        await waitValue(field, bank, original, selector, format);
        const start = browser.controls.length, target = original ? original - 1 : 1;
        await browser.key(selector, original ? 'ArrowDown' : 'ArrowUp');
        await until(() => browser.controls.slice(start).some(body => body.kind === 'reverb' &&
          body.parameter === parameter && body.bank === bank && body.value === target && body.httpStatus === 200),
        'reverb step sends the correct bank and wire index');
        await waitValue(field, bank, target, selector, format);
        await browser.click(`[aria-label="${original ? 'Increase' : 'Decrease'} reverb ${suffix}"]`);
        await waitValue(field, bank, original, selector, format);
        if (bank === 1) {
          for (const [value, key] of [[0, 'ArrowDown'], [maximum, 'ArrowUp']]) {
            await api('/api/control', {kind: 'reverb', parameter, bank, value});
            await waitValue(field, bank, value, selector, format);
            const count = browser.controls.length;
            await browser.key(selector, key); await pause(150);
            assert.equal(browser.controls.length, count, 'Reverb endpoint does not wrap or send a redundant write');
          }
        }
      }
    }
    const old = await browser.evaluate('document.querySelector("#reverb-time").value');
    await browser.key('#reverb-time', 'F2');
    await browser.call('Input.insertText', {text: '3.4'});
    assert.equal(await browser.evaluate('document.querySelector("#reverb-time").value'), old, 'Read-only reference text rejects typing');
  } finally {
    await api('/api/control', {kind: 'reverb', parameter: 'type', bank: 0, value: before.reverb.type});
    for (let bank = 1; bank <= 4; bank++) {
      await api('/api/control', {kind: 'reverb', parameter: 'pre-delay', bank, value: before.reverb.pre_delay_indices[bank]});
      await api('/api/control', {kind: 'reverb', parameter: 'time', bank, value: before.reverb.time_indices[bank]});
    }
    await browser.click('#reverb [data-close]');
    if (wasPreamp) await browser.click('#view-switch');
  }
  assert.deepEqual((await api('/api/state')).state, before, 'Complete reverb test state restored');
  console.log('PASS real reverb type banks, read-only text, pointer/keyboard steps, endpoint bounds and complete restoration');
}
