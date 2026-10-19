// SPDX-License-Identifier: GPL-2.0-only
// Browser/hardware integration. --write changes safe controls with raw recovery.
import assert from 'node:assert/strict';
import {Buffer} from 'node:buffer';
import fs from 'node:fs/promises';
import path from 'node:path';
import process from 'node:process';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
import {BrowserClient, until} from './BrowserClient.mjs';
import {checkRates} from './GuiRates.mjs';
import {checkReconnect} from './GuiReconnect.mjs';
import {checkControlMatrix} from './GuiControlMatrix.mjs';
import {checkReverb} from './GuiReverb.mjs';
import {checkMasterFaders} from './GuiMasterFaders.mjs';
import {checkKnobDrags} from './GuiKnobDrag.mjs';
import {checkFaderDrags} from './GuiFaderDrag.mjs';
import {checkWheels, checkWheelDrags} from './GuiWheelBrowser.mjs';
import {checkInputMonitor} from './GuiInputMonitor.mjs';
import {checkFooter, checkFooterRates} from './GuiFooter.mjs';
import {checkAutoSenseSelection, checkAutoSenseFailure} from './GuiAutoSense.mjs';
import {checkDialogWindows} from './GuiDialogs.mjs';
import {checkControlHits} from './GuiControlHits.mjs';
import {checkControlGeometry} from './GuiControlGeometry.mjs';
import {checkChannelFaders} from './GuiChannelFaders.mjs';

const run = promisify(execFile);
const root = path.resolve(path.dirname(new URL(import.meta.url).pathname), '..');
const logFile = path.resolve(process.argv[2] || 'build/gui/server.log');
const portFile = path.resolve(process.argv[3] || 'build/gui/browser-profile/DevToolsActivePort');
const write = process.argv.includes('--write');
const rates = process.argv.includes('--rates');
const reload = process.argv.includes('--reload-module');
const matrix = process.argv.includes('--matrix');
if ((rates || reload || matrix) && !write) throw new Error('--rates/--reload-module/--matrix require --write for recovery.');
const launch = (await fs.readFile(logFile, 'utf8')).match(/http:\/\/127\.0\.0\.1:[0-9]+\/#token=[^\s]+/)?.[0];
if (!launch) throw new Error('No private GUI launch link in the server log.');
const url = new URL(launch), token = new URLSearchParams(url.hash.slice(1)).get('token');

async function api(endpoint, body) {
  const headers = {Authorization: `Bearer ${token}`};
  if (body !== undefined) { headers['Content-Type'] = 'application/json'; headers.Origin = url.origin; }
  const response = await fetch(url.origin + endpoint, {method: body === undefined ? 'GET' : 'POST',
    headers, body: body === undefined ? undefined : JSON.stringify(body)});
  if (!response.ok) throw new Error(`HTTP ${response.status}: ${endpoint}`);
  return response.json();
}

const playwright = process.argv.includes('--playwright');
const Client = playwright ? (await import('./PlaywrightClient.mjs')).PlaywrightClient : BrowserClient;
const browser = await Client.attach(portFile);
const before = (await api('/api/state')).state;
const directory = await fs.mkdtemp(path.join(path.dirname(logFile), 'browser-check-'));
const recovery = path.join(directory, 'original.syx');
const payload = Buffer.from(before.payload_hex, 'hex');
assert.equal(payload.length, 1146);
const body = Buffer.concat([Buffer.from([1, 0, 0, 0]), payload]);
const checksum = (128 - [...body].reduce((total, value) => (total + value) % 128, 0)) % 128;
await fs.writeFile(recovery, Buffer.concat([Buffer.from('f0411000004d12', 'hex'), body, Buffer.from([checksum, 247])]), {mode: 0o600, flag: 'wx'});

async function hardware(predicate, description) {
  await until(async () => predicate((await api('/api/state')).state), description);
}

async function restoreMixer() {
  await browser.blank(); await api('/api/disconnect', {});
  await run(path.join(root, 'build/strict/octa-preset'), ['restore', recovery], {timeout: 20000});
  await browser.call('Page.navigate', {url: launch});
  await until(() => browser.evaluate('document.querySelector("#connection-message")?.hidden === true'), 'reconnected after raw recovery');
  await browser.click('#view-switch');
  assert.deepEqual((await api('/api/state')).state, before);
}

async function contextLabels(selector, labels) {
  await browser.click(selector, 1, 'right');
  assert.deepEqual(await browser.evaluate('[...document.querySelectorAll("#menus button")].map(node=>node.textContent)'), labels);
  await browser.key('#menus button', 'Escape');
}

try {
  await browser.blank();
  await browser.call('Page.navigate', {url: launch});
  await until(() => browser.evaluate('document.querySelectorAll(".preamp-strip").length === 8 && document.querySelector("#connection-message").hidden'), 'live preamp view');
  await checkFooter(browser, api);
  const geometryReference = JSON.parse(await fs.readFile(path.join(root, 'test/data/control-geometry.json'), 'utf8'));
  const controlGeometry = await browser.evaluate(`(${checkControlGeometry.toString()})(${JSON.stringify(geometryReference)})`);
  console.log(`PASS original main-panel control bounds: ${controlGeometry.checked} matched, ${controlGeometry.hidden} hidden at this rate`);
  await checkChannelFaders(browser, api);
  const devices = (await api('/api/devices')).devices;
  assert.equal(devices.filter(device=>device.selected).length, 1, 'Selected card is discovered through sysfs');
  await browser.key('#device-status', 'ContextMenu');
  await until(() => browser.evaluate('document.querySelectorAll("#menus [role=menuitemradio]").length > 0'), 'device selection menu');
  assert.equal(await browser.evaluate('document.querySelectorAll("#menus [role=menuitemradio]").length'), devices.length);
  if (write) {
    const start = browser.selections.length;
    await browser.click('#menus [aria-checked=true]');
    await until(() => browser.selections.slice(start).some(selection=>selection.id === devices.find(device=>device.selected).id && selection.httpStatus === 200), 'fresh device selection and CTRL reconnection');
    await until(() => browser.evaluate('document.querySelector("#connection-message").hidden && document.querySelector("#busy-message").hidden'), 'selected device controls ready');
    assert.deepEqual((await api('/api/state')).state, before, 'Device reselection preserves complete raw state');
  } else await browser.key('#menus [aria-checked=true]', 'Escape');
  console.log('PASS sysfs device menu, keyboard access and selected-device lifecycle');
  assert.equal(await browser.evaluate('document.querySelectorAll("#preamp-view .knob").length'), 56);
  assert.equal(await browser.evaluate('document.querySelectorAll("#preamp-view canvas").length'), 8);
  const exposed = await browser.evaluate('location.hash');
  assert.equal(exposed, '');
  await browser.screenshot(path.join(directory, 'preamp.png'));
  await fs.writeFile(path.join(directory, 'preamp-geometry.json'), JSON.stringify(await browser.evaluate(`(() => {
    const panel=document.querySelector('#panel').getBoundingClientRect();
    return [...document.querySelectorAll('.preamp-strip:first-child .knob-dial, .preamp-strip:first-child .toggle, .preamp-strip:first-child canvas, .preamp-strip:first-child .meter')].map(node=>{
      const r=node.getBoundingClientRect(); return {label:node.getAttribute('aria-label') || node.parentElement.getAttribute('aria-label') || node.className,
        x:r.x-panel.x,y:r.y-panel.y,width:r.width,height:r.height};
    });
  })()`), null, 2), {mode: 0o600});
  await until(async () => (await api('/api/meters')).meters?.age_ms < 1000, 'actual input meters');
  await until(async () => {
    const pcm = (await api('/api/meters')).meters?.pcm;
    return pcm?.enabled && pcm.error === 0 && pcm.capture.fresh && pcm.playback.fresh &&
      pcm.received_age_ms < 500;
  }, 'actual kernel capture and playback PCM meters');
  for (const [selector, labels] of [
    ['[aria-label="Input 1 compressor bypass"]', ['Bypass Off All Ch.', 'Bypass On All Ch.']],
    ['[aria-label="Input 1 +48"]', ['Phantom Off All Ch.']],
    ['[aria-label="Input 1 LO-CUT"]', ['Lo-Cut On All Ch.', 'Lo-Cut Off All Ch.']],
    ['[aria-label="Input 1 PHASE"]', ['Phase On All Ch.', 'Phase Off All Ch.']],
    ['[aria-label="Compressor stereo link 1-2"]', ['Link On All Ch.', 'Link Off All Ch.']]]) {
    await contextLabels(selector, labels);
  }
  if (write) {
    const sensitivity = '#preamp-view .preamp-strip:first-child .sensitivity';
    const original = before.preamps[0].sensitivity_index;
    await browser.key(sensitivity, original < 100 ? 'ArrowUp' : 'ArrowDown');
    await hardware(state => state.preamps[0].sensitivity_index === original + (original < 100 ? 1 : -1), 'sensitivity hardware readback');
    await api('/api/control', {kind: 'preamp', channel: 0, parameter: 'sensitivity', value: original});
    const editor = '[aria-label="Input 1 sensitivity value"]';
    await browser.click(editor, 2);
    await until(() => browser.evaluate(`!document.querySelector(${JSON.stringify(editor)}).readOnly`), 'double-click sensitivity edit');
    await browser.call('Input.insertText', {text: '8.25'});
    await new Promise(resolve => setTimeout(resolve, 600));
    assert.equal(await browser.evaluate(`document.querySelector(${JSON.stringify(editor)}).value`), '8.25', 'polling preserves active text edits');
    await browser.key(editor, 'Enter');
    await hardware(state => state.preamps[0].sensitivity_index === 16, 'typed sensitivity truncates to half dB');
    await browser.key(editor, 'F2');
    await browser.call('Input.insertText', {text: '25.9'});
    await browser.click('.brand');
    await hardware(state => state.preamps[0].sensitivity_index === 51, 'focus-loss sensitivity commit');
    await browser.key(editor, 'F2');
    await browser.call('Input.insertText', {text: '49.5'});
    await browser.key(editor, 'Escape');
    await browser.key(editor, 'F2');
    await browser.call('Input.insertText', {text: '50.1'});
    await browser.key(editor, 'Enter');
    assert.equal((await api('/api/state')).state.preamps[0].sensitivity_index, 51, 'cancel and out-of-range edits preserve the device');
    await api('/api/control', {kind: 'preamp', channel: 0, parameter: 'sensitivity', value: original});
    const button = '[aria-label="Input 1 LO-CUT"]';
    await browser.click(button);
    await hardware(state => state.preamps[0].low_cut !== before.preamps[0].low_cut, 'low-cut hardware readback');
    await api('/api/control', {kind: 'preamp', channel: 0, parameter: 'low-cut', value: Number(before.preamps[0].low_cut)});
    const attack = '[aria-label="Input 1 / ATTACK"]';
    const requests = browser.controls.length;
    await browser.key(attack, 'End');
    assert.equal((await api('/api/state')).state.preamps[0].compressor.attack_index,
      before.preamps[0].compressor.attack_index, 'End leaves a reference knob unchanged');
    assert.equal(browser.controls.length, requests, 'End sends no knob command');
    await api('/api/control', {kind: 'preamp', channel: 0, parameter: 'attack', value: 25});
    await hardware(state => state.preamps[0].compressor.attack_index === 25, 'nonlinear attack endpoint');
    await until(() => browser.evaluate(`document.querySelector(${JSON.stringify(attack)}).getAttribute("aria-valuetext") === '100 ms'`),
      'nonlinear attack endpoint display');
    assert.equal(await browser.evaluate(`document.querySelector(${JSON.stringify(attack)}).getAttribute("aria-valuetext")`), '100 ms');
    await api('/api/control', {kind: 'preamp', channel: 0, parameter: 'attack', value: before.preamps[0].compressor.attack_index});
    const outputMeter = '.preamp-strip:first-child .meter.out';
    await api('/api/control', {kind: 'preamp', channel: 0, parameter: 'compressor', value: 1});
    const graphPalette = async (background, grid) => {
      await until(() => browser.evaluate(`(() => {
        const context=document.querySelector('.preamp-strip:first-child canvas').getContext('2d');
        return JSON.stringify([...context.getImageData(11,11,1,1).data].slice(0,3)) === '${JSON.stringify(background)}' &&
          JSON.stringify([...context.getImageData(21,11,1,1).data].slice(0,3)) === '${JSON.stringify(grid)}';
      })()`), 'actual rendered compressor background and overlaid grid');
    };
    await graphPalette([208, 155, 66], [133, 115, 88]);
    await until(() => browser.evaluate(`!document.querySelector(${JSON.stringify(outputMeter)}).matches('.unavailable, .stale')`), 'enabled compressor shows actual USB capture meter');
    await browser.click('[aria-label="Input 1 compressor bypass"]');
    await hardware(state => !state.preamps[0].compressor.enabled, 'compressor bypass readback');
    await graphPalette([99, 77, 66], [107, 89, 62]);
    await until(() => browser.evaluate(`document.querySelector(${JSON.stringify(outputMeter)}).classList.contains('unavailable')`), 'bypassed compressor hides OUT meter');
    await api('/api/control', {kind: 'preamp', channel: 0, parameter: 'compressor', value: Number(before.preamps[0].compressor.enabled)});
    console.log('PASS preamp controls: actual keyboard/pointer events and device readback');
  }
  await browser.click('#view-switch');
  assert.equal(await browser.evaluate('document.querySelector("#mixer-view").hidden'), false);
  for (const source of ['input', 'playback']) {
    const strip = `.mixer-strip[data-source="${source}"][data-channel="0"]`;
    for (const [selector, labels] of [
      ['.mixer-pair-link', ['Link On All Ch.', 'Link Off All Ch.']],
      ['.mixer-switches > button:first-of-type', ['Mute ON All Ch.', 'Mute OFF All Ch.']],
      ['.mixer-switches > button:nth-of-type(2)', ['Solo ON All Ch.', 'Solo OFF All Ch.']]]) {
      await contextLabels(`${strip} ${selector}`, labels);
    }
  }
  assert.equal(await browser.evaluate('document.querySelectorAll(".mixer-strip").length'), 20);
  await until(() => browser.evaluate('document.querySelectorAll(".level-lane:not(.stale)").length === 20'), 'all input and playback mixer meters use fresh PCM');
  assert.equal(await browser.evaluate(`(() => {
    const first=document.querySelector('.mixer-strip .fader input').getBoundingClientRect();
    return [...document.querySelectorAll('.fader')].every(node=>{
      const input=node.querySelector('input'), box=input.getBoundingClientRect(), label=node.querySelector('output').getBoundingClientRect();
      const cap=getComputedStyle(input).getPropertyValue('--fader-cap');
      const gap=node.closest('.master-group') ? 1 : 0;
      return box.width===18 && box.height===88 && box.top===first.top && label.top===box.bottom+gap &&
        cap.includes(node.closest('.master-group') ? 'fader-red.svg' : 'fader-white.svg');
    });
  })()`), true, 'original channel/master fader alignment, colors and exact zero/one-pixel readout gaps');
  await browser.screenshot(path.join(directory, 'mixer.png'));
  if (playwright && write) await checkMasterFaders(browser, api, restoreMixer);
  if (playwright && write) await checkFaderDrags(browser, api, restoreMixer);
  if (playwright && write) await checkWheels(browser, api, restoreMixer);
  if (playwright && write) await checkWheelDrags(browser, api, restoreMixer);
  if (playwright && write) await checkKnobDrags(browser, api, restoreMixer);
  if (write) {
    await browser.key('[aria-label="input 1 / PAN"]', 'ArrowRight');
    const initialPan = before.mixers[0].input.channels[0].pan;
    const initialPosition = Math.floor((initialPan * 100 + 8192) / 16384);
    const expected = Math.floor((Math.min(200, initialPosition + 1) * 16384 + 50) / 100);
    await hardware(state => state.mixers[0].input.channels[0].pan === expected, 'mixer pan hardware readback');
    await api('/api/control', {kind: 'mixer', source: 'input', bus: 0, channel: 0, parameter: 'pan', value: initialPan});
    console.log('PASS direct mixer pan: reference rounding and hardware readback');
    const routes = enabled => enabled ? [0, 1, 2, 3, 0] : [0, 5, 6, 7, 8];
    const toggleMultiple = async enabled => {
      await browser.click('#header-action');
      await until(() => browser.evaluate('document.querySelector("#confirm").open'), 'multi-mixer confirmation');
      await browser.click('#confirm-yes');
      await hardware(state => JSON.stringify(state.output_routes) === JSON.stringify(routes(enabled)),
        `multi-mixer ${enabled ? 'enable' : 'disable'} patchbay`);
      await until(() => browser.evaluate(`document.querySelector('#header-action').classList.contains('lit') === ${enabled}`), 'multi-mixer lamp');
    };
    if ((await api('/api/state')).state.output_routes.some(route => route >= 1 && route <= 3)) {
      await toggleMultiple(false);
    }
    await toggleMultiple(true);
    assert.equal(await browser.evaluate('document.querySelectorAll("#mixer-tabs > button:not([hidden])").length'), 4);
    for (const selected of [1, 2, 3, 0]) {
      const start = browser.controls.length;
      await browser.click(`#mixer-tabs > button:nth-of-type(${selected + 1})`);
      await until(() => browser.controls.slice(start).some(body => body.kind === 'display' && body.bus === selected && body.httpStatus === 200),
        `mixer ${selected} sends its real display command`);
    }
    const identifyStart = browser.controls.length;
    await browser.click('#device-status');
    await until(() => browser.controls.slice(identifyStart).some(body => body.kind === 'identify' && body.httpStatus === 200), 'identification command');
    await api('/api/control', {kind: 'display', bus: 0});
    await toggleMultiple(false);
    assert.equal(await browser.evaluate('document.querySelectorAll("#mixer-tabs > button:not([hidden])").length'), 1);
    // Restore even nonstandard original routing/display metadata before proceeding.
    await browser.blank(); await api('/api/disconnect', {});
    await run(path.join(root, 'build/strict/octa-preset'), ['restore', recovery], {timeout: 20000});
    await browser.call('Page.navigate', {url: launch});
    await until(() => browser.evaluate('document.querySelector("#connection-message")?.hidden === true'), 'reconnected after raw recovery');
    await browser.click('#view-switch');
    assert.deepEqual((await api('/api/state')).state, before);
    console.log('PASS multi-mixer on/off, real LCD/identify commands, tab gating and complete raw recovery');
  }
  await browser.click('[data-menu="device"]');
  const submenuParent = '#menus .menu[data-depth="0"] > button[aria-haspopup="menu"]';
  const submenuButtons = '#menus .menu[data-depth="1"] button';
  await browser.key(submenuParent, 'ArrowRight');
  assert.deepEqual(await browser.evaluate(`[...document.querySelectorAll(${JSON.stringify(submenuButtons)})].map(node=>node.textContent)`),
    ['All', 'MIC PRE', 'DIRECT MIX', 'REVERB', 'PATCHBAY']);
  await browser.key(submenuButtons, 'Escape');
  assert.equal(await browser.evaluate('document.querySelectorAll("#menus .menu").length'), 1);
  await browser.key(submenuParent, 'Escape');
  assert.equal(await browser.evaluate('document.querySelectorAll("#menus .menu").length'), 0);
  for (const [selector, count, parameter] of [['[aria-label="input 1 volume"]', 10, 'volume'],
    ['[aria-label="input 1 / PAN"]', 2, 'pan'], ['[aria-label="Input 1 / REVERB"]', 10, 'send']]) {
    await browser.click(selector, 1, 'right');
    await browser.key(submenuParent, 'ArrowRight');
    assert.equal(await browser.evaluate(`document.querySelectorAll(${JSON.stringify(submenuButtons)}).length`), count);
    assert.equal(await browser.evaluate('[...document.querySelectorAll("#menus .menu")].every(node=>{const r=node.getBoundingClientRect();return r.left>=0&&r.top>=0&&r.right<=innerWidth&&r.bottom<=innerHeight;})'), true);
    if (write) {
      const start = browser.controls.length;
      await browser.click(`${submenuButtons}:nth-of-type(${parameter === 'pan' ? 1 : count})`);
      const targets = parameter === 'pan' ? [0, 32768, 0, 32768, 0, 32768, 0, 32768, 0, 32768] : Array(10).fill(0);
      await until(() => new Set(browser.controls.slice(start).filter(body => body.kind === 'mixer' && body.source === 'input' &&
        body.bus === 0 && body.parameter === parameter && body.value === targets[body.channel] && body.httpStatus === 200).map(body => body.channel)).size === 10,
      `ten acknowledged All Ch. ${parameter} commands`);
      await hardware(state => parameter === 'send' ? state.reverb.input_send.every((value, channel) => value === targets[channel]) :
        state.mixers[0].input.channels.every((value, channel) => value[parameter] === targets[channel]), `All Ch. ${parameter} readback`);
      for (let channel = 0; channel < 10; channel++) {
        const value = parameter === 'send' ? before.reverb.input_send[channel] : before.mixers[0].input.channels[channel][parameter];
        await api('/api/control', {kind: 'mixer', source: 'input', bus: 0, channel, parameter, value});
      }
      assert.deepEqual((await api('/api/state')).state, before, `All Ch. ${parameter} full restoration`);
    } else {
      await browser.key(submenuButtons, 'Escape');
      await browser.key(submenuParent, 'Escape');
    }
    assert.equal(await browser.evaluate(`document.activeElement.matches(${JSON.stringify(selector)})`), true);
  }
  console.log('PASS original context labels, Initialize/All Ch. submenus and keyboard navigation');
  await browser.click('[data-menu="driver"]');
  assert.equal(await browser.evaluate('document.querySelector("#menus [role=menuitemcheckbox]").disabled'), true,
    'A browser cannot change its host window stacking policy');
  await browser.click('#menus button:nth-of-type(3)');
  assert.equal(await browser.evaluate('document.querySelector("#readme").open'), true);
  assert.equal(await browser.evaluate('document.querySelectorAll("#readme a, #readme script, #readme iframe").length'), 0);
  await browser.click('#readme [data-close]');
  await browser.click('[data-menu="driver"]');
  await browser.click('#menus button:first-of-type');
  await until(() => browser.evaluate(`JSON.stringify([...document.querySelector('#settings-device').options].map(option=>option.value)) === ${JSON.stringify(JSON.stringify(devices.map(device=>device.id)))}`), 'settings device selector uses discovered identities');
  assert.equal(await browser.evaluate('document.querySelector("#settings-device").value'), devices.find(device=>device.selected).id);
  const settingsText = await browser.evaluate('document.querySelector("#settings").textContent');
  assert.match(settingsText, /ALSA/);
  assert.match(settingsText, /PipeWire or JACK/);
  assert.doesNotMatch(settingsText, /ASIO|VS EXPAND/);
  assert.equal(await browser.evaluate('document.querySelectorAll("#settings footer button").length'), 1);
  await browser.click('#settings [data-close]');
  // Dialog inspection is read-only. Dangerous power/memory controls are never clicked.
  for (const name of ['patchbay', 'reverb', 'settings', 'auto-sens', 'diagram', 'about']) {
    await browser.evaluate(`document.querySelector('#${name}').showModal()`);
    assert.equal(await browser.evaluate(`document.querySelector('#${name}').open`), true);
    const screenshot = await browser.call('Page.captureScreenshot', {format: 'png'});
    await fs.writeFile(path.join(directory, `${name}.png`), Buffer.from(screenshot.data, 'base64'));
    await browser.evaluate(`document.querySelector('#${name}').close()`);
  }
  await until(() => browser.evaluate('document.querySelector("#diagram img").complete && document.querySelector("#diagram img").naturalWidth === 945'), 'local signal-flow schematic decoded');
  await browser.call('Emulation.setDeviceMetricsOverride', {width: 978, height: 429, deviceScaleFactor: 1, mobile: false});
  for (const name of ['patchbay', 'reverb', 'settings', 'auto-sens', 'diagram', 'about', 'readme', 'port-warning']) {
    await browser.evaluate(`document.querySelector('#${name}').showModal()`);
    assert.equal(await browser.evaluate(`(() => {
      const dialog=document.querySelector('#${name}'), box=dialog.getBoundingClientRect();
      return box.top >= 0 && box.bottom <= innerHeight && box.left >= 0 && box.right <= innerWidth &&
        [...dialog.querySelectorAll('footer button')].every(node=>{const r=node.getBoundingClientRect();return r.top>=0&&r.bottom<=innerHeight&&document.elementFromPoint(r.x+r.width/2,r.y+r.height/2)===node;});
    })()`), true, `${name} footer remains visible and clickable at the desktop window size`);
    if (name === 'diagram') await browser.screenshot(path.join(directory, 'diagram-desktop.png'));
    await browser.evaluate(`document.querySelector('#${name}').close()`);
  }
  await browser.call('Emulation.setDeviceMetricsOverride', {width: 1100, height: 650, deviceScaleFactor: 1, mobile: false});
  console.log('PASS local schematic and all dialog footers at the GTK viewport size');
  await browser.downloadTo(directory);
  await browser.click('[data-menu="device"]');
  await browser.click('#menus .menu > button:first-of-type');
  const settingsFile = path.join(directory, 'OCTA-CAPTURE.octa');
  await until(async () => { try { return (await fs.stat(settingsFile)).size === 1639; } catch { return false; } }, 'settings download');
  const settings = await fs.readFile(settingsFile);
  const header = 'OCTA-CAPTURE Control Panel Data\0Ver100\0';
  assert.equal(settings.subarray(0, Buffer.byteLength(header)).toString('ascii'), header);
  await checkAutoSenseFailure(browser);
  if (write) {
    await browser.setFiles('#load-file', [settingsFile]);
    await until(() => browser.evaluate('document.querySelector("#confirm").open'), 'load confirmation');
    await browser.click('#confirm-yes');
    await until(() => browser.evaluate('!document.querySelector("#load-file").disabled && !document.querySelector("#confirm").open'), 'settings load completion');
    assert.equal(await browser.evaluate('document.querySelector("#toast").hidden'), true, 'No settings load error');
    console.log('PASS browser settings download/upload, confirmation and verified device application');
    await browser.click('#view-switch');
    for (const action of ['cancel', 'finish']) {
      await browser.click('#header-action');
      await checkAutoSenseSelection(browser);
      await browser.click('#auto-none');
      await browser.click('#auto-channels input[value="0"]');
      await browser.click('#auto-start');
      await until(async () => (await api('/api/state')).auto_sense?.state === 'running', 'Auto-Sens device activity');
      assert.deepEqual(await browser.evaluate('[...document.querySelectorAll("#auto-channels input:checked")].map(node=>Number(node.value))'), [0], 'Active selection agrees with the backend job');
      assert.equal((await api('/api/state')).busy, true);
      await browser.screenshot(path.join(directory, `auto-sens-${action}.png`));
      await browser.click(action === 'cancel' ? '#auto-cancel' : '#auto-start');
      const result = action === 'cancel' ? 'cancelled' : 'completed';
      await until(async () => (await api('/api/state')).auto_sense?.state === result, `Auto-Sens ${result}`);
      await until(async () => !(await api('/api/state')).busy, 'Auto-Sens released CTRL');
      await until(() => browser.evaluate('!document.querySelector("#auto-sens").open'), 'Successful Auto-Sens completion closes the dialog');
      const current = (await api('/api/state')).state;
      if (action === 'cancel') assert.deepEqual(current, before, 'Auto-Sens cancel restores full state');
      else {
        const actual = Buffer.from(current.payload_hex, 'hex');
        actual[0x1d] = payload[0x1d];
        assert.deepEqual(actual, payload, 'Auto-Sens finish changes only selected sensitivity');
        await api('/api/control', {kind: 'preamp', channel: 0, parameter: 'sensitivity', value: before.preamps[0].sensitivity_index});
      }
    }
    console.log('PASS browser Auto-Sens start/finish/cancel, verified notifications and recovery');
  }
  if (write) await checkReverb(browser, api);
  if (write) await checkInputMonitor(browser, api, restoreMixer);
  if (matrix) await checkControlMatrix(browser, api);
  if (rates) await checkRates(browser, api, directory, [44100, 48000, 96000, 192000][payload[2]]);
  if (rates) await checkFooterRates(browser, api);
  const expectedErrors = reload ? await checkReconnect(browser, api, launch, run) : {consoleErrors: [], httpErrors: []};
  await checkDialogWindows(browser, directory, api);
  await checkControlHits(browser, api);
  assert.deepEqual(browser.exceptions, [], 'JavaScript exceptions');
  assert.deepEqual(browser.securityErrors, [], 'Content Security Policy violations');
  if (playwright) {
    assert.deepEqual(browser.consoleErrors, expectedErrors.consoleErrors, 'Playwright console errors');
    assert.deepEqual(browser.networkErrors, [], 'Playwright failed requests');
    assert.deepEqual(browser.httpErrors, expectedErrors.httpErrors, 'Playwright HTTP errors');
  }
  assert.deepEqual((await api('/api/state')).state, before, 'Complete raw state preserved');
  console.log('PASS browser views, dialogs, actual meters, CSP and complete state preservation');
} catch (error) {
  const loaded = await browser.evaluate('Boolean(document.querySelector("#panel"))');
  if (loaded) await browser.screenshot(path.join(directory, 'failure.png'));
  await fs.writeFile(path.join(directory, 'failure.json'), JSON.stringify(await browser.evaluate(`({
    busyHidden:document.querySelector('#busy-message')?.hidden,
    connected:document.querySelector('#connection-message')?.hidden,
    dialogs:[...document.querySelectorAll('dialog[open]')].map(node=>node.id),
    menu:[...document.querySelectorAll('#menus button')].map(node=>({label:node.textContent,disabled:node.disabled})),
    toast:document.querySelector('#toast')?.hidden ? null : document.querySelector('#toast')?.textContent
  })`), null, 2), {mode: 0o600});
  throw error;
} finally {
  await browser.blank();
  await api('/api/disconnect', {});
  if (write) {
    await run(path.join(root, 'build/strict/octa-preset'), ['restore', recovery], {timeout: 20000});
  }
  const {stdout} = await run(path.join(root, 'build/strict/octa-state'), [], {timeout: 10000});
  assert.deepEqual(JSON.parse(stdout), before, 'Independent final state and released CTRL port');
  await browser.close();
  console.log(`PASS independent final readback; screenshots: ${path.relative(root, directory)}`);
}
