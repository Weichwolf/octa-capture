// SPDX-License-Identifier: GPL-2.0-only
// Browser/hardware integration. --write changes safe controls with raw recovery.
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
import {BrowserClient, until} from './BrowserClient.mjs';

const run = promisify(execFile);
const root = path.resolve(path.dirname(new URL(import.meta.url).pathname), '..');
const logFile = path.resolve(process.argv[2] || 'build/gui/server.log');
const portFile = path.resolve(process.argv[3] || 'build/gui/browser-profile/DevToolsActivePort');
const write = process.argv.includes('--write');
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

const browser = await BrowserClient.attach(portFile);
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

try {
  await browser.blank();
  await browser.call('Page.navigate', {url: launch});
  await until(() => browser.evaluate('document.querySelectorAll(".preamp-strip").length === 8 && document.querySelector("#connection-message").hidden'), 'live preamp view');
  assert.equal(await browser.evaluate('document.querySelectorAll("#preamp-view .knob").length'), 56);
  assert.equal(await browser.evaluate('document.querySelectorAll("#preamp-view canvas").length'), 8);
  const exposed = await browser.evaluate('location.hash');
  assert.equal(exposed, '');
  await browser.screenshot(path.join(directory, 'preamp.png'));
  await until(async () => (await api('/api/meters')).meters?.age_ms < 1000, 'actual input meters');
  await until(async () => {
    const pcm = (await api('/api/meters')).meters?.pcm;
    return pcm?.enabled && pcm.error === 0 && pcm.capture.fresh && pcm.playback.fresh &&
      pcm.received_age_ms < 500;
  }, 'actual kernel capture and playback PCM meters');
  if (write) {
    const sensitivity = '#preamp-view .preamp-strip:first-child .sensitivity';
    const original = before.preamps[0].sensitivity_index;
    await browser.key(sensitivity, original < 100 ? 'ArrowUp' : 'ArrowDown');
    await hardware(state => state.preamps[0].sensitivity_index === original + (original < 100 ? 1 : -1), 'sensitivity hardware readback');
    await api('/api/control', {kind: 'preamp', channel: 0, parameter: 'sensitivity', value: original});
    const button = '[aria-label="Input 1 LO-CUT"]';
    await browser.click(button);
    await hardware(state => state.preamps[0].low_cut !== before.preamps[0].low_cut, 'low-cut hardware readback');
    await api('/api/control', {kind: 'preamp', channel: 0, parameter: 'low-cut', value: Number(before.preamps[0].low_cut)});
    const attack = '[aria-label="Input 1 / ATTACK"]';
    await browser.key(attack, 'End');
    await hardware(state => state.preamps[0].compressor.attack_index === 25, 'nonlinear attack endpoint');
    assert.equal(await browser.evaluate(`document.querySelector(${JSON.stringify(attack)}).getAttribute("aria-valuetext")`), '100 ms');
    await api('/api/control', {kind: 'preamp', channel: 0, parameter: 'attack', value: before.preamps[0].compressor.attack_index});
    const outputMeter = '.preamp-strip:first-child .meter.out';
    await api('/api/control', {kind: 'preamp', channel: 0, parameter: 'compressor', value: 1});
    await until(() => browser.evaluate(`!document.querySelector(${JSON.stringify(outputMeter)}).matches('.unavailable, .stale')`), 'enabled compressor shows actual USB capture meter');
    await browser.click('[aria-label="Input 1 compressor bypass"]');
    await hardware(state => !state.preamps[0].compressor.enabled, 'compressor bypass readback');
    await until(() => browser.evaluate(`document.querySelector(${JSON.stringify(outputMeter)}).classList.contains('unavailable')`), 'bypassed compressor hides OUT meter');
    await api('/api/control', {kind: 'preamp', channel: 0, parameter: 'compressor', value: Number(before.preamps[0].compressor.enabled)});
    console.log('PASS preamp controls: actual keyboard/pointer events and device readback');
  }
  await browser.click('#view-switch');
  assert.equal(await browser.evaluate('document.querySelector("#mixer-view").hidden'), false);
  assert.equal(await browser.evaluate('document.querySelectorAll(".mixer-strip").length'), 20);
  await until(() => browser.evaluate('document.querySelectorAll(".level-lane:not(.stale)").length === 20'), 'all input and playback mixer meters use fresh PCM');
  await browser.screenshot(path.join(directory, 'mixer.png'));
  if (write) {
    await browser.key('[aria-label="input 1 / PAN"]', 'ArrowRight');
    const initialPan = before.mixers[0].input.channels[0].pan;
    const initialPosition = Math.floor((initialPan * 100 + 8192) / 16384);
    const expected = Math.floor((Math.min(200, initialPosition + 5) * 16384 + 50) / 100);
    await hardware(state => state.mixers[0].input.channels[0].pan === expected, 'mixer pan hardware readback');
    await api('/api/control', {kind: 'mixer', source: 'input', bus: 0, channel: 0, parameter: 'pan', value: initialPan});
    console.log('PASS direct mixer pan: reference rounding and hardware readback');
  }
  // Dialog inspection is read-only. Dangerous power/memory/initialization controls are never clicked.
  for (const name of ['patchbay', 'reverb', 'settings', 'auto-sens', 'diagram', 'about']) {
    await browser.evaluate(`document.querySelector('#${name}').showModal()`);
    assert.equal(await browser.evaluate(`document.querySelector('#${name}').open`), true);
    const screenshot = await browser.call('Page.captureScreenshot', {format: 'png'});
    await fs.writeFile(path.join(directory, `${name}.png`), Buffer.from(screenshot.data, 'base64'));
    await browser.evaluate(`document.querySelector('#${name}').close()`);
  }
  await browser.call('Page.setDownloadBehavior', {behavior: 'allow', downloadPath: directory});
  await browser.click('[data-menu="device"]');
  await browser.click('#menus .menu > button:first-of-type');
  const settingsFile = path.join(directory, 'OCTA-CAPTURE.octa');
  await until(async () => { try { return (await fs.stat(settingsFile)).size === 1639; } catch { return false; } }, 'settings download');
  const settings = await fs.readFile(settingsFile);
  const header = 'OCTA-CAPTURE Control Panel Data\0Ver100\0';
  assert.equal(settings.subarray(0, Buffer.byteLength(header)).toString('ascii'), header);
  if (write) {
    const document = await browser.call('DOM.getDocument');
    const input = await browser.call('DOM.querySelector', {nodeId: document.root.nodeId, selector: '#load-file'});
    await browser.call('DOM.setFileInputFiles', {nodeId: input.nodeId, files: [settingsFile]});
    await until(() => browser.evaluate('document.querySelector("#confirm").open'), 'load confirmation');
    await browser.click('#confirm-yes');
    await until(() => browser.evaluate('!document.querySelector("#load-file").disabled && !document.querySelector("#confirm").open'), 'settings load completion');
    assert.equal(await browser.evaluate('document.querySelector("#toast").hidden'), true, 'No settings load error');
    console.log('PASS browser settings download/upload, confirmation and verified device application');
    await browser.click('#view-switch');
    for (const action of ['cancel', 'finish']) {
      await browser.click('#header-action');
      await browser.click('#auto-none');
      await browser.click('#auto-channels input[value="0"]');
      await browser.click('#auto-start');
      await until(async () => (await api('/api/state')).auto_sense?.state === 'running', 'Auto-Sens device activity');
      assert.equal((await api('/api/state')).busy, true);
      await browser.screenshot(path.join(directory, `auto-sens-${action}.png`));
      await browser.click(action === 'cancel' ? '#auto-cancel' : '#auto-start');
      const result = action === 'cancel' ? 'cancelled' : 'completed';
      await until(async () => (await api('/api/state')).auto_sense?.state === result, `Auto-Sens ${result}`);
      await until(async () => !(await api('/api/state')).busy, 'Auto-Sens released CTRL');
      const current = (await api('/api/state')).state;
      if (action === 'cancel') assert.deepEqual(current, before, 'Auto-Sens cancel restores full state');
      else {
        const actual = Buffer.from(current.payload_hex, 'hex');
        actual[0x1d] = payload[0x1d];
        assert.deepEqual(actual, payload, 'Auto-Sens finish changes only selected sensitivity');
        await api('/api/control', {kind: 'preamp', channel: 0, parameter: 'sensitivity', value: before.preamps[0].sensitivity_index});
      }
      await browser.click('#auto-cancel');
    }
    console.log('PASS browser Auto-Sens start/finish/cancel, verified notifications and recovery');
  }
  assert.deepEqual(browser.exceptions, [], 'JavaScript exceptions');
  assert.deepEqual(browser.securityErrors, [], 'Content Security Policy violations');
  assert.deepEqual((await api('/api/state')).state, before, 'Complete raw state preserved');
  console.log('PASS browser views, dialogs, actual meters, CSP and complete state preservation');
} finally {
  await browser.blank();
  await api('/api/disconnect', {});
  if (write) {
    await run(path.join(root, 'build/strict/octa-preset'), ['restore', recovery], {timeout: 20000});
  }
  const {stdout} = await run(path.join(root, 'build/strict/octa-state'), [], {timeout: 10000});
  assert.deepEqual(JSON.parse(stdout), before, 'Independent final state and released CTRL port');
  browser.close();
  console.log(`PASS independent final readback; screenshots: ${path.relative(root, directory)}`);
}
