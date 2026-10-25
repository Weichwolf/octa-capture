// SPDX-License-Identifier: GPL-2.0-only
/* eslint-env node */
// Trusted Playwright input; every device reply is synthetic and hardware tools are unavailable.
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import {spawn} from 'node:child_process';
import {once} from 'node:events';
import {PlaywrightClient} from './PlaywrightClient.mjs';
import {until} from './BrowserClient.mjs';

const root = path.resolve(new URL('..', import.meta.url).pathname);
await fs.mkdir(path.join(root, 'build/gui'), {recursive: true});
const directory = await fs.mkdtemp(path.join(root, 'build/gui/knob-display-offline-'));
const server = spawn('/usr/bin/python3', ['-u', 'tools/serve-gui.py', '--port', '0',
  '--bin-dir', path.join(directory, 'no-device-binaries'), '--data-dir', path.join(directory, 'state')],
{cwd: root, stdio: ['ignore', 'pipe', 'pipe']});
let serverOutput = '', serverErrors = '', browser;
server.stdout.on('data', data => { serverOutput += data; });
server.stderr.on('data', data => { serverErrors += data; });
const actions = [], unexpected = [];
const sensitivity = JSON.parse(await fs.readFile(path.join(root, 'test/data/sensitivity-display.json'), 'utf8'));

function fixture() {
  const group = () => ({channels: Array.from({length: 10}, () => ({mute: false, solo: false, pan: 0, volume: 0})),
    pairs: Array.from({length: 5}, () => ({linked: false, mono: false}))});
  return {connected: true, busy: false, preferences: {show_port_warning: false}, meters: null,
    auto_sense: null, capabilities: {auto_sense: true},
    status: {sample_rate_hz: 48000, raw_status_hex: '01000080bb000001', clock_source: 'internal', clock_active: true},
    state: {payload_hex: '000001' + '00'.repeat(1143),
      preamps: Array.from({length: 8}, () => ({phantom: false, phase_inverted: false, hi_z: false,
        low_cut: false, sensitivity_index: 0, compressor: {enabled: false, gate_index: 0,
          attack_index: 0, release_index: 0, threshold_index: 0, ratio_index: 0, gain_index: 0}})),
      stereo_links: [false, false, false, false], output_routes: [0, 0, 0, 0, 0],
      mixers: Array.from({length: 4}, () => ({input: group(), playback: group(), input_output_link: false,
        input_master: {linked: false, volume: [0, 0]}, playback_master: {linked: false, volume: [0, 0]}})),
      reverb: {type: 0, pre_delay_indices: [0, 0, 0, 0, 0], time_indices: [0, 0, 0, 0, 0],
        input_send: Array(10).fill(0), master_return: 0}}};
}
const model = fixture();

async function responses(route) {
  const request = route.request(), endpoint = new URL(request.url()).pathname;
  let json;
  if (endpoint === '/api/state') json = model;
  else if (endpoint === '/api/meters') json = {meters: null};
  else if (endpoint === '/api/devices') json = {devices: []};
  else if (endpoint === '/api/preferences') json = model.preferences;
  else if (endpoint === '/api/control') {
    const control = request.postDataJSON();
    assert.deepEqual(Object.keys(control).sort(), ['channel', 'kind', 'parameter', 'value']);
    assert.equal(control.kind, 'preamp');
    assert.equal(control.channel, 0);
    assert.equal(control.parameter, 'sensitivity');
    assert.ok(Number.isInteger(control.value) && control.value >= 0 && control.value <= 100);
    model.state.preamps[0].sensitivity_index = control.value;
    actions.push(control);
    json = model;
  } else {
    unexpected.push(endpoint);
    return route.fulfill({status: 500, json: {error: 'Unexpected offline-test request.'}});
  }
  await route.fulfill({status: 200, json});
}

async function displayCases() {
  const vectors = JSON.parse(await fs.readFile(path.join(root, 'test/data/knob-display.json'), 'utf8'));
  const reference = new Map(vectors.cases.filter(row => row.maximum === 100).map(row => [row.value, row.frame]));
  for (let base = 0; base <= 100; base += 8) {
    const indices = Array.from({length: 8}, (_, channel) => Math.min(base + channel, 100));
    indices.forEach((index, channel) => { model.state.preamps[channel].sensitivity_index = index; });
    await until(async () => {
      const shown = await browser.page.locator('.sensitivity input.knob-value').evaluateAll(nodes => nodes.map(node => node.value));
      return JSON.stringify(shown) === JSON.stringify(indices.map(value => sensitivity.texts[value]));
    }, 'all eight sensitivity fields match reference numeric text');
    for (let channel = 0; channel < 8; channel++) {
      const knob = browser.page.locator('.sensitivity').nth(channel);
      assert.equal(await knob.getAttribute('aria-valuetext'), `${(indices[channel] / 2).toFixed(1)} dB`);
      assert.equal(await knob.locator('.knob-dial').evaluate(node => node.style.getPropertyValue('--angle')),
        `${-135 + 270 * reference.get(indices[channel]) / 64}deg`);
    }
  }
}

async function editing() {
  const editor = browser.page.locator('.sensitivity input.knob-value').first();
  await editor.dblclick(); await editor.fill('8.25'); await editor.press('Enter');
  await until(async () => actions.length === 1 && await editor.inputValue() === sensitivity.texts[16], 'numeric field after committed write');
  assert.deepEqual(actions, [{kind: 'preamp', channel: 0, parameter: 'sensitivity', value: 16}]);
  await editor.dblclick(); await editor.fill('49.5'); await editor.press('Escape');
  assert.equal(await editor.inputValue(), sensitivity.texts[16]);
  assert.equal(actions.length, 1, 'Escape sends no control request');
}

try {
  await until(() => /http:\/\/127\.0\.0\.1:[0-9]+\/#token=[^\s]+/.test(serverOutput), 'offline local server');
  const launch = serverOutput.match(/http:\/\/127\.0\.0\.1:[0-9]+\/#token=[^\s]+/)[0];
  browser = await PlaywrightClient.attach();
  await browser.page.route('**/api/**', responses);
  await browser.call('Page.navigate', {url: launch});
  await until(() => browser.evaluate('document.querySelector("#connection-message")?.hidden === true'), 'synthetic panel');
  await displayCases();
  await editing();
  await browser.page.locator('.panel').screenshot({path: path.join(directory, 'preamp.png')});
  for (const errors of [unexpected, browser.exceptions, browser.securityErrors, browser.networkErrors,
    browser.httpErrors, browser.consoleEvents]) assert.deepEqual(errors, []);
  console.log('PASS Playwright: 101 sensitivity readouts/rotary positions across eight channels, dB accessibility, trusted commit and Escape. All replies synthetic.');
} finally {
  if (browser) await browser.close();
  if (server.exitCode === null) { server.kill('SIGTERM'); await once(server, 'exit'); }
  assert.equal(serverErrors, '', 'No server errors or hardware access attempts');
}
