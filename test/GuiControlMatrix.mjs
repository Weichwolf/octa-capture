// SPDX-License-Identifier: GPL-2.0-only
// Optional real-browser/device control coverage; the outer runner owns raw recovery.
import assert from 'node:assert/strict';
import {until} from './BrowserClient.mjs';
import {VOLUME, volumeIndex, panToWire, wireToPan} from '../gui/web/parameters.js';

export async function checkControlMatrix(browser, api) {
  let count = 0;
  const fresh = async () => (await api('/api/state')).state;
  const acknowledged = async (start, control, read, expected) => {
    await until(() => browser.controls.slice(start).some(body => body.httpStatus === 200 &&
      Object.entries(control).every(([name, value])=>body[name] === value) && body.value === expected),
    `acknowledged browser control ${JSON.stringify(control)}`);
    await until(async () => Number(read(await fresh())) === expected, 'independent target readback');
    count++;
  };
  const knob = async (selector, control, read, encode = value => value, step = 1, decode = value => value) => {
    const original = Number(read(await fresh()));
    const position = decode(original);
    await until(async () => Number(await browser.evaluate(`document.querySelector(${JSON.stringify(selector)}).getAttribute('aria-valuenow')`)) === position,
      'knob display agrees with hardware');
    const target = position > 0 ? Math.max(0, position - step) : step;
    const start = browser.controls.length;
    await browser.key(selector, position > 0 ? 'ArrowDown' : 'ArrowUp');
    try { await acknowledged(start, control, read, encode(target)); }
    finally { await api('/api/control', {...control, value: original}); }
  };
  const toggle = async (selector, control, read, inverted = false) => {
    const original = Number(read(await fresh()));
    await until(async () => await browser.evaluate(`document.querySelector(${JSON.stringify(selector)}).getAttribute('aria-pressed')`) === String(inverted ? !original : Boolean(original)),
      'switch display agrees with hardware');
    const start = browser.controls.length;
    await browser.click(selector);
    try { await acknowledged(start, control, read, Number(!original)); }
    finally { await api('/api/control', {...control, value: original}); }
  };
  const fader = async (selector, control, read) => {
    const original = Number(read(await fresh()));
    const position = volumeIndex(original);
    await until(async () => Number(await browser.evaluate(`document.querySelector(${JSON.stringify(selector)}).value`)) === position,
      'fader display agrees with hardware');
    const start = browser.controls.length;
    await browser.key(selector, position > 0 ? 'ArrowDown' : 'ArrowUp');
    try { await acknowledged(start, control, read, VOLUME[position > 0 ? position - 1 : 1]); }
    finally { await api('/api/control', {...control, value: original}); }
  };
  const rate = parseInt((await fresh()).payload_hex.slice(4, 6), 16);
  assert.notEqual(rate, 3, 'Full matrix requires a rate below 192 kHz.');
  if (await browser.evaluate('document.querySelector("#preamp-view").hidden')) await browser.click('#view-switch');
  for (let channel = 0; channel < 8; channel++) {
    for (const parameter of ['sensitivity', 'gate', 'threshold', 'gain', 'attack', 'release', 'ratio']) {
      const read = state => parameter === 'sensitivity' ? state.preamps[channel].sensitivity_index :
        state.preamps[channel].compressor[`${parameter}_index`];
      const selector = parameter === 'sensitivity' ? `.preamp-strip[data-channel="${channel}"] .sensitivity` :
        `[aria-label="Input ${channel + 1} / ${parameter.toUpperCase()}"]`;
      await knob(selector, {kind: 'preamp', channel, parameter}, read);
    }
    for (const [parameter, label, field] of [['low-cut', 'LO-CUT', 'low_cut'], ['phase', 'PHASE', 'phase_inverted']]) {
      await toggle(`[aria-label="Input ${channel + 1} ${label}"]`, {kind: 'preamp', channel, parameter},
        state => state.preamps[channel][field]);
    }
    await toggle(`[aria-label="Input ${channel + 1} compressor bypass"]`, {kind: 'preamp', channel, parameter: 'compressor'},
      state => state.preamps[channel].compressor.enabled, true);
    console.log(`PASS browser control matrix: all safe preamp controls, input ${channel + 1}`);
  }
  await browser.click('#view-switch');
  const originalRoutes = [...(await fresh()).output_routes];
  try {
    if (!originalRoutes.some(value => value >= 1 && value <= 3)) {
      await browser.click('#header-action'); await browser.click('#confirm-yes');
      await until(async () => (await fresh()).output_routes.some(value => value >= 1 && value <= 3), 'four mixer buses enabled');
    }
    for (let bus = 0; bus < 4; bus++) {
      await browser.click(`#mixer-tabs > button:nth-of-type(${bus + 1})`);
      await until(() => browser.evaluate(`document.querySelector('#mixer-tabs > button:nth-of-type(${bus + 1})').getAttribute('aria-selected') === 'true'`), 'selected matrix mixer bus');
      for (const source of ['input', 'playback']) {
        for (let channel = 0; channel < 10; channel++) {
          const control = {kind: 'mixer', source, bus, channel};
          const read = parameter => state => state.mixers[bus][source].channels[channel][parameter];
          await knob(`[aria-label="${source} ${channel + 1} / PAN"]`, {...control, parameter: 'pan'},
            read('pan'), panToWire, 1, wireToPan);
          await fader(`[aria-label="${source} ${channel + 1} volume"]`, {...control, parameter: 'volume'}, read('volume'));
          for (const parameter of ['mute', 'solo']) {
            await toggle(`.mixer-strip[data-source="${source}"][data-channel="${channel}"] .mixer-switches .toggle:nth-of-type(${parameter === 'mute' ? 1 : 2})`,
              {...control, parameter}, read(parameter));
          }
        }
        for (let channel = 0; channel < 2; channel++) {
          await fader(`[aria-label="${source} master ${channel + 1} volume"]`, {kind: 'master', source, bus, channel, parameter: 'volume'},
            state => state.mixers[bus][`${source}_master`].volume[channel]);
        }
      }
      if (bus === 0) {
        for (let channel = 0; channel < 10; channel++) {
          await knob(`[aria-label="Input ${channel + 1} / REVERB"]`, {kind: 'mixer', source: 'input', bus, channel, parameter: 'send'},
            state => state.reverb.input_send[channel], value => VOLUME[value], 1, volumeIndex);
        }
        await knob('[aria-label="REVERB RETURN"]', {kind: 'master', source: 'input', bus, channel: 0, parameter: 'return'},
          state => state.reverb.master_return, value => VOLUME[value], 1, volumeIndex);
      }
      console.log(`PASS browser control matrix: all strip/master faders, pan, mute/solo and available sends, bus ${'ABCD'[bus]}`);
    }
  } finally {
    await browser.click('#mixer-tabs > button:first-of-type');
    for (let pair = 0; pair < originalRoutes.length; pair++) await api('/api/control', {kind: 'route', pair, selection: originalRoutes[pair]});
    await api('/api/control', {kind: 'display', bus: 0});
  }
  console.log(`PASS ${count} real browser control changes with hardware readback and exact-value restoration; power and persistent memory excluded`);
}
