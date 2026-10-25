// SPDX-License-Identifier: GPL-2.0-only
/* eslint-env node */
// Trusted Playwright input; every device reply is synthetic and hardware tools are unavailable.
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import {spawn} from 'node:child_process';
import {once} from 'node:events';
import {parseArgs} from 'node:util';
import {PlaywrightClient} from './PlaywrightClient.mjs';
import {until} from './BrowserClient.mjs';
import {fixture} from './GuiSyntheticModel.mjs';

const root = path.resolve(new URL('..', import.meta.url).pathname);
const {values} = parseArgs({options: {prefix: {type: 'string'}}});
await fs.mkdir(path.join(root, 'build/gui'), {recursive: true});
const directory = await fs.mkdtemp(path.join(root, 'build/gui/knob-display-offline-'));
const executable = values.prefix ? path.join(path.resolve(values.prefix), 'bin/octa-capture-http') : '/usr/bin/python3';
const server = spawn(executable, [...(values.prefix ? [] : ['-u', 'tools/serve-gui.py']), '--port', '0',
  '--bin-dir', path.join(directory, 'no-device-binaries'), '--data-dir', path.join(directory, 'state')],
{cwd: root, stdio: ['ignore', 'pipe', 'pipe']});
let serverOutput = '', serverErrors = '', browser;
server.stdout.on('data', data => { serverOutput += data; });
server.stderr.on('data', data => { serverErrors += data; });
const actions = [], unexpected = [];
const sensitivity = JSON.parse(await fs.readFile(path.join(root, 'test/data/sensitivity-display.json'), 'utf8'));

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

async function setRate(index) {
  const rate = [44100, 48000, 96000, 192000][index];
  model.state.payload_hex = `0000${index.toString(16).padStart(2, '0')}` + '00'.repeat(1143);
  model.status.sample_rate_hz = rate;
  const raw = Buffer.from(model.status.raw_status_hex, 'hex');
  raw.writeUInt32LE(rate, 3);
  model.status.raw_status_hex = raw.toString('hex');
  await until(() => browser.evaluate(`Number(document.querySelector('#footer-rate').value) === ${rate}`), 'synthetic rate synchronized');
}

const curve = channel => browser.page.locator('.preamp-strip canvas').nth(channel).evaluate(node => node.toDataURL());

async function holdPointer(locator, bottom = false) {
  const bounds = await locator.boundingBox();
  assert.ok(bounds);
  await browser.page.mouse.move(bounds.x + bounds.width / 2, bounds.y + (bottom ? bounds.height - 16 : bounds.height / 2));
  await browser.page.mouse.down();
  assert.equal(await locator.evaluate(node => (node.closest('.knob') || node).hasPointerCapture(1)), true, 'Trusted pointer captured');
}

async function maskedPreamps() {
  const knob = browser.page.locator('.sensitivity').nth(4);
  await holdPointer(knob.locator('.knob-dial'));
  await setRate(3);
  assert.equal(await knob.evaluate(node => node.classList.contains('dragging') || node.hasPointerCapture(1)), false);
  await browser.page.mouse.move(40, 10); await browser.page.mouse.up();
  const strips = browser.page.locator('.preamp-strip.unavailable-channel');
  assert.equal(await strips.count(), 4);
  assert.equal(await strips.evaluateAll(nodes => nodes.every(node => node.inert && node.getAttribute('aria-hidden') === 'true' &&
    getComputedStyle(node).opacity === '1' && [...node.querySelectorAll('.toggle-label')].every(label => getComputedStyle(label).opacity === '1') &&
    [...node.querySelectorAll('input.knob-value')].every(input => input.disabled && input.value === '') &&
    [...node.querySelectorAll('output.knob-value')].every(output => getComputedStyle(output).display === 'none') &&
    [...node.querySelectorAll('.meter-fill')].every(fill => getComputedStyle(fill).visibility === 'hidden'))), true);
  const minimum = await browser.page.locator('.sensitivity .knob-needle').first().evaluate(node => {
    const probe = node.cloneNode(); probe.style.transform = 'rotate(-135deg)'; node.parentElement.append(probe);
    const matrix = getComputedStyle(probe).transform; probe.remove(); return matrix;
  });
  assert.equal(await knob.locator('.knob-needle').evaluate(node => getComputedStyle(node).transform), minimum);
  const masked = await curve(4), active = await curve(0);
  for (const channel of [0, 4]) {
    Object.assign(model.state.preamps[channel].compressor,
      {enabled: true, threshold_index: 10, ratio_index: 8, gate_index: 30, gain_index: 12});
  }
  model.state.preamps[4].sensitivity_index = 50;
  await until(async () => await curve(0) !== active, 'available curve responds to fresh state');
  assert.equal(await curve(4), masked, 'Unavailable grid ignores cached compressor settings');
  await browser.page.locator('.panel').screenshot({path: path.join(directory, 'preamp-192000.png')});
  await setRate(1);
  assert.equal(await knob.locator('input').inputValue(), sensitivity.texts[50]);
  assert.notEqual(await curve(4), masked, 'Available curve restored from cached state');
  assert.equal(await knob.evaluate(node => node.closest('.preamp-strip').inert), false);
  const editor = knob.locator('input');
  await editor.dblclick(); await editor.fill('42.0');
  await setRate(3);
  assert.equal(await editor.evaluate(node => node.disabled && node.readOnly && node.value === ''), true);
  await setRate(1);
  assert.equal(await editor.inputValue(), sensitivity.texts[50], 'Rate change cancels unfinished editing');
}

async function maskedMixers() {
  model.state.mixers[0].input.channels[4].volume = 23;
  const cached = JSON.stringify(model.state.mixers);
  await browser.click('#view-switch');
  const fader = browser.page.locator('.mixer-strip[data-source="input"][data-channel="4"] input[type=range]');
  await until(async () => await fader.inputValue() === '1', 'Nonzero cached fader synchronized');
  await holdPointer(fader, true);
  await setRate(3);
  assert.equal(await fader.evaluate(node => node.hasPointerCapture(1)), false, 'Rate change releases held fader');
  await browser.page.mouse.move(40, 10); await browser.page.mouse.up();
  const strips = browser.page.locator('.mixer-strip.unavailable-channel');
  assert.equal(await strips.count(), 12);
  assert.equal(await strips.evaluateAll(nodes => nodes.every(node => node.inert && getComputedStyle(node).opacity === '1' &&
    node.querySelector('input[type=range]').disabled && node.querySelector('input[type=range]').value === '0' &&
    node.querySelector('.fader-value').textContent === '' && getComputedStyle(node.querySelector('.level-fill')).visibility === 'hidden')), true);
  assert.equal(await browser.page.locator('.mixer-send, .master-return, .master-reverb-type').evaluateAll(
    nodes => nodes.every(node => getComputedStyle(node).visibility === 'hidden')), true);
  await browser.page.locator('.panel').screenshot({path: path.join(directory, 'mixer-192000.png')});
  for (const index of [0, 2, 1]) {
    await setRate(index);
    assert.equal(await browser.page.locator('.unavailable-channel').count(), 0);
    assert.equal(await fader.isEnabled(), true);
    assert.equal(await fader.inputValue(), '1', 'Nonzero cached fader restored');
    assert.equal(await browser.page.locator('.mixer-send, .master-return, .master-reverb-type').evaluateAll(
      nodes => nodes.every(node => getComputedStyle(node).visibility === 'visible')), true);
  }
  await browser.click('#view-switch');
  assert.equal(actions.length, 1, 'Rate transitions and interrupted controls send no device writes');
  assert.equal(model.state.preamps[4].sensitivity_index, 50, 'Cached parameters remain unchanged');
  assert.equal(JSON.stringify(model.state.mixers), cached, 'Masking never changes mixer state');
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
  await maskedPreamps();
  await maskedMixers();
  await browser.page.locator('.panel').screenshot({path: path.join(directory, 'preamp.png')});
  for (const errors of [unexpected, browser.exceptions, browser.securityErrors, browser.networkErrors,
    browser.httpErrors, browser.consoleEvents]) assert.deepEqual(errors, []);
  console.log('PASS Playwright: 101 sensitivity readouts/rotary positions, dB accessibility, commit/Escape, all-rate fixed channel presentation and interrupted knob/fader/edit recovery. All replies synthetic.');
} finally {
  if (browser) await browser.close();
  if (server.exitCode === null) { server.kill('SIGTERM'); await once(server, 'exit'); }
  assert.equal(serverErrors, '', 'No server errors or hardware access attempts');
}
