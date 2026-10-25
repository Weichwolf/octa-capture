// SPDX-License-Identifier: GPL-2.0-only
/* eslint-env node */
// Real Playwright input with synthetic device replies and unusable device binaries.
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import {spawn} from 'node:child_process';
import {once} from 'node:events';
import {PlaywrightClient} from './PlaywrightClient.mjs';
import {until} from './BrowserClient.mjs';

const root = path.resolve(new URL('..', import.meta.url).pathname);
await fs.mkdir(path.join(root, 'build/gui'), {recursive: true});
const directory = await fs.mkdtemp(path.join(root, 'build/gui/clock-offline-'));
const server = spawn('/usr/bin/python3', ['-u', 'tools/serve-gui.py', '--port', '0',
  '--bin-dir', path.join(directory, 'no-device-binaries'), '--data-dir', path.join(directory, 'state')],
{cwd: root, stdio: ['ignore', 'pipe', 'pipe']});
let serverOutput = '', serverErrors = '';
server.stdout.on('data', data => { serverOutput += data; });
server.stderr.on('data', data => { serverErrors += data; });
let browser, model, polls = 0, pending = null;
const actions = [], unexpected = [];
const rates = [44100, 48000, 96000, 192000];

function fixture() {
  const group = () => ({channels: Array.from({length: 10}, () => ({mute: false, solo: false, pan: 0, volume: 0})),
    pairs: Array.from({length: 5}, () => ({linked: false, mono: false}))});
  return {connected: true, busy: false, preferences: {show_port_warning: false}, meters: null,
    auto_sense: null, capabilities: {auto_sense: true}, state: {payload_hex: '00'.repeat(1146),
      preamps: Array.from({length: 8}, () => ({phantom: false, phase_inverted: false, hi_z: false,
        low_cut: false, sensitivity_index: 0, compressor: {enabled: false, gate_index: 0,
          attack_index: 0, release_index: 0, threshold_index: 0, ratio_index: 0, gain_index: 0}})),
      stereo_links: [false, false, false, false], output_routes: [0, 0, 0, 0, 0],
      mixers: Array.from({length: 4}, () => ({input: group(), playback: group(), input_output_link: false,
        input_master: {linked: false, volume: [0, 0]}, playback_master: {linked: false, volume: [0, 0]}})),
      reverb: {type: 0, pre_delay_indices: [0, 0, 0, 0, 0], time_indices: [0, 0, 0, 0, 0],
        input_send: Array(10).fill(0), master_return: 0}}};
}

function setClock(status) {
  model.status = status;
  if (status) {
    const index = rates.indexOf(status.sample_rate_hz);
    model.state.payload_hex = model.state.payload_hex.slice(0, 4) + `0${index}` + model.state.payload_hex.slice(6);
  }
}

async function responses(route) {
  const request = route.request(), endpoint = new URL(request.url()).pathname;
  let json;
  if (endpoint === '/api/state') { json = model; polls++; }
  else if (endpoint === '/api/meters') json = {meters: null};
  else if (endpoint === '/api/devices') json = {devices: []};
  else if (endpoint === '/api/preferences') json = model.preferences;
  else if (endpoint === '/api/action') {
    const action = request.postDataJSON(); actions.push(action);
    assert.equal(action.action, 'sample-rate');
    if (pending) {
      await pending;
      return route.fulfill({status: 503, json: {error: 'The device clock currently permits only 48000 Hz.'}});
    }
    const rate = action.rate, packet = Buffer.from(model.status.raw_status_hex, 'hex');
    packet.writeUIntLE(rate, 3, 3);
    setClock({...model.status, sample_rate_hz: rate, raw_status_hex: packet.toString('hex')});
    json = model;
  } else {
    unexpected.push(endpoint);
    return route.fulfill({status: 500, json: {error: 'Unexpected offline-test request.'}});
  }
  await route.fulfill({status: 200, json});
}

async function expectChoices(allowed, selected, disabled = false) {
  await until(async () => {
    const actual = await browser.page.locator('[data-rate-select]').evaluateAll(nodes => nodes.map(node => ({
      allowed: [...node.options].filter(option => !option.hidden && !option.disabled).map(option => Number(option.value)),
      value: node.value, disabled: node.disabled, count: node.options.length})));
    return actual.length === 2 && actual.every(node => JSON.stringify(node.allowed) === JSON.stringify(allowed) &&
      node.value === String(selected) && node.disabled === disabled && node.count === allowed.length);
  }, 'both clock selectors match availability and selection');
}

async function transitions(cases) {
  for (const row of cases) {
    const before = polls;
    setClock(row.status);
    await until(() => polls > before, 'synthetic clock state polled');
    await expectChoices(row.allowed, row.status.sample_rate_hz);
  }
  setClock(null);
  await expectChoices([], '', true);
  for (const row of cases.filter(row => row.status.raw_status_hex.slice(12, 14) === '00')) {
    setClock(row.status); await expectChoices(row.allowed, row.status.sample_rate_hz);
  }
}

async function inputAndPending(cases) {
  const internal = cases.find(row => row.status.sample_rate_hz === 48000 && row.status.raw_status_hex.endsWith('0001'));
  const external = cases.find(row => row.status.sample_rate_hz === 48000 && row.status.raw_status_hex.endsWith('0101'));
  setClock(external.status); await expectChoices([48000], 48000);
  for (const selector of ['#footer-rate', '#sample-rate']) {
    if (selector === '#sample-rate') {
      await browser.click('[data-menu="driver"]');
      await browser.page.getByRole('menuitem', {name: 'Driver Settings…', exact: true}).click();
    }
    await browser.page.locator(selector).focus();
    await browser.page.keyboard.press('End');
    await browser.page.keyboard.press('Enter');
    await expectChoices([48000], 48000);
    await browser.page.locator(selector).evaluate(node => {
      node.add(new Option('Forged unavailable rate', '96000'));
      node.value = '96000'; node.dispatchEvent(new Event('change', {bubbles: true}));
    });
    await expectChoices([48000], 48000);
    if (selector === '#sample-rate') await browser.page.keyboard.press('Escape');
  }
  assert.equal(actions.length, 0, 'External-clock keys and forged selection cannot submit an unavailable rate');
  setClock(internal.status); await expectChoices(rates, 48000);
  await browser.page.locator('#footer-rate').selectOption('96000');
  await expectChoices(rates, 96000);
  assert.deepEqual(actions, [{action: 'sample-rate', rate: 96000}]);
  setClock(internal.status); await expectChoices(rates, 48000);
  let release;
  pending = new Promise(resolve => { release = resolve; });
  try {
    await browser.page.locator('#footer-rate').selectOption('96000');
    await until(() => actions.length === 2, 'held synthetic rate request');
    await expectChoices(rates, 48000, true);
    setClock(external.status);
  } finally { release(); }
  await expectChoices([48000], 48000);
  pending = null;
  assert.match(await browser.page.locator('#toast').textContent(), /clock.*48000/);
}

try {
  await until(() => /http:\/\/127\.0\.0\.1:[0-9]+\/#token=[^\s]+/.test(serverOutput), 'offline local server');
  const launch = serverOutput.match(/http:\/\/127\.0\.0\.1:[0-9]+\/#token=[^\s]+/)[0];
  const {cases} = JSON.parse(await fs.readFile(path.join(root, 'test/data/rate-availability.json'), 'utf8'));
  model = fixture(); setClock(cases[0].status);
  browser = await PlaywrightClient.attach();
  await browser.page.route('**/api/**', responses);
  await browser.call('Page.navigate', {url: launch});
  await until(() => browser.evaluate('document.querySelector("#connection-message")?.hidden === true'), 'synthetic panel');
  await transitions(cases);
  await inputAndPending(cases);
  assert.deepEqual(unexpected, []);
  assert.deepEqual(browser.exceptions, []);
  assert.deepEqual(browser.securityErrors, []);
  assert.deepEqual(browser.networkErrors, []);
  assert.deepEqual(browser.httpErrors, [{path: '/api/action', status: 503}]);
  assert.equal(browser.consoleEvents.length, 1, 'Only the deliberate rate-action rejection reaches the console');
  assert.equal(browser.consoleEvents[0].path, '/api/action');
  assert.match(browser.consoleEvents[0].text, /503/);
  await browser.screenshot(path.join(directory, 'external-clock.png'));
  console.log('PASS Playwright: 64 reference clock choices in both selectors, missing/restored status, trusted keys, forged selection and pending-error recovery. All device replies are synthetic.');
} finally {
  if (browser) await browser.close();
  if (server.exitCode === null) { server.kill('SIGTERM'); await once(server, 'exit'); }
  assert.equal(serverErrors, '', 'Offline server has no errors or device access attempts');
}
